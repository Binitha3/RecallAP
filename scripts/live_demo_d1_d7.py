"""Run the D1–D7 story against the live API, Groq, and Hindsight.

Run from the repository root with the API running, for example:
    $env:RECALLAP_API_URL = "http://127.0.0.1:8765"
    python scripts/live_demo_d1_d7.py --runs 3

Each run rotates an isolated Hindsight bank. D2 receives a human resolution marked
manual-only so D3 still has only one reusable precedent. D3's human resolution
becomes CASE-0003, allowing D5 to test the CASE-0001 + CASE-0003 warm transition.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
API_BASE_URL = os.getenv("RECALLAP_API_URL", "http://127.0.0.1:8765").rstrip("/")
BANK_ALIAS = "recallap-live-demo"


class LiveDemoError(RuntimeError):
    pass


def request_json(method: str, path: str, **kwargs: Any) -> Any:
    response = requests.request(method, API_BASE_URL + path, timeout=90, **kwargs)
    response.raise_for_status()
    return response.json()


def process(invoice_id: str, bank: str) -> dict[str, Any]:
    return request_json(
        "POST",
        f"/api/invoices/{invoice_id}/process",
        params={"memory": "on", "bank": bank},
    )


def human_resolve(invoice_id: str, bank: str, note: str, *, reusable: bool = True) -> dict[str, Any]:
    return request_json(
        "POST",
        f"/api/invoices/{invoice_id}/resolve",
        params={"bank": bank},
        json={
            "action": "APPROVE_WITH_NOTE",
            "note": note,
            "approver": "Priya Nair",
            "reusable_precedent": reusable,
        },
    )


def wait_for_case(case_id: str, bank: str, *, timeout_seconds: float = 20.0) -> dict[str, Any]:
    """Verify metadata and original retained source text are searchable in Hindsight."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        found = request_json(
            "GET",
            "/api/memory/search",
            params={"q": f"{case_id} Deccan Freight PRICE_MISMATCH", "bank": bank, "limit": 20},
        )
        for item in found:
            if item.get("case_id") == case_id:
                text = item.get("text", "")
                if "Decided by: human" not in text or "Applies to:" not in text:
                    raise LiveDemoError(f"{case_id} recalled without its original decision/range fields")
                return item
        time.sleep(1.0)
    raise LiveDemoError(f"Hindsight did not return {case_id} within {timeout_seconds:.0f}s")


def show(label: str, result: dict[str, Any]) -> None:
    decision = result["decision"]
    cited = ",".join(decision.get("cited_case_ids", [])) or "none"
    guardrails = "; ".join(result["guardrails"].get("triggered", [])) or "none"
    print(
        f"  {label}: {result['final_status']} {decision['action']} "
        f"confidence={decision['confidence']:.2f} citations={cited} "
        f"latency={result['latency_ms']}ms guardrails={guardrails}"
    )


