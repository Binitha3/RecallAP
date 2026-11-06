"""Create an isolated Hindsight bank and seed the RecallAP demo memories.

Run from the repository root after configuring HINDSIGHT_BASE_URL and
HINDSIGHT_API_KEY in .env:
    python scripts/seed_demo_memory.py

The script prints the bank ID to use as BANK_ID for the demo API process and
stores a local manifest under data/snapshots/ so the prepared bank can be
identified without exposing credentials or mixing it with experiment banks.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import settings


SEEDS = (
    {
        "context": "vendor_profile",
        "content": (
            "Deccan Freight Lines (V-1004) is a logistics vendor on Net 30 terms. "
            "It often bills a fuel surcharge of up to 5% above the purchase order, "
            "covered by a contract amendment."
        ),
    },
    {
        "context": "resolution_case",
        "case_id": "CASE-9001",
        "content": (
            'CASE-9001 | 2025-03-12 | Vendor: Deccan Freight Lines (V-1004) | '
            'Exception: PRICE_MISMATCH. Invoice INV-2025-0731 billed Rs. 104,000 '
            'against PO Rs. 100,000 (+4.0%). Resolution: APPROVED by Priya Nair '
            '(AP Manager). Reason given: "Fuel surcharge up to 5% is covered by '
            'the contract amendment." Applies to: this vendor, price variance up '
            'to 5%. Decided by: human. Outcome: paid.'
        ),
    },
    {
        "context": "approver_preference",
        "content": (
            "Priya Nair (AP Manager) never auto-approves invoices above Rs. "
            "2,50,000 and prefers to contact the vendor before rejecting an invoice."
        ),
    },
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bank",
        help="Use this exact bank ID; by default a fresh demo bank is created.",
    )
    args = parser.parse_args()

    if not settings.hindsight_base_url or not settings.hindsight_api_key:
        print("Missing HINDSIGHT_BASE_URL or HINDSIGHT_API_KEY; set them in .env.", file=sys.stderr)
        return 2

    try:
        from hindsight_client import Hindsight
    except ImportError:
        print("hindsight-client is not installed; install requirements.txt first.", file=sys.stderr)
        return 2

    timestamp = datetime.now(timezone.utc)
    bank_id = args.bank or (
        f"{settings.bank_id}-demo-{timestamp:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    )
    client = Hindsight(
        base_url=settings.hindsight_base_url,
        api_key=settings.hindsight_api_key,
        timeout=60.0,
    )
    try:
        client.create_bank(
            bank_id=bank_id,
            name="RecallAP prepared demo",
            background="AP invoice exceptions, human resolutions, and approver preferences.",
        )
        for item in SEEDS:
            options = {key: item[key] for key in ("context", "content")}
            if item.get("case_id"):
                options["metadata"] = {"case_id": item["case_id"]}
            client.retain(bank_id=bank_id, **options)

        manifest_dir = ROOT / "data" / "snapshots"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "demo_bank.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "bank_id": bank_id,
                    "created_at": timestamp.isoformat(),
                    "seeded_contexts": [item["context"] for item in SEEDS],
                    "seeded_case_ids": [
                        item["case_id"] for item in SEEDS if item.get("case_id")
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Prepared Hindsight demo bank: {bank_id}")
        print(f"Seeded memories: {len(SEEDS)}; manifest: {manifest_path}")
        print("Set BANK_ID to this value in .env, then restart the API for the demo.")
        return 0
    except Exception as exc:
        print(f"Could not prepare demo memory bank: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
