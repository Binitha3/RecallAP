"""Probe Hindsight Cloud behavior before the production memory adapter is built.

Run from the repository root after filling in `.env`:
    python scripts/smoke_hindsight.py

This creates a uniquely named throwaway bank and never deletes or modifies the
configured application bank.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.config import settings


SEEDS = (
    (
        "vendor_profile",
        "Deccan Freight Lines (V-1004) is a freight vendor. The vendor's billing quirks include fuel surcharges supported by contract amendments; payment terms are Net 30.",
    ),
    (
        "resolution_case",
        'CASE-0001 | 2025-03-12 | Vendor: Deccan Freight Lines (V-1004) | Exception: PRICE_MISMATCH. Invoice INV-2025-0731 billed Rs. 104,000 against PO Rs. 100,000 (+4.0%). Resolution: APPROVED by Priya Nair (AP Manager). Reason given: "Fuel surcharge up to 5% is covered by the contract amendment." Applies to: this vendor, price variance up to 5%. Decided by: human. Outcome: paid.',
    ),
    (
        "approver_preference",
        "Priya Nair (AP Manager) prefers invoices from Deccan Freight Lines to be checked against the written contract amendment and allows a documented freight price variance up to 5%.",
    ),
)


def public_attribute_names(value: Any) -> list[str]:
    """Return response field names without serializing potentially large values."""
    names: set[str] = set()
    model_fields = getattr(type(value), "model_fields", None)
    if isinstance(model_fields, dict):
        names.update(str(name) for name in model_fields)
    instance_dict = getattr(value, "__dict__", None)
    if isinstance(instance_dict, dict):
        names.update(name for name in instance_dict if not name.startswith("_"))
    slots = getattr(type(value), "__slots__", ())
    if isinstance(slots, str):
        slots = (slots,)
    names.update(name for name in slots if isinstance(name, str) and not name.startswith("_"))
    return sorted(names)


def result_items(response: Any) -> Iterable[Any]:
    """Read recalled records from the documented RecallResponse shape."""
    results = getattr(response, "results", None)
    return results if isinstance(results, (list, tuple)) else ()


def print_recalled(label: str, response: Any) -> list[str]:
    """Print the unmodified recalled record text and return it for checks."""
    print(f"\n{label}")
    texts: list[str] = []
    for index, record in enumerate(result_items(response), start=1):
        text = str(getattr(record, "text", ""))
        texts.append(text)
        print(f"[{index}] type={getattr(record, 'type', 'unknown')} id={getattr(record, 'id', 'unknown')}")
        print(text if text else "<record has no text attribute>")
    if not texts:
        print("<no recalled records>")
    return texts


def main() -> int:
    """Retain seed examples, probe recall and reflect, then close the client."""
    if not settings.hindsight_api_key or not settings.hindsight_base_url:
        print("Missing HINDSIGHT_API_KEY or HINDSIGHT_BASE_URL; set them in .env.", file=sys.stderr)
        return 2

    try:
        from hindsight_client import Hindsight
    except ImportError:
        print("hindsight-client is not installed; install requirements.txt first.", file=sys.stderr)
        return 2

    bank_id = f"recallap-smoke-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
    client = Hindsight(
        base_url=settings.hindsight_base_url,
        api_key=settings.hindsight_api_key,
        timeout=60.0,
    )
    print(f"Throwaway bank: {bank_id}")
    print("First retain is sent without create_bank(), to probe implicit bank creation.")

    retain_durations: list[float] = []
    try:
        for context, content in SEEDS:
            started = time.perf_counter()
            response = client.retain(bank_id=bank_id, content=content, context=context)
            elapsed = time.perf_counter() - started
            retain_durations.append(elapsed)
            print(f"retain context={context}: {elapsed:.3f}s; response={type(response).__name__}")

        print(f"Retain timings (seconds): {[round(value, 3) for value in retain_durations]}")
        print(f"Total retain time (seconds): {sum(retain_durations):.3f}")

        query = "CASE-0001 Deccan Freight Lines PRICE_MISMATCH price variance up to 5%"
        try:
            immediate = client.recall(bank_id=bank_id, query=query)
            immediate_texts = print_recalled("Immediate recall (right after retain):", immediate)
            immediate_survives = any(
                "CASE-0001" in text and "up to 5%" in text for text in immediate_texts
            )
            print(f"Immediate recall returned {len(immediate_texts)} records.")
            print(f"CASE-0001 and 'up to 5%' both survive verbatim immediately: {immediate_survives}")
        except Exception as exc:  # print a clean diagnostic, not an SDK traceback
            immediate_texts = []
            immediate_survives = False
            print(f"Immediate recall failed: {type(exc).__name__}: {exc}")

        wait_seconds = 10
        print(f"Waiting {wait_seconds}s before the second recall...")
        time.sleep(wait_seconds)
        delayed = client.recall(bank_id=bank_id, query=query)
        delayed_texts = print_recalled("Recall after wait:", delayed)
        delayed_survives = any(
            "CASE-0001" in text and "up to 5%" in text for text in delayed_texts
        )
        print(f"Delayed recall returned {len(delayed_texts)} records.")
        print(f"CASE-0001 and 'up to 5%' both survive verbatim after wait: {delayed_survives}")

        reflected = client.reflect(
            bank_id=bank_id,
            query="Summarize the Deccan Freight Lines precedent for price mismatches.",
        )
        print("\nReflect response:")
        print(f"Response class: {type(reflected).__module__}.{type(reflected).__name__}")
        print(f"Response attribute names: {json.dumps(public_attribute_names(reflected))}")
        print(f"Response text: {getattr(reflected, 'text', '<no text attribute>')}")

        if hasattr(client, "retain_batch"):
            batch_bank = f"{bank_id}-batch"
            print(f"\nBatch retain method found; probing in separate throwaway bank {batch_bank}.")
            batch_response = client.retain_batch(
                bank_id=batch_bank,
                items=[
                    {"content": content, "context": context}
                    for context, content in SEEDS
                ],
            )
            print(f"Batch retain succeeded; response={type(batch_response).__name__}")
        else:
            print("\nNo retain_batch method is exposed by this installed client.")

        print("\nSmoke test complete. Banks created by this probe are intentionally left for inspection.")
        return 0
    except Exception as exc:
        print(f"Hindsight smoke test failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
