"""
RecallAP - run_experiment.py
Member 3 experiment runner, following Section 11.9 of the team playbook.

Run from the RecallAP repository root:
    pip install requests
    python scripts/run_experiment.py

Environment:
    RECALLAP_API_URL  Base URL of the running backend (default: http://127.0.0.1:8000)
    EXPERIMENT_TIMEOUT_SECONDS  Per-request timeout (default: 90)

Expected repository layout:
    data/invoices.json
    data/experiment_results.json       (created by this script)
    scripts/simulate_approver.py       (created earlier)

IMPORTANT API contract note:
Section 11.9 requires clearing the two experiment banks. The playbook documents
POST /api/demo/reset with {"clear_memory": true}, but does not specify how that
endpoint selects a bank. This runner sends ?bank=<bank-id> on reset, process,
and resolve. Member 1 must support the bank query parameter on reset as well
(or adjust reset_bank() to the exact implementation agreed by the team).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SCRIPTS_DIR = ROOT / "scripts"
INVOICES_PATH = DATA_DIR / "invoices.json"
RESULTS_PATH = DATA_DIR / "experiment_results.json"
LOG_PATH = DATA_DIR / "experiment_debug.jsonl"
CSV_PATH = DATA_DIR / "experiment_decisions.csv"

API_BASE_URL = os.getenv("RECALLAP_API_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = int(os.getenv("EXPERIMENT_TIMEOUT_SECONDS", "90"))
ROLLING_WINDOW = 5

# Allow imports whether scripts/ is a Python package or a plain folder.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from simulate_approver import ground_truth, human_decision
except ImportError as exc:
    raise ImportError(
        "Could not import simulate_approver.py. Keep it in the scripts/ folder."
    ) from exc


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(DATA_DIR / "run_experiment.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("recallap.experiment")


class ExperimentError(RuntimeError):
    """Raised when the backend or experiment data is not usable."""


def load_invoices() -> list[dict[str, Any]]:
    if not INVOICES_PATH.exists():
        raise FileNotFoundError(f"Missing invoice dataset: {INVOICES_PATH}")

    with INVOICES_PATH.open("r", encoding="utf-8") as file:
        invoices = json.load(file)

    if not isinstance(invoices, list):
        raise ValueError("data/invoices.json must contain a JSON list.")

    invoices.sort(key=lambda item: int(item.get("seq", 0)))

    if len(invoices) != 60:
        logger.warning("Expected 60 invoices from the playbook; found %d.", len(invoices))

    for index, invoice in enumerate(invoices, start=1):
        if not invoice.get("id"):
            raise ValueError(f"Invoice at position {index} has no 'id' field.")
        if not invoice.get("vendor_id"):
            raise ValueError(f"Invoice {invoice['id']} has no 'vendor_id' field.")

    return invoices


def request_json(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{API_BASE_URL}{path}"

    try:
        response = requests.request(
            method=method,
            url=url,
            params=params,
            json=payload,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        body = ""
        if getattr(exc, "response", None) is not None:
            body = exc.response.text[:1000]
        raise ExperimentError(f"{method} {url} failed: {exc}. Response: {body}") from exc

    if not response.content:
        return {}

    try:
        data = response.json()
    except ValueError as exc:
        raise ExperimentError(
            f"{method} {url} returned non-JSON content: {response.text[:500]}"
        ) from exc

    if not isinstance(data, dict):
        raise ExperimentError(f"{method} {url} should return a JSON object.")
    return data


def reset_bank(bank_id: str) -> None:
    """
    Reset the specified experiment bank.

    The playbook documents clear_memory in the reset body and bank selection
    for experiment isolation. If Member 1 implemented bank selection differently,
    change this one function to match the agreed backend contract.
    """
    result = request_json(
        "POST",
        "/api/demo/reset",
        params={"bank": bank_id},
        payload={"clear_memory": True},
    )
    if result.get("ok") is False:
        raise ExperimentError(f"Reset was not successful for bank {bank_id}: {result}")
    logger.info("Reset requested for bank=%s response=%s", bank_id, result)


def get_exception_list(process_result: dict[str, Any]) -> list[dict[str, Any]]:
    exceptions = process_result.get("exceptions") or []
    if not isinstance(exceptions, list):
        raise ExperimentError("Backend response field 'exceptions' must be a list.")
    return exceptions


def get_final_action(process_result: dict[str, Any]) -> str:
    decision = process_result.get("decision") or {}
    action = decision.get("action")
    if not action:
        # Some backend implementations may return final_action at the top level.
        action = process_result.get("final_action")
    if not action:
        raise ExperimentError(
            "Process response has no decision.action or final_action field. "
            "Check the Section 8.2 response contract with Member 1."
        )
    return str(action).upper()


def action_to_ground_truth_class(action: str) -> str:
    """Translate the agent's action vocabulary into simulator scoring labels."""
    mapping = {
        "AUTO_APPROVE": "APPROVE",
        "AUTO_APPROVE_WITH_NOTE": "APPROVE",
        "APPROVE": "APPROVE",
        "REQUEST_VENDOR_INFO": "REQUEST_VENDOR_INFO",
        "REJECT": "REJECT",
        "ESCALATE": "ESCALATE_HUMAN",
        "ESCALATE_HUMAN": "ESCALATE_HUMAN",
    }
    return mapping.get(action.upper(), action.upper())


