"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.detectors import run_all
from backend.models import InvoiceDetailResponse, InvoiceListResponse, InvoiceSummary
from backend.store import store

APP_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = APP_ROOT / "frontend"
FRONTEND_DIR.mkdir(exist_ok=True)

app = FastAPI(title="RecallAP", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    """Report service availability and whether external integrations are configured."""
    return {
        "status": "ok",
        "hindsight_ok": bool(settings.hindsight_api_key and settings.hindsight_base_url),
        "llm_ok": bool(settings.groq_api_key),
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


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
