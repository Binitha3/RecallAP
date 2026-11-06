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
    monkeypatch.setattr(
        agent.store,
        "reserve_resolution",
        lambda bank, key, record: ({**record, "case_id": "CASE-0042"}, True),
    )
    monkeypatch.setattr(
        agent.store,
        "complete_resolution",
        lambda bank, key, retained: {"case_id": "CASE-0042", "retained": retained},
    )
    monkeypatch.setattr(agent.memory, "retain_case", lambda bank_id, text, **kwargs: captured.append({"text": text, **kwargs}) or True)
    monkeypatch.setattr(agent, "_append_decision_log", lambda decision: None)

    result = agent.resolve_invoice(
        "INV-2025-7301",
        "APPROVE",
        "Fuel surcharge is covered; up to 5% is acceptable.",
        "Priya Nair",
        "bank-a",
        reusable_precedent=False,
    )

    assert result == {"case_id": "CASE-0042", "status": "AUTO_RESOLVED", "retained": True}
    assert "Vendor: Deccan Freight Lines (V-1004)" in captured[0]["text"]
    assert "Exception: PRICE_MISMATCH" in captured[0]["text"]
    assert "Applies to: Deccan Freight Lines up to 5%" in captured[0]["text"]
    assert "Decided by: human" in captured[0]["text"]
    assert "Automation eligibility: manual-only" in captured[0]["text"]


def test_repeating_same_human_resolution_does_not_retain_twice(monkeypatch: Any) -> None:
    reservations: dict[str, dict[str, Any]] = {}
    retains: list[str] = []

    def reserve(bank: str, key: str, record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if key in reservations:
            return reservations[key], False
        reservations[key] = {**record, "case_id": "CASE-0043", "retained": False}
        return reservations[key], True

    monkeypatch.setattr(agent.store, "reserve_resolution", reserve)

    def complete(bank: str, key: str, retained: bool) -> dict[str, Any]:
        reservations[key]["retained"] = retained
        return dict(reservations[key])

    monkeypatch.setattr(
        agent.store,
        "complete_resolution",
        complete,
    )
    monkeypatch.setattr(agent.memory, "retain_case", lambda *args, **kwargs: retains.append("retained") or True)
    monkeypatch.setattr(agent, "_append_decision_log", lambda decision: None)

    first = agent.resolve_invoice("INV-2025-7301", "APPROVE", "up to 5%", "Priya", "idempotent-bank")
    second = agent.resolve_invoice("INV-2025-7301", "APPROVE_WITH_NOTE", "up to 5%", "Priya", "idempotent-bank")

    assert first == second == {"case_id": "CASE-0043", "status": "AUTO_RESOLVED", "retained": True}
    assert len(retains) == 1
