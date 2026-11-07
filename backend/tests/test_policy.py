"""Table-driven coverage for the deterministic policy guardrails."""

import pytest

from backend.models import (
    DecisionAction,
    ExceptionSeverity,
    InvoiceException,
    ProposedDecision,
    RecalledMemory,
)
from backend.policy import (
    apply_guardrails,
    calibrate_confidence,
    decision_from_precedents,
    strip_unrecalled_citations,
)


VENDOR = "Deccan Freight Lines (V-1004)"


def exception(kind: str = "PRICE_MISMATCH", variance: float | None = 4.9) -> InvoiceException:
    return InvoiceException(
        type=kind,
        detail="invoice is above PO",
        severity=ExceptionSeverity.LOW,
        variance_pct=variance,
    )


def memory(
    case_id: str = "CASE-0001",
    kind: str = "PRICE_MISMATCH",
    vendor: str = VENDOR,
    body: str = "Applies to: this vendor, price variance up to 5%.",
    decided: str = "human",
) -> RecalledMemory:
    return RecalledMemory(
        case_id=case_id,
        memory_type="resolution_case",
        text=f"{case_id} | Vendor: {vendor} | Exception: {kind}. {body} Decided by: {decided}.",
    )


def proposal(
    action: DecisionAction = DecisionAction.AUTO_APPROVE_WITH_NOTE,
    confidence: float = 0.9,
    citations: list[str] | None = None,
) -> ProposedDecision:
    return ProposedDecision(
        action=action,
        confidence=confidence,
        rationale="The matching human decision supports the action for this invoice.",
        cited_case_ids=citations or [],
    )


@pytest.mark.parametrize(
    ("exceptions", "recalled", "citations", "total", "confidence", "expected", "trigger"),
    [
        ([exception("BANK_DETAILS_CHANGED", None)], [memory()], ["CASE-0001"], 10_000, 0.99, "G1", "BANK_DETAILS_CHANGED"),
        ([exception()], [], [], 10_000, 0.95, "G2", "fewer than two cited human precedents"),
        ([exception(variance=5.1)], [memory()], ["CASE-0001"], 10_000, 0.95, "G3", "exceeds precedent range"),
        ([exception(variance=4.9)], [memory(), memory(case_id="CASE-0002")], ["CASE-0001", "CASE-0002"], 10_000, 0.95, None, None),
        ([exception(variance=5.1)], [memory()], ["CASE-0001"], 10_000, 0.95, "G3", "exceeds precedent range"),
        ([exception("MISSING_PO", None)], [memory(kind="MISSING_PO", vendor="Godavari IT (V-2001)", body="Applies to invoices below Rs. 50,000."), memory(case_id="CASE-0002", kind="MISSING_PO", vendor="Godavari IT (V-2001)", body="Applies to invoices below Rs. 50,000.")], ["CASE-0001", "CASE-0002"], 32_000, 0.91, None, None),
        ([exception("MISSING_PO", None)], [memory(kind="MISSING_PO", vendor="Godavari IT (V-2001)", body="Applies to invoices below Rs. 50,000.")], ["CASE-0001"], 80_000, 0.91, "G3", "exceeds precedent range"),
        ([exception()], [memory(decided="agent")], ["CASE-0001"], 10_000, 0.95, "G2", "fewer than two cited human precedents"),
        ([exception()], [memory()], ["CASE-9999"], 10_000, 0.95, "G6", "removed citations"),
        ([exception()], [memory()], ["CASE-0001"], 10_000, 0.79, "G5", "confidence below 0.80"),
        ([exception(), exception("MISSING_PO", None)], [memory()], ["CASE-0001"], 10_000, 0.95, "G7", "only some exception types"),
        ([exception(), exception("MISSING_PO", None)], [memory(), memory(case_id="CASE-0002", kind="MISSING_PO")], ["CASE-0001", "CASE-0002"], 10_000, 0.95, "G2", "fewer than two cited human precedents"),
        ([exception("DUPLICATE_INVOICE", None)], [], [], 10_000, 0.95, None, None),
        ([exception("DUPLICATE_INVOICE", None), exception("TAX_RATE_MISMATCH", None)], [], [], 10_000, 0.95, None, None),
        ([exception("DUPLICATE_INVOICE", None), exception("BANK_DETAILS_CHANGED", None)], [], [], 10_000, 0.95, "G1", "bank details changed"),
    ],
)
def test_guardrail_table(
    exceptions: list[InvoiceException],
    recalled: list[RecalledMemory],
    citations: list[str],
    total: float,
    confidence: float,
    expected: str | None,
    trigger: str | None,
) -> None:
    current_vendor = "Godavari IT (V-2001)" if exceptions and exceptions[0].type == "MISSING_PO" else VENDOR
    action = DecisionAction.REJECT if exceptions and exceptions[0].type == "DUPLICATE_INVOICE" else DecisionAction.AUTO_APPROVE_WITH_NOTE
    final, result = apply_guardrails(proposal(action, confidence, citations), exceptions, recalled, current_vendor, total)

    if expected is None:
        assert result.passed is True
        assert final.action == action
    else:
        assert result.passed is False
        assert final.action == DecisionAction.ESCALATE
        assert any(expected in entry for entry in result.triggered)
        if trigger:
            assert any(trigger in entry for entry in result.triggered)


def test_amount_range_supports_indian_comma_grouping() -> None:
    precedent = memory(kind="MISSING_PO", vendor="Godavari IT (V-2001)", body="Applies to invoices below Rs. 2,50,000.")
    final, result = apply_guardrails(
        proposal(citations=["CASE-0001"]),
        [exception("MISSING_PO", None)],
        [precedent],
        "Godavari IT (V-2001)",
        250_000,
    )
    assert final.action == DecisionAction.ESCALATE
    assert any("G3" in entry for entry in result.triggered)


