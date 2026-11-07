"""Run the D1–D7 story against the live API, Groq, and Hindsight.

Run from the repository root with the API running, for example:
    $env:RECALLAP_API_URL = "http://127.0.0.1:8000"
    python scripts/live_demo_d1_d7.py --runs 3

Each run rotates an isolated Hindsight bank. D2 receives a human resolution marked
manual-only so D3 still has only one reusable precedent. D3's human resolution
becomes CASE-0003, allowing D5 to test the CASE-0001 + CASE-0003 warm transition.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
API_BASE_URL = os.getenv("RECALLAP_API_URL", "http://127.0.0.1:8000").rstrip("/")
BANK_ALIAS = "recallap-live-demo"
STORY_PATH = ROOT / "data" / "demo_story.json"


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


def variance_label(step: str, story: dict[str, dict[str, Any]], result: dict[str, Any]) -> str:
    target = story[step].get("variance_pct")
    actual = next(
        (item.get("variance_pct") for item in result.get("exceptions", []) if item.get("variance_pct") is not None),
        None,
    )
    if target is None or actual is None:
        return step
    return f"{step} story {float(target):+.1f}% -> fixture {float(actual):+.1f}%"


def story_invoice_map() -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Resolve each story step to the closest unused invoice in the shared dataset."""
    from backend.detectors import run_all
    from backend.store import store

    story = json.loads(STORY_PATH.read_text(encoding="utf-8"))
    steps = {item["step"]: item["invoice"] for item in story}
    required = {f"D{number}" for number in range(1, 8)}
    if set(steps) != required:
        raise LiveDemoError(f"{STORY_PATH} must define exactly D1 through D7")

    known = store.all_invoices()
    available: list[dict[str, Any]] = []
    for summary in store.list_summaries():
        invoice, po, grn, vendor = store.load_context(summary["id"])
        exceptions = run_all(invoice, po, grn, vendor, known)
        available.append(
            {
                "id": invoice.id,
                "vendor_id": vendor.id,
                "total": invoice.total,
                "seq": invoice.seq,
                "exceptions": exceptions,
            }
        )

    selected: dict[str, str] = {}
    used: set[str] = set()
    for step in (f"D{number}" for number in range(1, 8)):
        expected = steps[step]
        target_vendor = expected.get("vendor_id")
        exception_type = expected.get("exception")
        target_variance = expected.get("variance_pct")
        target_total = expected.get("total")
        matches = [
            candidate for candidate in available
            if candidate["id"] not in used
            and any(exc.type == exception_type for exc in candidate["exceptions"])
        ]
        same_vendor = [item for item in matches if item["vendor_id"] == target_vendor]
        # The dataset has a changed-bank invoice under V-1012 while the story names
        # V-1004; preserve the exception shape when that exact vendor case is absent.
        if same_vendor:
            matches = same_vendor
        if not matches:
            raise LiveDemoError(f"No unused dataset invoice matches {step}/{exception_type}")

        def score(candidate: dict[str, Any]) -> tuple[float, float, int, str]:
            exception = next(exc for exc in candidate["exceptions"] if exc.type == exception_type)
            variance_delta = (
                abs(float(exception.variance_pct) - float(target_variance))
                if target_variance is not None and exception.variance_pct is not None
                else 0.0
            )
            amount_delta = abs(candidate["total"] - float(target_total)) if target_total is not None else 0.0
            return (variance_delta, amount_delta, candidate["seq"], candidate["id"])

        choice = min(matches, key=score)
        selected[step] = choice["id"]
        used.add(choice["id"])

    # The precedents that warm D5 must come from the same vendor as the warm target.
    for step in ("D1", "D2", "D3", "D5", "D6"):
        if steps[step].get("vendor_id") != steps["D1"].get("vendor_id"):
            raise LiveDemoError(f"{step} must use D1's vendor for the precedent/range story")
    return selected, steps


