"""FastAPI application entry point."""

from pathlib import Path
from typing import Literal
import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx

from backend import memory
from backend.agent import process_invoice, resolve_invoice
from backend.config import get_active_bank_id, set_active_bank_id, settings
from backend.detectors import run_all
from backend.metrics import get_metrics
from backend.models import (
    DemoResetRequest,
    DemoResetResponse,
    HumanResolutionRequest,
    HumanResolutionResponse,
    InvoiceDetailResponse,
    InvoiceListResponse,
    InvoiceSummary,
    DecisionResponse,
    MemorySearchResult,
    MetricsResponse,
    VendorBriefingResponse,
)
from backend.store import store

APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
FRONTEND_DIR.mkdir(exist_ok=True)

app = FastAPI(title="RecallAP", version="0.1.0")
logger = logging.getLogger(__name__)


@app.exception_handler(Exception)
def unhandled_error(_request, exc: Exception) -> JSONResponse:
    """Return a clean JSON error body without exposing an exception traceback."""
    logger.error("Unhandled API error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def _check_hindsight() -> bool:
    """Use Hindsight's documented readiness endpoint to check service reachability."""
    if not settings.hindsight_base_url or not settings.hindsight_api_key:
        return False
    try:
        response = httpx.get(
            f"{settings.hindsight_base_url.rstrip('/')}/health/ready",
            timeout=2.0,
        )
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _check_llm() -> bool:
    """Check Groq key and API reachability with the models-list endpoint."""
    if not settings.groq_api_key:
        return False
    try:
        from backend.llm import _get_client

        _get_client().with_options(timeout=2.0).models.list()
        return True
    except Exception:
        return False


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    """Report API liveness and live reachability of configured integrations."""
    return {
        "status": "ok",
        "hindsight_ok": _check_hindsight(),
        "llm_ok": _check_llm(),
    }


@app.get("/api/invoices", response_model=InvoiceListResponse)
def list_invoices(status: str | None = None) -> InvoiceListResponse:
    """List invoice summaries, optionally filtered by status."""
    rows = store.list_summaries(status)
    known_invoices = store.all_invoices()
    for row in rows:
        invoice, po, grn, vendor = store.load_context(row["id"])
        row["exception_types"] = [
            exception.type for exception in run_all(invoice, po, grn, vendor, known_invoices)
        ]
    return InvoiceListResponse(root=[InvoiceSummary.model_validate(row) for row in rows])


@app.get("/api/invoices/{invoice_id}", response_model=InvoiceDetailResponse)
def get_invoice(invoice_id: str) -> InvoiceDetailResponse:
    """Return invoice context with the matching PO, GRN, vendor, and exceptions."""
    try:
        invoice, po, grn, vendor = store.load_context(invoice_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Invoice or vendor not found") from exc
    return InvoiceDetailResponse(
        invoice=invoice,
        po=po,
        grn=grn,
        vendor=vendor,
        exceptions=run_all(invoice, po, grn, vendor, store.all_invoices()),
    )


@app.post("/api/invoices/{invoice_id}/process", response_model=DecisionResponse)
def process_invoice_route(
    invoice_id: str,
    memory: Literal["on", "off"] = Query("on"),
    bank: str | None = Query(None, min_length=1, max_length=100),
) -> DecisionResponse:
    """Run detectors, memory recall, LLM proposal, and deterministic guardrails."""
    try:
        return process_invoice(invoice_id, memory_enabled=memory == "on", bank_id=bank)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Invoice or vendor not found") from exc


@app.post("/api/invoices/{invoice_id}/resolve", response_model=HumanResolutionResponse)
def resolve_invoice_route(
    invoice_id: str,
    body: HumanResolutionRequest,
    bank: str | None = Query(None, min_length=1, max_length=100),
) -> HumanResolutionResponse:
    """Record a human decision and attempt to retain it in Hindsight."""
    try:
        return HumanResolutionResponse(**resolve_invoice(invoice_id, body.action, body.note, body.approver, bank))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Invoice or vendor not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/memory/search", response_model=list[MemorySearchResult])
def search_memories(
    q: str = Query(..., min_length=1, max_length=500),
    bank: str | None = Query(None, min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=100),
) -> list[MemorySearchResult]:
    """Search a Hindsight bank for retained precedent and profile memories."""
    if not q.strip():
        raise HTTPException(status_code=422, detail="q must not be empty")
    active_bank = bank or get_active_bank_id()
    results = memory.search_memory(active_bank, q, limit)
    return [
        MemorySearchResult(case_id=item.case_id, type=item.memory_type, text=item.text)
        for item in results
    ]


@app.get("/api/vendors/{vendor_id}/briefing", response_model=VendorBriefingResponse)
def vendor_briefing(vendor_id: str) -> VendorBriefingResponse:
    """Use Hindsight reflect to prepare a concise briefing for a known vendor."""
    try:
        vendor = store.load_vendor(vendor_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Vendor not found") from exc
    return VendorBriefingResponse(
        vendor_id=vendor.id,
        briefing_text=memory.reflect_vendor(get_active_bank_id(), vendor.name),
    )


@app.get("/api/metrics", response_model=MetricsResponse)
def metrics(memory: Literal["on", "off"] = Query("on")) -> MetricsResponse:
    """Return the selected memory-on/off experiment results, when available."""
    return get_metrics(memory)


@app.post("/api/demo/reset", response_model=DemoResetResponse)
def reset_demo(body: DemoResetRequest) -> DemoResetResponse:
    """Clear local demo state and rotate to a fresh Hindsight bank when requested."""
    current_bank = get_active_bank_id()
    store.clear_bank(current_bank)
    if body.clear_memory:
        set_active_bank_id(f"{settings.bank_id}-demo-{int(time.time())}-{uuid.uuid4().hex[:6]}")
    return DemoResetResponse(ok=True)


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
