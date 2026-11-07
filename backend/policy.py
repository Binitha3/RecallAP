"""Deterministic decision guardrails; model proposals never bypass these checks."""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.models import (
    DecisionAction,
    GuardrailResult,
    InvoiceException,
    ProposedDecision,
    RecalledMemory,
)

CASE_RE = re.compile(r"CASE-\d{4}")
EXCEPTION_RE = re.compile(r"\bException:\s*([A-Z_]+)", re.IGNORECASE)
VENDOR_ID_RE = re.compile(r"\((V-[A-Za-z0-9-]+)\)")
PERCENT_LIMIT_RE = re.compile(r"\bup\s+to\s+(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
AMOUNT_LIMIT_RE = re.compile(
    r"\b(below|under|up\s+to)\s+Rs\.?\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE
)
VARIANCE_RE = re.compile(r"\(([+-]?\d+(?:\.\d+)?)\s*%\)")
HUMAN_RE = re.compile(r"Decided\s+by\s*:\s*human\b", re.IGNORECASE)
MANUAL_ONLY_RE = re.compile(r"Automation\s+eligibility\s*:\s*manual-only\b", re.IGNORECASE)
RESOLUTION_RE = re.compile(
    r"\bResolution:\s*(APPROVED|AUTO_APPROVE(?:_WITH_NOTE)?|REQUEST_VENDOR_INFO|REJECT|ESCALATE)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Precedent:
    """Parsed applicability fields from a single recalled case."""

    memory: RecalledMemory
    case_id: str
    vendor: str
    exception_type: str
    human_decided: bool
    reusable: bool


def _vendor_matches(text: str, vendor: str) -> bool:
    target = vendor.strip().casefold()
    if not target:
        return False
    vendor_id = VENDOR_ID_RE.search(text)
    if target.startswith("v-"):
        return bool(vendor_id and vendor_id.group(1).casefold() == target)
    return target in text.casefold()


def _parse_precedent(memory: RecalledMemory, vendor: str) -> Precedent | None:
    case_id = memory.case_id or ""
    if not case_id:
        match = CASE_RE.search(memory.text)
        case_id = match.group(0) if match else ""
    exception = EXCEPTION_RE.search(memory.text)
    if not case_id or not exception or not _vendor_matches(memory.text, vendor):
        return None
    return Precedent(
        memory=memory,
        case_id=case_id,
        vendor=vendor,
        exception_type=exception.group(1).upper(),
        human_decided=bool(HUMAN_RE.search(memory.text)),
        reusable=not bool(MANUAL_ONLY_RE.search(memory.text)),
    )


def _range_violation(precedent: Precedent, exception: InvoiceException, invoice_total: float) -> bool:
    text = precedent.memory.text
    percent = PERCENT_LIMIT_RE.search(text)
    amount = AMOUNT_LIMIT_RE.search(text)
    if percent:
        if exception.variance_pct is None:
            return False
        return abs(exception.variance_pct) > float(percent.group(1)) + 1e-9
    if amount:
        limit = float(amount.group(2).replace(",", ""))
        # "below" / "under" are strict bounds; "up to" is inclusive.
        strict = amount.group(1).casefold() in {"below", "under"}
        return invoice_total >= limit if strict else invoice_total > limit
    if exception.variance_pct is not None:
        prior_variance = VARIANCE_RE.search(text)
        if prior_variance:
            return abs(exception.variance_pct - float(prior_variance.group(1))) > 1.0 + 1e-9
    return False


def decision_from_precedents(
    exceptions: list[InvoiceException],
    recalled: list[RecalledMemory],
    vendor: str,
    invoice_total: float,
) -> ProposedDecision | None:
    """Apply a unanimous action only when two reusable human cases support each exception."""
    if not exceptions:
        return None
    if any(item.type == "DUPLICATE_INVOICE" for item in exceptions):
        return ProposedDecision(
            action=DecisionAction.REJECT,
            confidence=0.99,
            rationale="The deterministic duplicate detector found an exact prior invoice match.",
            cited_case_ids=[],
        )

    parsed = [_parse_precedent(item, vendor) for item in recalled]
    precedents = [
        item for item in parsed
        if item is not None and item.human_decided and item.reusable
    ]
    actions: list[DecisionAction] = []
    citations: list[str] = []

    for exception in exceptions:
        matching: list[tuple[Precedent, DecisionAction]] = []
        for precedent in precedents:
            if precedent.exception_type != exception.type:
                continue
            resolution = RESOLUTION_RE.search(precedent.memory.text)
            if not resolution or _range_violation(precedent, exception, invoice_total):
                continue
            label = resolution.group(1).upper()
            action = {
                "APPROVED": DecisionAction.AUTO_APPROVE_WITH_NOTE,
                "AUTO_APPROVE": DecisionAction.AUTO_APPROVE,
                "AUTO_APPROVE_WITH_NOTE": DecisionAction.AUTO_APPROVE_WITH_NOTE,
                "REQUEST_VENDOR_INFO": DecisionAction.REQUEST_VENDOR_INFO,
                "REJECT": DecisionAction.REJECT,
            }.get(label)
            if action is not None:
                matching.append((precedent, action))

        if len({item.case_id for item, _ in matching}) < 2:
            return None
        matching_actions = {action for _, action in matching}
        if len(matching_actions) != 1:
            return None
        actions.append(next(iter(matching_actions)))
        for precedent, _ in matching[:2]:
            if precedent.case_id not in citations:
                citations.append(precedent.case_id)

    if not actions or len(set(actions)) != 1:
        return None
    return ProposedDecision(
        action=actions[0],
        confidence=0.95,
        rationale=(
            "Using two or more matching, reusable human resolutions for each exception: "
            + ", ".join(citations)
            + "."
        ),
        cited_case_ids=citations,
    )


def calibrate_confidence(
    decision: ProposedDecision,
    exceptions: list[InvoiceException],
    recalled: list[RecalledMemory],
    vendor: str,
    invoice_total: float,
) -> ProposedDecision:
    """Replace model confidence with a conservative score grounded in cited evidence.

    The score is intentionally deterministic for the demo: no cited precedent caps at
    0.50, one or conflicting precedents caps at 0.75, and at least two unanimous,
    in-range human cases per exception can support 0.90. Deterministic policy
    decisions (including exact duplicate rejection) are produced before this helper.
    """
    if not exceptions or decision.action == DecisionAction.ESCALATE:
        # Keep escalation confidence informative while preventing unsupported model
        # certainty from appearing in the demo.
        if not exceptions:
            return decision

    cited_ids = set(decision.cited_case_ids)
    parsed = [
        item for item in (_parse_precedent(memory, vendor) for memory in recalled)
        if item is not None
        and item.human_decided
        and item.reusable
        and item.case_id in cited_ids
    ]
    if not parsed:
        return decision.model_copy(update={"confidence": min(decision.confidence, 0.50)})

    action_map = {
        "APPROVED": DecisionAction.AUTO_APPROVE_WITH_NOTE,
        "AUTO_APPROVE": DecisionAction.AUTO_APPROVE,
        "AUTO_APPROVE_WITH_NOTE": DecisionAction.AUTO_APPROVE_WITH_NOTE,
        "REQUEST_VENDOR_INFO": DecisionAction.REQUEST_VENDOR_INFO,
        "REJECT": DecisionAction.REJECT,
    }
    counts: list[int] = []
    unanimous = True
    for exception in exceptions:
        matching: list[DecisionAction] = []
        for precedent in parsed:
            if precedent.exception_type != exception.type or _range_violation(precedent, exception, invoice_total):
                continue
            resolution = RESOLUTION_RE.search(precedent.memory.text)
            action = action_map.get(resolution.group(1).upper()) if resolution else None
            if action is not None:
                matching.append(action)
        counts.append(len(matching))
        if not matching or len(set(matching)) != 1 or matching[0] != decision.action:
            unanimous = False

    if not any(counts):
        return decision.model_copy(update={"confidence": min(decision.confidence, 0.50)})
    if min(counts) < 2 or not unanimous:
        return decision.model_copy(update={"confidence": min(decision.confidence, 0.75)})
    return decision.model_copy(update={"confidence": 0.90})


def _add(triggered: list[str], message: str) -> None:
    if message not in triggered:
        triggered.append(message)


def apply_guardrails(
    decision: ProposedDecision,
    exceptions: list[InvoiceException],
    recalled: list[RecalledMemory],
    vendor: str,
    invoice_total: float,
) -> tuple[ProposedDecision, GuardrailResult]:
    """Validate a proposal and downgrade it to ESCALATE whenever a guardrail fires."""
    proposed_action = decision.action
    triggered: list[str] = []

    valid_ids = {memory.case_id for memory in recalled if memory.case_id}
    valid_citations = [case_id for case_id in decision.cited_case_ids if case_id in valid_ids]
    if valid_citations != decision.cited_case_ids:
        decision = decision.model_copy(update={"cited_case_ids": valid_citations})
        _add(triggered, "G6: removed citations not present in recalled memories")

    if any(exception.type == "BANK_DETAILS_CHANGED" for exception in exceptions):
        _add(triggered, "G1: bank details changed")

    if decision.confidence == 0 and "agent unavailable" in decision.rationale.casefold():
        _add(triggered, "G8: agent unavailable; human review")

    parsed = [_parse_precedent(memory, vendor) for memory in recalled]
    parsed = [item for item in parsed if item is not None]
    human_cited = [
        item for item in parsed
        if item.human_decided and item.reusable and item.case_id in valid_citations
    ]

    if proposed_action != DecisionAction.ESCALATE:
        if exceptions and any(exception.type == "DUPLICATE_INVOICE" for exception in exceptions) and proposed_action == DecisionAction.REJECT:
            pass
        else:
            for exception in exceptions:
                matching_count = sum(
                    1 for item in human_cited if item.exception_type == exception.type
                )
                if matching_count < 2:
                    _add(
                        triggered,
                        f"G2: fewer than two cited human precedents for {exception.type}",
                    )

    duplicate_rejection = (
        proposed_action == DecisionAction.REJECT
        and any(exception.type == "DUPLICATE_INVOICE" for exception in exceptions)
    )
    if len({exception.type for exception in exceptions}) > 1 and not duplicate_rejection:
        covered = {
            exception.type
            for exception in exceptions
            if any(
                item.exception_type == exception.type
                for item in parsed
                if item.human_decided and item.reusable
            )
        }
        if covered and len(covered) != len({exception.type for exception in exceptions}):
            _add(triggered, "G7: only some exception types have human precedent")

    for exception in exceptions:
        matching = [item for item in human_cited if item.exception_type == exception.type]
        if matching and any(_range_violation(item, exception, invoice_total) for item in matching):
            _add(triggered, f"G3: {exception.type} exceeds precedent range")

    if invoice_total > 250_000:
        _add(triggered, "G4: invoice total exceeds Rs. 2,50,000")
    if decision.confidence < 0.80 and proposed_action != DecisionAction.ESCALATE:
        _add(triggered, "G5: confidence below 0.80")

    if triggered:
        decision = decision.model_copy(
            update={
                "action": DecisionAction.ESCALATE,
                "rationale": decision.rationale,
            }
        )
    result = GuardrailResult(
        passed=not triggered,
        triggered=triggered,
        downgraded_from=proposed_action if triggered and proposed_action != DecisionAction.ESCALATE else None,
    )
    return decision, result