def append_jsonl(record: dict[str, Any]) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def resolve_escalated_invoice(
    invoice: dict[str, Any],
    exceptions: list[dict[str, Any]],
    vendor_id: str,
    bank_id: str,
) -> tuple[str, str, str]:
    """Ask the simulator for Priya's resolution, then retain it through /resolve."""
    action, note, ground_truth_class = human_decision(
        invoice, exceptions, vendor_id
    )

    payload = {
        "action": action,
        "note": note,
        "approver": "Priya",
        # A case the simulator labels ESCALATE_HUMAN was approved only after
        # one-off review; retain it for audit, but never generalize it to
        # automatic approval on later invoices.
        "reusable_precedent": ground_truth_class != "ESCALATE_HUMAN",
    }
    response = request_json(
        "POST",
        f"/api/invoices/{invoice['id']}/resolve",
        params={"bank": bank_id},
        payload=payload,
    )
    logger.info(
        "Human resolution invoice=%s action=%s ground_truth=%s response=%s",
        invoice["id"], action, ground_truth_class, response,
    )
    return action, note, ground_truth_class


def process_one_invoice(
    invoice: dict[str, Any],
    mode: str,
    bank_id: str,
) -> dict[str, Any]:
    invoice_id = invoice["id"]
    vendor_id = invoice["vendor_id"]

    result = request_json(
        "POST",
        f"/api/invoices/{invoice_id}/process",
        params={"memory": mode, "bank": bank_id},
    )

    status = str(result.get("final_status") or result.get("status") or "").upper()
    exceptions = get_exception_list(result)
    latency_ms = result.get("latency_ms")

    # CLEAN invoices are recorded and skipped, as specified in Section 11.9.
    if status == "CLEAN":
        return {
            "seq": invoice.get("seq"),
            "invoice_id": invoice_id,
            "vendor": vendor_id,
            "exception": "CLEAN",
            "touchless": True,
            "correct": True,
            "unsafe": False,
            "human_touch": False,
            "status": "CLEAN",
            "latency": latency_ms,
            "agent_action": "CLEAN",
            "ground_truth": "APPROVE",
        }

    if not exceptions:
        raise ExperimentError(
            f"Invoice {invoice_id} is not CLEAN but backend returned no exceptions."
        )

    expected_class, _ = ground_truth(invoice, exceptions, vendor_id)
    action = get_final_action(result)

    # Section 11.9: touchless means final action is not ESCALATE.
    escalated = status == "NEEDS_REVIEW" or action in {"ESCALATE", "ESCALATE_HUMAN"}
    touchless = not escalated

    if touchless:
        actual_class = action_to_ground_truth_class(action)
        correct = actual_class == expected_class
        unsafe = not correct
        human_touch = False
    else:
        correct = False
        unsafe = False
        human_touch = True
        resolve_escalated_invoice(invoice, exceptions, vendor_id, bank_id)

    row = {
        "seq": invoice.get("seq"),
        "invoice_id": invoice_id,
        "vendor": vendor_id,
        "exception": exceptions[0].get("type", "UNKNOWN"),
        "touchless": touchless,
        "correct": correct,
        "unsafe": unsafe,
        "human_touch": human_touch,
        "status": status,
        "latency": latency_ms,
        "agent_action": action,
        "ground_truth": expected_class,
    }
    return row


