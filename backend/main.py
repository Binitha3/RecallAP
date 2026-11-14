"""FastAPI application entry point."""

from pathlib import Path
from typing import Literal
import csv
import io
import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx

from backend import memory
from backend.agent import process_context, process_invoice, resolve_invoice
from backend.config import get_active_bank_id, new_bank_id, register_bank_id, resolve_bank_id, settings
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
    Invoice,
    SandboxDecisionRequest,
    VendorBriefingResponse,
)
from backend.store import store

APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
FRONTEND_DIR.mkdir(exist_ok=True)

app = FastAPI(title="RecallAP", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
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
        "bank_id": get_active_bank_id(),
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
    response: Response,
    memory_mode: Literal["on", "off"] = Query("on", alias="memory"),
    bank: str | None = Query(None, min_length=1, max_length=100),
) -> DecisionResponse:
    """Run detectors, memory recall, LLM proposal, and deterministic guardrails."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return process_invoice(invoice_id, memory_enabled=memory_mode == "on", bank_id=bank)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Invoice or vendor not found") from exc
    except memory.HindsightCaseCounterError as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not verify bank case IDs; decision was not recorded",
        ) from exc
    except memory.HindsightRecallError as exc:
        raise HTTPException(
            status_code=503,
            detail="Hindsight memory recall failed; check backend logs",
        ) from exc


@app.post("/api/invoices/{invoice_id}/resolve", response_model=HumanResolutionResponse)
def resolve_invoice_route(
    invoice_id: str,
    body: HumanResolutionRequest,
    response: Response,
    bank: str | None = Query(None, min_length=1, max_length=100),
) -> HumanResolutionResponse:
    """Record a human decision and attempt to retain it in Hindsight."""
    try:
        active_bank = resolve_bank_id(bank)
        result = resolve_invoice(
            invoice_id,
            body.action,
            body.note,
            body.approver,
            active_bank,
            reusable_precedent=body.reusable_precedent,
        )
        response.headers["X-RecallAP-Bank-ID"] = active_bank
        response.headers["X-RecallAP-Retained"] = str(result["retained"]).lower()
        logger.info(
            "Human resolution completed: case_id=%s bank_id=%s retained=%s",
            result["case_id"],
            active_bank,
            result["retained"],
        )
        return HumanResolutionResponse(**result)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Invoice or vendor not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except memory.HindsightCaseCounterError as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not verify bank case IDs; approval was not recorded",
        ) from exc


@app.post("/api/sandbox/decision", response_model=DecisionResponse)
def sandbox_decision(body: SandboxDecisionRequest) -> DecisionResponse:
    """Run real detectors, memory, model, and guardrails on an unpersisted clone.

    Sandbox runs never save decisions or retain agent memories. The caller can
    vary invoice amount and bank details without mutating the source invoice.
    """
    try:
        invoice, po, grn, vendor = store.load_context(body.invoice_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Invoice or vendor not found") from exc
    scale = body.invoice_total / invoice.total if invoice.total else 1.0
    payload = invoice.model_dump(mode="python")
    simulation_id = uuid.uuid4().hex[:10].upper()
    payload["id"] = "SANDBOX-" + simulation_id
    payload["invoice_number"] = "SANDBOX-" + simulation_id
    payload["total"] = body.invoice_total
    payload["bank_account"] = "XXXX0000" if body.bank_change else vendor.bank_account
    payload["lines"] = [
        line.model_copy(update={"unit_price": round(line.unit_price * scale, 2)}).model_dump()
        for line in invoice.lines
    ]
    simulated_invoice = Invoice.model_validate(payload)
    try:
        return process_context(
            simulated_invoice,
            po,
            grn,
            vendor,
            store.all_invoices(),
            memory_enabled=body.memory_enabled,
            record_decision=False,
            allow_agent_retain=False,
        )
    except memory.HindsightRecallError as exc:
        raise HTTPException(status_code=503, detail="Hindsight recall failed during sandbox run") from exc
    except memory.HindsightCaseCounterError as exc:
        raise HTTPException(status_code=503, detail="Could not verify Hindsight state for sandbox run") from exc


@app.get("/api/memory/search", response_model=list[MemorySearchResult])
def search_memories(
    response: Response,
    q: str = Query(..., min_length=1, max_length=500),
    bank: str | None = Query(None, min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=100),
) -> list[MemorySearchResult]:
    """Search a Hindsight bank for retained precedent and profile memories."""
    response.headers["Cache-Control"] = "no-store"
    if not q.strip():
        raise HTTPException(status_code=422, detail="q must not be empty")
    active_bank = resolve_bank_id(bank)
    response.headers["X-RecallAP-Bank-ID"] = active_bank
    try:
        results = memory.search_memory(active_bank, q, limit)
    except memory.HindsightSearchError as exc:
        raise HTTPException(status_code=503, detail="Hindsight memory search failed; check backend logs") from exc
    logger.info("Memory search completed: bank_id=%s result_count=%d", active_bank, len(results))
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
def metrics(response: Response, memory: Literal["on", "off"] = Query("on")) -> MetricsResponse:
    """Return the selected memory-on/off experiment results, when available."""
    response.headers["Cache-Control"] = "no-store"
    return get_metrics(memory)


@app.get("/api/audit")
def audit_log(response: Response, limit: int = Query(500, ge=1, le=2000)) -> dict[str, object]:
    """Return recent persisted decisions from the active local demo bank."""
    response.headers["Cache-Control"] = "no-store"
    bank_id = get_active_bank_id()
    records = store.decisions(bank_id)
    return {"bank_id": bank_id, "records": records[-limit:]}


@app.get("/api/audit.csv")
def audit_csv() -> Response:
    """Download an audit-safe CSV for the active demo bank."""
    bank_id = get_active_bank_id()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "recorded_at", "invoice_id", "final_status", "action", "confidence",
        "memory_used", "cited_case_ids", "guardrails_triggered", "case_id",
        "human_approver", "reusable_precedent", "retained", "note",
    ])
    for record in store.decisions(bank_id):
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
        guardrails = record.get("guardrails") if isinstance(record.get("guardrails"), dict) else {}
        values = [
            record.get("recorded_at", ""), record.get("invoice_id", ""),
            record.get("final_status", ""), decision.get("action", record.get("action", "")),
            decision.get("confidence", ""), record.get("memory_used", ""),
            " | ".join(map(str, decision.get("cited_case_ids", []))),
            " | ".join(map(str, guardrails.get("triggered", []))),
            record.get("case_id", ""), record.get("human_approver", ""),
            record.get("reusable_precedent", ""), record.get("retained", ""),
            record.get("note", decision.get("rationale", "")),
        ]
        # Prevent spreadsheet applications from evaluating user-controlled text as a formula.
        safe_values = [
            "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
            for value in values
        ]
        writer.writerow(safe_values)
    return Response(
        output.getvalue(), media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="recallap-audit-{bank_id}.csv"',
        },
    )


@app.get("/api/impact")
def impact_ledger(response: Response) -> dict[str, object]:
    """Summarize observed decisions and clearly labeled review estimates."""
    response.headers["Cache-Control"] = "no-store"
    bank_id = get_active_bank_id()
    latest: dict[str, dict[str, object]] = {}
    for record in store.decisions(bank_id):
        invoice_id = record.get("invoice_id")
        if invoice_id and "memory_used" in record:
            latest[str(invoice_id)] = record

    touchless_statuses = {"AUTO_RESOLVED", "VENDOR_QUERY", "REJECTED", "CLEAN"}
    hands_free = {
        invoice_id for invoice_id, record in latest.items()
        if record.get("final_status") in touchless_statuses
    }
    invoice_amounts = {
        invoice.id: invoice.total
        for invoice in store.all_invoices()
        if invoice.currency.upper() == "INR"
    }
    review_ids = {
        invoice_id for invoice_id, record in latest.items()
        if record.get("final_status") == "NEEDS_REVIEW"
        and any(
            str(item).split(":", 1)[0] in {"G1", "G3", "G4"}
            for item in (record.get("guardrails", {}).get("triggered", [])
                         if isinstance(record.get("guardrails"), dict) else [])
        )
    }
    hours_saved = len(hands_free) * 5 / 60
    return {
        "bank_id": bank_id,
        "invoices_handled": len(latest),
        "hands_free_decisions": len(hands_free),
        "estimated_hours_saved": round(hours_saved, 2),
        "estimate_minutes_per_hands_free_invoice": 5,
        "risk_routed_to_review_inr": round(sum(invoice_amounts.get(i, 0) for i in review_ids), 2),
        "review_estimate_invoice_count": len(review_ids),
        "risk_label": "INR value routed to review; not a payment-blocked amount",
    }


@app.post("/api/demo/reset", response_model=DemoResetResponse)
def reset_demo(
    body: DemoResetRequest,
    bank: str | None = Query(None, min_length=1, max_length=100),
) -> DemoResetResponse:
    """Switch the selected alias to a new empty Hindsight bank when requested.

    The previous physical bank is retained in Hindsight; only its local snapshot
    is cleared. A fresh physical bank keeps old memories out of the new demo.
    """
    current_bank = resolve_bank_id(bank)
    if body.clear_memory:
        fresh_bank = new_bank_id(bank)
        try:
            memory.create_bank(fresh_bank)
        except Exception as exc:
            logger.error("Could not provision Hindsight bank: %s", exc)
            raise HTTPException(status_code=503, detail="Could not create an isolated memory bank") from exc
        store.clear_bank(current_bank)
        register_bank_id(bank, fresh_bank)
        store.sync_case_counter(fresh_bank, 0)
    else:
        store.clear_bank(current_bank)
    return DemoResetResponse(ok=True)


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
