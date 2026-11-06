"""Tests for the invoice pipeline and human resolution flow."""

from typing import Any

from backend import agent
from backend.models import DecisionAction


def test_clean_invoice_short_circuits_memory_and_llm(monkeypatch: Any) -> None:
    invoice, po, grn, vendor = agent.store.load_context("INV-2025-7301")
    invoice = invoice.model_copy(update={"total": 100_000})
    monkeypatch.setattr(agent.memory, "recall_for", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not recall")))
    monkeypatch.setattr(agent.llm, "decide", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call LLM")))
    monkeypatch.setattr(agent, "_save_decision", lambda *args, **kwargs: None)

    result = agent.process_context(invoice, po, grn, vendor, memory_enabled=True)

    assert result.final_status == "CLEAN"
    assert result.decision.action == DecisionAction.AUTO_APPROVE
    assert result.exceptions == []


def test_memory_off_skips_recall_but_keeps_pipeline(monkeypatch: Any) -> None:
    invoice, po, grn, vendor = agent.store.load_context("INV-2025-7301")
    monkeypatch.setattr(agent.memory, "recall_for", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("memory off must skip recall")))
    monkeypatch.setattr(
        agent.llm,
        "decide",
        lambda facts, recalled: agent.ProposedDecision(
            action=DecisionAction.ESCALATE,
            confidence=0.9,
            rationale="No memory was supplied; review this exception.",
        ),
    )
    monkeypatch.setattr(agent.store, "save_decision", lambda *args, **kwargs: None)

    result = agent.process_context(invoice, po, grn, vendor, memory_enabled=False)

    assert result.memory_used is False
    assert result.recalled == []
    assert result.final_status == "NEEDS_REVIEW"


def test_human_resolution_retains_human_tag_and_case_id(monkeypatch: Any) -> None:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(agent.store, "next_case_id", lambda bank_id: "CASE-0042")
    monkeypatch.setattr(agent.store, "save_decision", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent.memory, "retain_case", lambda bank_id, text, **kwargs: captured.append({"text": text, **kwargs}) or True)

    result = agent.resolve_invoice(
        "INV-2025-7301",
        "APPROVE",
        "Fuel surcharge is covered; up to 5% is acceptable.",
        "Priya Nair",
        "bank-a",
    )

    assert result == {"case_id": "CASE-0042", "status": "AUTO_RESOLVED", "retained": True}
    assert "Vendor: Deccan Freight Lines (V-1004)" in captured[0]["text"]
    assert "Exception: PRICE_MISMATCH" in captured[0]["text"]
    assert "Applies to: Deccan Freight Lines up to 5%" in captured[0]["text"]
    assert "Decided by: human" in captured[0]["text"]
