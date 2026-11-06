"""Run the D1-D7 demo story locally with fake memory and LLM responses."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import agent
from backend.models import (
    DecisionAction,
    GoodsReceiptNote,
    Invoice,
    ProposedDecision,
    PurchaseOrder,
    RecalledMemory,
    Vendor,
)
from backend.store import store


def _case(case_id: str, variance: float) -> RecalledMemory:
    text = (
        f"{case_id} | 2025-03-12 | Vendor: Deccan Freight Lines (V-1004) | "
        f"Exception: PRICE_MISMATCH. Invoice was {variance:+.1f}% vs PO. "
        "Resolution approved. Applies to: this vendor, price variance up to 5%. Decided by: human."
    )
    return RecalledMemory(case_id=case_id, memory_type="resolution_case", text=text)


def main() -> None:
    base, base_po, base_grn, deccan = store.load_context("INV-2025-7301")
    godavari_invoice, godavari_po, godavari_grn, godavari = store.load_context("INV-2025-7314")
    human_cases: list[RecalledMemory] = []

    def recall(bank_id: str, vendor_name: str, exc_type: str, detail: str, limit: int = 6) -> list[RecalledMemory]:
        if vendor_name != deccan.name or exc_type != "PRICE_MISMATCH":
            return []
        return list(human_cases[:limit])

    def decide(facts: dict, recalled: list[dict]) -> ProposedDecision:
        exceptions = facts["exceptions"]
        bank_changed = any(item["type"] == "BANK_DETAILS_CHANGED" for item in exceptions)
        variance = abs(float(exceptions[0].get("variance_pct") or 0))
        if bank_changed or not recalled or variance > 5:
            return ProposedDecision(
                action=DecisionAction.ESCALATE,
                confidence=0.95,
                rationale="This invoice requires human review under the available evidence.",
                cited_case_ids=[item["case_id"] for item in recalled],
            )
        confidence = 0.75 if len(recalled) == 1 else 0.91
        return ProposedDecision(
            action=DecisionAction.AUTO_APPROVE_WITH_NOTE,
            confidence=confidence,
            rationale="Matching human cases support this documented variance up to 5%.",
            cited_case_ids=[item["case_id"] for item in recalled],
        )

    def process(label: str, invoice: Invoice, po: PurchaseOrder | None, grn: GoodsReceiptNote | None, vendor: Vendor) -> None:
        response = agent.process_context(
            invoice,
            po,
            grn,
            vendor,
            memory_enabled=True,
            bank_id="recallap-walking-skeleton",
        )
        guards = ", ".join(response.guardrails.triggered) or "none"
        print(f"{label}: {response.final_status} | {response.decision.action.value} | guardrails: {guards}")

    deccan_po = base_po
    assert deccan_po is not None
    with (
        patch.object(agent.memory, "recall_for", side_effect=recall),
        patch.object(agent.llm, "decide", side_effect=decide),
        patch.object(store, "save_decision"),
    ):
        # D1: no precedent, then a human creates CASE-0001 with a 5% range.
        process("D1 +4.0%, no memory", base, deccan_po, base_grn, deccan)
        human_cases.append(_case("CASE-0001", 4.0))

        # D2 and D3 each see exactly one human case, so confidence stays below 0.80.
        d2 = base.model_copy(update={"id": "DEMO-D2", "invoice_number": "DEMO-732", "date": "2025-03-15", "total": 103200, "seq": 20})
        process("D2 +3.2%, one case", d2, deccan_po, base_grn, deccan)
        d3 = base.model_copy(update={"id": "DEMO-D3", "invoice_number": "DEMO-733", "date": "2025-03-20", "total": 104500, "seq": 21})
        process("D3 +4.5%, one case", d3, deccan_po, base_grn, deccan)
        human_cases.append(_case("CASE-0003", 4.5))

        process("D4 missing PO, no memory", godavari_invoice, godavari_po, godavari_grn, godavari)
        d5 = base.model_copy(update={"id": "DEMO-D5", "invoice_number": "DEMO-735", "date": "2025-03-25", "total": 103800, "seq": 22})
        process("D5 +3.8%, two cases", d5, deccan_po, base_grn, deccan)
        d6 = base.model_copy(update={"id": "DEMO-D6", "invoice_number": "DEMO-736", "date": "2025-03-27", "total": 109000, "seq": 23})
        process("D6 +9.0%, outside range", d6, deccan_po, base_grn, deccan)
        d7 = base.model_copy(update={"id": "DEMO-D7", "invoice_number": "DEMO-737", "date": "2025-03-28", "bank_account": "XXXX9999", "seq": 24})
        process("D7 changed bank details", d7, deccan_po, base_grn, deccan)


if __name__ == "__main__":
    main()
