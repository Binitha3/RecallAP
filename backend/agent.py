"""Invoice processing pipeline that combines deterministic checks with guarded LLM proposals."""

from __future__ import annotations

import time
import re
import json
import logging
import threading
from datetime import date
from pathlib import Path
from typing import Iterable

from backend import llm, memory
from backend.config import get_active_bank_id, settings
from backend.detectors import run_all
from backend.models import (
    DecisionAction,
    DecisionResponse,
    ExceptionSeverity,
    GoodsReceiptNote,
    Invoice,
    InvoiceException,
    ProposedDecision,
    PurchaseOrder,
    RecalledMemory,
    Vendor,
    GuardrailResult,
)
from backend.policy import apply_guardrails
from backend.store import store

TOUCHLESS_STATUSES = {"AUTO_RESOLVED", "VENDOR_QUERY", "REJECTED"}
APP_ROOT = Path(__file__).resolve().parents[1]
_decision_log_lock = threading.Lock()
logger = logging.getLogger(__name__)


def _save_decision(bank_id: str, decision: dict) -> None:
    """Persist one decision in the snapshot and append one JSONL record."""
    store.save_decision(bank_id, decision)
    try:
        path = APP_ROOT / "logs" / "decisions.jsonl"
        with _decision_log_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(decision, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        logger.warning("Could not append decision log: %s", exc)


def _status_for(action: DecisionAction) -> str:
    if action in {DecisionAction.AUTO_APPROVE, DecisionAction.AUTO_APPROVE_WITH_NOTE}:
        return "AUTO_RESOLVED"
    if action == DecisionAction.REQUEST_VENDOR_INFO:
        return "VENDOR_QUERY"
    if action == DecisionAction.REJECT:
        return "REJECTED"
    return "NEEDS_REVIEW"


def _memory_type_for(exception: InvoiceException) -> str:
    return "resolution_case" if exception.type else "vendor_profile"


def _case_text(
    case_id: str,
    invoice: Invoice,
    vendor: Vendor,
    exception: InvoiceException,
    decision: ProposedDecision,
    decided_by: str,
) -> str:
    variance = f" ({exception.variance_pct:+.1f}% vs PO)" if exception.variance_pct is not None else ""
    note = decision.rationale.replace("\n", " ").strip()
    action = "APPROVED" if decision.action in {DecisionAction.AUTO_APPROVE, DecisionAction.AUTO_APPROVE_WITH_NOTE} else decision.action.value
    range_text = ""
    range_match = re.search(r"up\s+to\s+\d+(?:\.\d+)?\s*%|below\s+Rs\.?\s*[\d,]+", note, re.IGNORECASE)
    if range_match:
        range_text = f" {range_match.group(0)}"
    return (
        f"{case_id} | {invoice.date or date.today().isoformat()} | Vendor: {vendor.name} ({vendor.id}) | "
        f"Exception: {exception.type}. Invoice {invoice.invoice_number} total Rs. {invoice.total:,.2f}{variance}. "
        f"Resolution: {action}. Reason given: \"{note}\". Applies to: {vendor.name}{range_text}. "
        f"Decided by: {decided_by}. Outcome: {action.casefold()}."
    )


def process_context(
    invoice: Invoice,
    po: PurchaseOrder | None,
    grn: GoodsReceiptNote | None,
    vendor: Vendor,
    known_invoices: Iterable[Invoice] = (),
    *,
    memory_enabled: bool = True,
    bank_id: str | None = None,
) -> DecisionResponse:
    """Process already loaded context; exposed for repeatable demo and unit tests."""
    started = time.perf_counter()
    exceptions = run_all(invoice, po, grn, vendor, known_invoices)
    if not exceptions:
        result = DecisionResponse(
            invoice_id=invoice.id,
            memory_used=False,
            exceptions=[],
            recalled=[],
            decision=ProposedDecision(
                action=DecisionAction.AUTO_APPROVE,
                confidence=1,
                rationale="No invoice exceptions were detected by deterministic checks.",
            ),
            guardrails=GuardrailResult(passed=True),
            final_status="CLEAN",
            latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
        )
        _save_decision(bank_id or get_active_bank_id(), result.model_dump(mode="json"))
        return result

    active_bank = bank_id or get_active_bank_id()
    recalled: list[RecalledMemory] = []
    if memory_enabled:
        first = exceptions[0]
        try:
            recalled = memory.recall_for(active_bank, vendor.name, first.type, first.detail)
        except Exception:
            recalled = []
    facts = {
        "invoice": invoice.model_dump(mode="json"),
        "purchase_order": po.model_dump(mode="json") if po else None,
        "goods_receipt": grn.model_dump(mode="json") if grn else None,
        "vendor": vendor.model_dump(mode="json"),
        "exceptions": [item.model_dump(mode="json") for item in exceptions],
    }
    decision = llm.decide(facts, [item.model_dump(mode="json") for item in recalled])
    decision, guardrails = apply_guardrails(decision, exceptions, recalled, vendor.name, invoice.total)
    final_status = _status_for(decision.action)

    if settings.retain_agent_decisions and final_status in TOUCHLESS_STATUSES and exceptions:
        case_id = store.next_case_id(active_bank)
        text = _case_text(case_id, invoice, vendor, exceptions[0], decision, "agent")
        memory.retain_case(active_bank, text, context=_memory_type_for(exceptions[0]), timestamp=invoice.date)

    result = DecisionResponse(
        invoice_id=invoice.id,
        memory_used=memory_enabled,
        exceptions=exceptions,
        recalled=recalled,
        decision=decision,
        guardrails=guardrails,
        final_status=final_status,
        latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
    )
    _save_decision(active_bank, result.model_dump(mode="json"))
    return result


def process_invoice(invoice_id: str, *, memory_enabled: bool = True, bank_id: str | None = None) -> DecisionResponse:
    """Load an invoice from the active store and process it."""
    invoice, po, grn, vendor = store.load_context(invoice_id)
    return process_context(
        invoice,
        po,
        grn,
        vendor,
        store.all_invoices(),
        memory_enabled=memory_enabled,
        bank_id=bank_id,
    )


def resolve_invoice(invoice_id: str, action: str, note: str, approver: str, bank_id: str | None = None) -> dict[str, str | bool]:
    """Record a human resolution and retain the resulting case to Hindsight."""
    invoice, po, grn, vendor = store.load_context(invoice_id)
    exceptions = run_all(invoice, po, grn, vendor, store.all_invoices())
    if not exceptions:
        raise ValueError("Cannot resolve an invoice without an exception")
    active_bank = bank_id or get_active_bank_id()
    normalized = action.strip().upper()
    action_map = {
        "APPROVE": DecisionAction.AUTO_APPROVE_WITH_NOTE,
        "APPROVED": DecisionAction.AUTO_APPROVE_WITH_NOTE,
        "AUTO_APPROVE": DecisionAction.AUTO_APPROVE,
        "AUTO_APPROVE_WITH_NOTE": DecisionAction.AUTO_APPROVE_WITH_NOTE,
        "REQUEST_VENDOR_INFO": DecisionAction.REQUEST_VENDOR_INFO,
        "REJECT": DecisionAction.REJECT,
        "REJECTED": DecisionAction.REJECT,
        "ESCALATE": DecisionAction.ESCALATE,
    }
    if normalized not in action_map:
        raise ValueError("Unsupported resolution action")
    resolved_action = action_map[normalized]
    decision = ProposedDecision(
        action=resolved_action,
        confidence=1,
        rationale=f"Human decision by {approver.strip()}: {note.strip()}" if note.strip() else f"Human decision by {approver.strip()}.",
    )
    case_id = store.next_case_id(active_bank)
    case_text = _case_text(case_id, invoice, vendor, exceptions[0], decision, "human")
    retained = memory.retain_case(active_bank, case_text, context="resolution_case", timestamp=invoice.date)
    status = _status_for(resolved_action)
    _save_decision(
        active_bank,
        {
            "invoice_id": invoice.id,
            "final_status": status,
            "case_id": case_id,
            "action": resolved_action.value,
            "human_approver": approver.strip(),
            "note": note.strip(),
        },
    )
    return {"case_id": case_id, "status": status, "retained": retained}
