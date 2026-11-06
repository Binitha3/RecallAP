"""
RecallAP - Simulated Human Approver (Priya)
Member 3 deliverable.

Run from the RecallAP project root:
    python scripts/simulate_approver.py

The script reads data/approver_rules.json and exposes:
    ground_truth(invoice, exceptions, vendor_id)
    human_decision(invoice, exceptions, vendor_id)

Exception objects are expected to contain:
    {"type": "PRICE_MISMATCH", "variance_pct": 4.0}
"""

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = PROJECT_ROOT / "data" / "approver_rules.json"

# Used when the simulated approver manually reviews an escalation.
HUMAN_CHECK_NOTES = {
    "V-1001": "verified the stationery price against the approved rate card.",
    "V-1002": "checked the renewal with the contract owner.",
    "V-1003": "reviewed the tax discrepancy and requested corrected tax documentation.",
    "V-1004": "checked the freight surcharge against the contract amendment.",
    "V-1005": "checked the goods-received quantity with the warehouse team.",
    "V-1006": "asked the vendor to reissue the invoice in INR.",
    "V-1007": "checked the cloud usage against the approved service terms.",
    "V-1008": "checked the duplicate record and held the payment.",
    "V-1009": "checked the steel price adjustment with the purchase team.",
    "V-1010": "verified the verbal order with the requester.",
    "V-1011": "asked the vendor for an invoice with the correct GSTIN.",
    "V-1012": "requested an independent phone verification of the bank change.",
}


def load_rules(path: Path = RULES_PATH) -> list[dict[str, Any]]:
    """Load the hidden ground-truth rules used only by the simulator."""
    if not path.exists():
        raise FileNotFoundError(
            f"Could not find {path}. Keep approver_rules.json inside the data/ folder."
        )

    with path.open("r", encoding="utf-8") as file:
        rules = json.load(file)

    if not isinstance(rules, list):
        raise ValueError("approver_rules.json must contain a JSON list.")

    required = {"vendor", "exception"}
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict) or not required.issubset(rule):
            raise ValueError(
                f"Rule {index} must be an object containing vendor and exception."
            )
    return rules


def ground_truth(
    inv: dict[str, Any],
    exceptions: list[dict[str, Any]],
    vendor_id: str,
    rules: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """
    Return (class, note) according to the hidden approver rules.

    Class is one of:
    APPROVE, REQUEST_VENDOR_INFO, REJECT, ESCALATE_HUMAN.
    The first detected exception is used, matching the team playbook.
    """
    if not exceptions:
        return "APPROVE", "No exception was detected."

    active_rules = rules if rules is not None else load_rules()
    exception_type = exceptions[0].get("type")
    if not exception_type:
        raise ValueError("The first exception must contain a 'type' field.")

    for rule in active_rules:
        if rule["vendor"] != vendor_id or rule["exception"] != exception_type:
            continue

        if "always" in rule:
            return rule["always"], rule.get("note", "")

        if "approve_if_pct_at_most" in rule:
            variance = exceptions[0].get("variance_pct")
            if variance is not None and float(variance) <= float(rule["approve_if_pct_at_most"]):
                return "APPROVE", rule.get("approve_note", "")

        if "approve_if_amount_below" in rule:
            amount = inv.get("total")
            if amount is not None and float(amount) < float(rule["approve_if_amount_below"]):
                return "APPROVE", rule.get("approve_note", "")

        return rule.get("else", "ESCALATE_HUMAN"), ""

    # Unknown vendor/exception combinations must be sent to a person.
    return "ESCALATE_HUMAN", ""


def human_decision(
    inv: dict[str, Any],
    exceptions: list[dict[str, Any]],
    vendor_id: str,
    rules: list[dict[str, Any]] | None = None,
) -> tuple[str, str, str]:
    """
    Simulate the human resolution.

    Returns (action, note, ground_truth_class). An escalated case is manually
    reviewed and resolved with a documented note, as specified in the playbook.
    """
    decision_class, note = ground_truth(inv, exceptions, vendor_id, rules)

    action_map = {
        "APPROVE": "APPROVE_WITH_NOTE",
        "REQUEST_VENDOR_INFO": "REQUEST_VENDOR_INFO",
        "REJECT": "REJECT",
        "ESCALATE_HUMAN": "APPROVE_WITH_NOTE",
    }

    if decision_class == "ESCALATE_HUMAN":
        manual_note = HUMAN_CHECK_NOTES.get(
            vendor_id, "verified the case manually with the requester."
        )
        note = f"Checked manually: {manual_note}"

    return action_map[decision_class], note, decision_class


def _self_test() -> None:
    """Small rule checks; no backend/API connection is required."""
    rules = load_rules()
    tests = [
        ({"total": 104000}, [{"type": "PRICE_MISMATCH", "variance_pct": 4.0}], "V-1004", "APPROVE"),
        ({"total": 109000}, [{"type": "PRICE_MISMATCH", "variance_pct": 9.0}], "V-1004", "ESCALATE_HUMAN"),
        ({"total": 32000}, [{"type": "MISSING_PO"}], "V-1002", "APPROVE"),
        ({"total": 80000}, [{"type": "MISSING_PO"}], "V-1002", "ESCALATE_HUMAN"),
        ({"total": 50000}, [{"type": "TAX_RATE_MISMATCH"}], "V-1003", "REQUEST_VENDOR_INFO"),
        ({"total": 10000}, [{"type": "BANK_DETAILS_CHANGED"}], "V-1012", "ESCALATE_HUMAN"),
    ]

    for invoice, exceptions, vendor, expected in tests:
        actual, _ = ground_truth(invoice, exceptions, vendor, rules)
        assert actual == expected, (
            f"{vendor}/{exceptions[0]['type']}: expected {expected}, got {actual}"
        )

    action, note, label = human_decision(
        {"total": 109000},
        [{"type": "PRICE_MISMATCH", "variance_pct": 9.0}],
        "V-1004",
        rules,
    )
    assert action == "APPROVE_WITH_NOTE"
    assert label == "ESCALATE_HUMAN"
    assert note.startswith("Checked manually:")

    print(f"Self-test passed: {len(tests)} ground-truth scenarios + manual escalation check.")


if __name__ == "__main__":
    _self_test()
