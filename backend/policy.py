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


@dataclass(frozen=True)
class Precedent:
    """Parsed applicability fields from a single recalled case."""

    memory: RecalledMemory
    case_id: str
    vendor: str
    exception_type: str
    human_decided: bool


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
        if item.human_decided and item.case_id in valid_citations
    ]

    if proposed_action != DecisionAction.ESCALATE:
        if exceptions and all(exception.type == "DUPLICATE_INVOICE" for exception in exceptions) and proposed_action == DecisionAction.REJECT:
            pass
        else:
            for exception in exceptions:
                if not any(item.exception_type == exception.type for item in human_cited):
                    _add(triggered, f"G2: no cited human precedent for {exception.type}")

    if len({exception.type for exception in exceptions}) > 1:
        covered = {
            exception.type
            for exception in exceptions
            if any(item.exception_type == exception.type for item in parsed if item.human_decided)
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