def run_once(
    run_number: int,
    invoice_ids: dict[str, str],
    story: dict[str, dict[str, Any]],
) -> None:
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
    print("  Story mapping:", ", ".join(f"{key}={value}" for key, value in invoice_ids.items()))

    d1 = process(invoice_ids["D1"], bank)
    show(variance_label("D1", story, d1) + " cold", d1)
    if d1["final_status"] != "NEEDS_REVIEW":
        raise LiveDemoError("D1 did not escalate on a fresh bank")
    first = human_resolve(
        invoice_ids["D1"],
        bank,
        "Fuel surcharge is covered by the contract amendment, up to 5%; reusable human precedent.",
    )
    if first.get("case_id") != "CASE-0001" or not first.get("retained"):
        raise LiveDemoError(f"Expected CASE-0001 retained from D1, got {first}")
    wait_for_case("CASE-0001", bank)

    d2 = process(invoice_ids["D2"], bank)
    show(variance_label("D2", story, d2) + ", one reusable case", d2)
    if d2["decision"]["confidence"] >= 0.80 or d2["final_status"] != "NEEDS_REVIEW":
        raise LiveDemoError("D2 must remain below 0.80 and go to human review")
    second = human_resolve(
        invoice_ids["D2"],
        bank,
        "Reviewed this invoice only; do not reuse this decision as an automation precedent.",
        reusable=False,
    )
    if second.get("case_id") != "CASE-0002" or not second.get("retained"):
        raise LiveDemoError(f"Expected manual-only CASE-0002 retained from D2, got {second}")

    d3 = process(invoice_ids["D3"], bank)
    show(variance_label("D3", story, d3) + ", manual-only case excluded", d3)
    if d3["decision"]["confidence"] >= 0.80 or d3["final_status"] != "NEEDS_REVIEW":
        raise LiveDemoError("D3 must remain below 0.80 and go to human review")
    third = human_resolve(
        invoice_ids["D3"],
        bank,
        "Fuel surcharge is within the approved contract amendment, up to 5%; reusable human precedent.",
    )
    if third.get("case_id") != "CASE-0003" or not third.get("retained"):
        raise LiveDemoError(f"Expected CASE-0003 retained from D3, got {third}")
    wait_for_case("CASE-0003", bank)

    d4 = process(invoice_ids["D4"], bank)
    show("D4 missing PO", d4)
    if d4["final_status"] != "NEEDS_REVIEW":
        raise LiveDemoError("D4 missing-PO case did not go to human review")
    fourth = human_resolve(
        invoice_ids["D4"],
        bank,
        "Master contract renewal below Rs. 50,000; reviewed and approved by the AP manager.",
    )
    if not fourth.get("retained"):
        raise LiveDemoError("D4 human resolution was not retained")

    d5: dict[str, Any] | None = None
    for attempt in range(4):
        if attempt:
            time.sleep(1.5)
        d5 = process(invoice_ids["D5"], bank)
        expected = {"CASE-0001", "CASE-0003"}
        if d5["final_status"] == "AUTO_RESOLVED" and set(d5["decision"].get("cited_case_ids", [])) >= expected:
            break
    assert d5 is not None
    show(variance_label("D5", story, d5) + ", two reusable cases", d5)
    if (
        d5["final_status"] != "AUTO_RESOLVED"
        or d5["decision"]["confidence"] <= 0.80
        or not {"CASE-0001", "CASE-0003"}.issubset(d5["decision"].get("cited_case_ids", []))
    ):
        raise LiveDemoError("D5 failed to auto-resolve with CASE-0001 and CASE-0003")

    d6 = process(invoice_ids["D6"], bank)
    show(variance_label("D6", story, d6) + ", outside range", d6)
    if d6["final_status"] != "NEEDS_REVIEW" or not any(
        "G3: PRICE_MISMATCH exceeds precedent range" in item
        for item in d6["guardrails"].get("triggered", [])
    ):
        raise LiveDemoError("D6 did not visibly trigger the 5% range guardrail")

    d7 = process(invoice_ids["D7"], bank)
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
    invoice_ids, story = story_invoice_map()
    for run_number in range(1, args.runs + 1):
        run_once(run_number, invoice_ids, story)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LiveDemoError, requests.RequestException) as exc:
        print(f"Live D1–D7 failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