def build_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build rolling five-invoice correct touchless rate and summary.

    A CLEAN invoice counts as a correct touchless result. An escalated invoice
    is a human touch and does not count as an automatic correct resolution.
    """
    recent_correct: deque[int] = deque(maxlen=ROLLING_WINDOW)
    points: list[dict[str, Any]] = []

    for index, row in enumerate(rows, start=1):
        is_auto_correct = bool(row["touchless"] and row["correct"])
        recent_correct.append(1 if is_auto_correct else 0)
        rolling_rate = sum(recent_correct) / len(recent_correct)

        points.append({
            "n": index,
            "rolling_rate": round(rolling_rate, 4),
            "auto_correct": is_auto_correct,
            "human_touch": bool(row["human_touch"]),
        })

    total = len(rows)
    touchless_correct = sum(
        1 for row in rows if row["touchless"] and row["correct"]
    )
    touchless_decisions = sum(1 for row in rows if row["touchless"])
    unsafe = sum(1 for row in rows if row["unsafe"])
    human_touches = sum(1 for row in rows if row["human_touch"])
    final_rate = (
        touchless_correct / total
        if total else 0.0
    )

    return {
        "points": points,
        "summary": {
            "final_rate": round(final_rate, 4),
            "touchless_correct": touchless_correct,
            "touchless_decisions": touchless_decisions,
            "touchless_precision": round(
                touchless_correct / touchless_decisions if touchless_decisions else 0.0,
                4,
            ),
            "unsafe": unsafe,
            "human_touches": human_touches,
            "invoices": total,
        },
    }


def write_csv(all_rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "mode", "seq", "invoice_id", "vendor", "exception", "touchless",
        "correct", "unsafe", "human_touch", "status", "latency",
        "agent_action", "ground_truth", "elapsed_seconds",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)


def run_mode(
    invoices: list[dict[str, Any]],
    mode: str,
    bank_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    logger.info("========== START memory=%s bank=%s ==========", mode, bank_id)
    rows: list[dict[str, Any]] = []

    for position, invoice in enumerate(invoices, start=1):
        started = time.perf_counter()
        row = process_one_invoice(invoice, mode, bank_id)
        elapsed_seconds = time.perf_counter() - started
        row["mode"] = mode
        row["elapsed_seconds"] = round(elapsed_seconds, 3)
        rows.append(row)

        append_jsonl({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "bank": bank_id,
            **row,
        })

        logger.info(
            "[%s %02d/%02d] invoice=%s vendor=%s exception=%s "
            "status=%s action=%s touchless=%s correct=%s unsafe=%s human=%s "
            "latency_ms=%s elapsed=%.2fs",
            mode.upper(), position, len(invoices), row["invoice_id"],
            row["vendor"], row["exception"], row["status"],
            row["agent_action"], row["touchless"], row["correct"],
            row["unsafe"], row["human_touch"], row["latency"],
            elapsed_seconds,
        )

    metrics = build_metrics(rows)
    logger.info("Summary memory=%s: %s", mode, metrics["summary"])
    return metrics, rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RecallAP memory ON/OFF experiment.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only the first N invoices (use --limit 10 for dry-run).")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    invoices = load_invoices()
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit must be at least 1")
        invoices = invoices[:args.limit]
        logger.info("Dry-run limit enabled: processing %d invoices per mode.", len(invoices))
    logger.info("Loaded %d invoices from %s", len(invoices), INVOICES_PATH)
    logger.info("Backend: %s", API_BASE_URL)

    # Clear previous experiment state before either run. The two banks remain
    # isolated from each other and from the live demo bank.
    reset_bank("exp-on-1")
    reset_bank("exp-off-1")

    # Remove prior run logs so the current experiment is easy to audit.
    for path in (LOG_PATH,):
        if path.exists():
            path.unlink()

    on_metrics, on_rows = run_mode(invoices, "on", "exp-on-1")
    off_metrics, off_rows = run_mode(invoices, "off", "exp-off-1")

    compiled = {
        "on": on_metrics,
        "off": off_metrics,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    with RESULTS_PATH.open("w", encoding="utf-8") as file:
        json.dump(compiled, file, indent=2, ensure_ascii=False)

    write_csv(on_rows + off_rows)

    logger.info("Saved metrics: %s", RESULTS_PATH)
    logger.info("Saved decision CSV: %s", CSV_PATH)
    logger.info("Saved debug JSONL: %s", LOG_PATH)
    logger.info("Final ON summary: %s", on_metrics["summary"])
    logger.info("Final OFF summary: %s", off_metrics["summary"])

    if on_metrics["summary"]["unsafe"] or off_metrics["summary"]["unsafe"]:
        logger.warning(
            "Unsafe touchless decisions detected. Inspect the JSONL log and backend guardrails."
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.error("Experiment interrupted by user.")
        raise SystemExit(130)
    except Exception:
        logger.exception("Experiment failed.")
        raise SystemExit(1)