def test_g4_absolute_invoice_limit() -> None:
    final, result = apply_guardrails(
        proposal(citations=["CASE-0001"]),
        [exception()],
        [memory()],
        VENDOR,
        250_001,
    )
    assert final.action == DecisionAction.ESCALATE
    assert any("G4" in entry for entry in result.triggered)


def test_manual_only_approval_is_not_reusable_as_precedent() -> None:
    one_off = memory(case_id="CASE-0001")
    one_off = one_off.model_copy(
        update={"text": one_off.text + " Automation eligibility: manual-only."}
    )
    final, result = apply_guardrails(
        proposal(citations=["CASE-0001", "CASE-0002"]),
        [exception()],
        [one_off, memory(case_id="CASE-0002")],
        VENDOR,
        10_000,
    )
    assert final.action == DecisionAction.ESCALATE
    assert any("fewer than two cited human precedents" in entry for entry in result.triggered)


def test_reusable_precedents_can_resolve_without_a_model_call() -> None:
    memories = [
        RecalledMemory(
            case_id=f"CASE-{number:04d}",
            memory_type="resolution_case",
            text=(
                f"CASE-{number:04d} | Vendor: {VENDOR} | Exception: PRICE_MISMATCH. "
                "Resolution: APPROVED. Applies to: price variance up to 5%. "
                "Decided by: human."
            ),
        )
        for number in (1, 2)
    ]
    decision = decision_from_precedents(
        [exception(variance=4.9)], memories, VENDOR, 10_000
    )
    assert decision is not None
    assert decision.action == DecisionAction.AUTO_APPROVE_WITH_NOTE
    assert decision.cited_case_ids == ["CASE-0001", "CASE-0002"]


def test_confidence_is_calibrated_from_cited_precedents_not_model_claim() -> None:
    current = exception(variance=4.0)
    first = memory(body="Resolution: APPROVED. Applies to: price variance up to 5%.")
    second = memory(case_id="CASE-0002", body="Resolution: APPROVED. Applies to: price variance up to 5%.")
    raw = proposal(confidence=0.73, citations=["CASE-0001", "CASE-0002"])

    cold = calibrate_confidence(raw.model_copy(update={"cited_case_ids": []}), [current], [], VENDOR, 10_000)
    warm_one = calibrate_confidence(raw, [current], [first], VENDOR, 10_000)
    warm_two = calibrate_confidence(raw, [current], [first, second], VENDOR, 10_000)

    assert cold.confidence == 0.50
    assert warm_one.confidence == 0.75
    assert warm_two.confidence == 0.90


def test_conflicting_precedents_cannot_raise_confidence() -> None:
    first = memory(body="Resolution: APPROVED. Applies to: price variance up to 5%.")
    second = memory(case_id="CASE-0002", body="Resolution: REJECT. Applies to: price variance up to 5%.")
    raw = proposal(confidence=0.99, citations=["CASE-0001", "CASE-0002"])

    calibrated = calibrate_confidence(raw, [exception(variance=4.0)], [first, second], VENDOR, 10_000)

    assert calibrated.confidence == 0.75


def test_hallucinated_citations_are_removed_before_confidence_calibration() -> None:
    recalled = [memory()]
    raw = proposal(confidence=0.99, citations=["CASE-9999"])

    sanitized = strip_unrecalled_citations(raw, recalled)
    calibrated = calibrate_confidence(
        sanitized, [exception(variance=4.0)], recalled, VENDOR, 10_000
    )

    assert sanitized.cited_case_ids == []
    assert calibrated.confidence == 0.50


def test_agent_decided_cases_do_not_count_toward_confidence_or_auto_resolution() -> None:
    human_case = memory(body="Resolution: APPROVED. Applies to: price variance up to 5%.")
    agent_case = memory(
        case_id="CASE-0002",
        body="Resolution: APPROVED. Applies to: price variance up to 5%.",
        decided="agent",
    )
    raw = proposal(confidence=0.99, citations=["CASE-0001", "CASE-0002"])

    calibrated = calibrate_confidence(
        raw, [exception(variance=4.0)], [human_case, agent_case], VENDOR, 10_000
    )
    deterministic = decision_from_precedents(
        [exception(variance=4.0)], [human_case, agent_case], VENDOR, 10_000
    )

    assert calibrated.confidence == 0.75
    assert deterministic is None


def test_out_of_range_human_precedent_triggers_guardrail_without_model_citation() -> None:
    final, result = apply_guardrails(
        proposal(citations=[]),
        [exception(variance=9.0)],
        [memory(body="Resolution: APPROVED. Applies to: price variance up to 5%.")],
        VENDOR,
        10_000,
    )

    assert final.action == DecisionAction.ESCALATE
    assert any("G3: PRICE_MISMATCH exceeds precedent range" in item for item in result.triggered)


def test_duplicate_invoice_can_be_rejected_from_deterministic_detection() -> None:
    decision = decision_from_precedents(
        [exception("DUPLICATE_INVOICE", None), exception("TAX_RATE_MISMATCH", None)],
        [],
        VENDOR,
        10_000,
    )
    assert decision is not None
    assert decision.action == DecisionAction.REJECT


def test_g8_unavailable_model_is_explicit() -> None:
    unavailable = ProposedDecision(
        action=DecisionAction.ESCALATE,
        confidence=0,
        rationale="Agent unavailable; routed to human review.",
    )
    final, result = apply_guardrails(unavailable, [exception()], [], VENDOR, 10_000)
    assert final.action == DecisionAction.ESCALATE
    assert any("G8" in entry for entry in result.triggered)