def run_once(run_number: int) -> None:
    bank = BANK_ALIAS
    reset = request_json(
        "POST",
        "/api/demo/reset",
        params={"bank": bank},
        json={"clear_memory": True},
    )
    if not reset.get("ok"):
        raise LiveDemoError("Fresh-bank reset failed")
    print(f"Run {run_number}: fresh Hindsight bank created")

    d1 = process("INV-2025-7301", bank)  # Deccan Freight, +4.0%, cold start.
    show("D1 cold +4.0%", d1)
    if d1["final_status"] != "NEEDS_REVIEW":
        raise LiveDemoError("D1 did not escalate on a fresh bank")
    first = human_resolve(
        "INV-2025-7301",
        bank,
        "Fuel surcharge is covered by the contract amendment, up to 5%; reusable human precedent.",
    )
    if first.get("case_id") != "CASE-0001" or not first.get("retained"):
        raise LiveDemoError(f"Expected CASE-0001 retained from D1, got {first}")
    wait_for_case("CASE-0001", bank)

    d2 = process("INV-2025-7315", bank)  # +4.9%, only CASE-0001 is reusable.
    show("D2 warm +4.9%, one reusable case", d2)
    if d2["decision"]["confidence"] >= 0.80 or d2["final_status"] != "NEEDS_REVIEW":
        raise LiveDemoError("D2 must remain below 0.80 and go to human review")
    second = human_resolve(
        "INV-2025-7315",
        bank,
        "Reviewed this invoice only; do not reuse this decision as an automation precedent.",
        reusable=False,
    )
    if second.get("case_id") != "CASE-0002" or not second.get("retained"):
        raise LiveDemoError(f"Expected manual-only CASE-0002 retained from D2, got {second}")

    d3 = process("INV-2025-7327", bank)  # +3.7%, CASE-0002 must not count.
    show("D3 warm +3.7%, manual-only case excluded", d3)
    if d3["decision"]["confidence"] >= 0.80 or d3["final_status"] != "NEEDS_REVIEW":
        raise LiveDemoError("D3 must remain below 0.80 and go to human review")
    third = human_resolve(
        "INV-2025-7327",
        bank,
        "Fuel surcharge is within the approved contract amendment, up to 5%; reusable human precedent.",
    )
    if third.get("case_id") != "CASE-0003" or not third.get("retained"):
        raise LiveDemoError(f"Expected CASE-0003 retained from D3, got {third}")
    wait_for_case("CASE-0003", bank)

    d4 = process("INV-2025-7314", bank)  # Godavari IT, missing PO.
    show("D4 missing PO", d4)
    if d4["final_status"] != "NEEDS_REVIEW":
        raise LiveDemoError("D4 missing-PO case did not go to human review")
    fourth = human_resolve(
        "INV-2025-7314",
        bank,
        "Master contract renewal below Rs. 50,000; reviewed and approved by the AP manager.",
    )
    if not fourth.get("retained"):
        raise LiveDemoError("D4 human resolution was not retained")

    d5: dict[str, Any] | None = None
    for attempt in range(4):
        if attempt:
            time.sleep(1.5)
        d5 = process("INV-2025-7341", bank)  # Deccan Freight, +3.5%.
        expected = {"CASE-0001", "CASE-0003"}
        if d5["final_status"] == "AUTO_RESOLVED" and set(d5["decision"].get("cited_case_ids", [])) >= expected:
            break
    assert d5 is not None
    show("D5 warm +3.5%, two reusable cases", d5)
    if (
        d5["final_status"] != "AUTO_RESOLVED"
        or d5["decision"]["confidence"] <= 0.80
        or not {"CASE-0001", "CASE-0003"}.issubset(d5["decision"].get("cited_case_ids", []))
    ):
        raise LiveDemoError("D5 failed to auto-resolve with CASE-0001 and CASE-0003")

    d6 = process("INV-2025-7357", bank)  # Deccan Freight, +9.0%.
    show("D6 +9.0%, outside 5% range", d6)
    if d6["final_status"] != "NEEDS_REVIEW" or not any(
        "G3: PRICE_MISMATCH exceeds precedent range" in item
        for item in d6["guardrails"].get("triggered", [])
    ):
        raise LiveDemoError("D6 did not visibly trigger the 5% range guardrail")

    d7 = process("INV-2025-7312", bank)  # Changed bank details.
    show("D7 changed bank details", d7)
    if d7["final_status"] != "NEEDS_REVIEW" or not any(
        "G1: bank details changed" in item for item in d7["guardrails"].get("triggered", [])
    ):
        raise LiveDemoError("D7 did not visibly trigger the bank-change guardrail")
    print(f"Run {run_number}: PASS\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3, help="fresh-bank live runs (default: 3)")
    args = parser.parse_args()
    if args.runs < 1 or args.runs > 10:
        parser.error("--runs must be between 1 and 10")
    health = request_json("GET", "/api/health")
    print(f"API health: {health}")
    if not health.get("hindsight_ok") or not health.get("llm_ok"):
        raise LiveDemoError("Both Hindsight and Groq must be green before the live walkthrough")
    for run_number in range(1, args.runs + 1):
        run_once(run_number)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LiveDemoError, requests.RequestException) as exc:
        print(f"Live D1–D7 failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
