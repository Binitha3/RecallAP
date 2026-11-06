"""Thread-safe in-memory store backed by JSON fixtures and snapshots."""

from __future__ import annotations

import json
import os
import threading
import tempfile
import time
from pathlib import Path
from typing import Any

from backend.models import GoodsReceiptNote, Invoice, PurchaseOrder, Vendor

APP_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = APP_ROOT / "data"
STUB_DIR = DATA_DIR / "stub"
SNAPSHOT_PATH = DATA_DIR / "snapshots" / "store_snapshot.json"


def _read_json(path: Path, default: Any) -> Any:
    """Read JSON or return a fresh default if the file is absent."""
    if not path.is_file():
        return default
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


class InMemoryStore:
    """Load invoice context from JSON and persist decisions to a snapshot."""

    def __init__(
        self,
        data_dir: Path = DATA_DIR,
        stub_dir: Path = STUB_DIR,
        snapshot_path: Path = SNAPSHOT_PATH,
    ) -> None:
        self.data_dir = data_dir
        self.stub_dir = stub_dir
        self.snapshot_path = snapshot_path
        self.lock = threading.RLock()
        self._decisions: dict[str, list[dict[str, Any]]] = {}
        self._case_counters: dict[str, int] = {}
        snapshot = _read_json(snapshot_path, {})
        if isinstance(snapshot, dict):
            self._decisions = snapshot.get("decisions", {})
            self._case_counters = snapshot.get("case_counters", {})

    def _source(self, filename: str) -> Path:
        """Prefer teammate-provided data files; fall back to the committed stub."""
        candidate = self.data_dir / filename
        return candidate if candidate.is_file() else self.stub_dir / filename

    def _records(self, filename: str) -> list[dict[str, Any]]:
        """Load a list of fixture records, accepting a keyed JSON object too."""
        value = _read_json(self._source(filename), [])
        if isinstance(value, dict):
            if isinstance(value.get("data"), list):
                value = value["data"]
            else:
                value = list(value.values())
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def all_invoices(self) -> list[Invoice]:
        """Return every invoice in the active dataset."""
        return [Invoice.model_validate(item) for item in self._records("invoices.json")]

    def list_summaries(self, status: str | None = None) -> list[dict[str, Any]]:
        """Build invoice queue rows from the active dataset and vendor records."""
        invoices = [Invoice.model_validate(item) for item in self._records("invoices.json")]
        vendors = {item.id: item for item in (Vendor.model_validate(x) for x in self._records("vendors.json"))}
        with self.lock:
            latest_status = {
                str(decision.get("invoice_id")): str(decision.get("final_status", "NEEDS_REVIEW"))
                for entries in self._decisions.values()
                for decision in entries
            }
        rows = [
            {
                "id": invoice.id,
                "vendor_name": vendors.get(invoice.vendor_id, Vendor(id=invoice.vendor_id, name=invoice.vendor_id)).name,
                "amount": invoice.total,
                "currency": invoice.currency,
                "status": latest_status.get(invoice.id, "NEEDS_REVIEW"),
                "exception_types": [],
            }
            for invoice in invoices
        ]
        return [row for row in rows if status is None or row["status"] == status]

    def load_context(
        self, invoice_id: str
    ) -> tuple[Invoice, PurchaseOrder | None, GoodsReceiptNote | None, Vendor]:
        """Load and validate invoice, PO, GRN, and vendor context."""
        invoice_records = self._records("invoices.json")
        raw_invoice = next((item for item in invoice_records if item.get("id") == invoice_id), None)
        if raw_invoice is None:
            raise KeyError(invoice_id)
        invoice = Invoice.model_validate(raw_invoice)
        raw_po = next(
            (item for item in self._records("purchase_orders.json") if item.get("po_number") == invoice.po_number),
            None,
        )
        po = PurchaseOrder.model_validate(raw_po) if raw_po else None
        raw_grn = next(
            (item for item in self._records("grns.json") if item.get("po_number") == invoice.po_number),
            None,
        )
        grn = GoodsReceiptNote.model_validate(raw_grn) if raw_grn else None
        raw_vendor = next(
            (item for item in self._records("vendors.json") if item.get("id") == invoice.vendor_id),
            None,
        )
        if raw_vendor is None:
            raise KeyError(f"vendor {invoice.vendor_id} for invoice {invoice_id}")
        vendor = Vendor.model_validate(raw_vendor)
        return invoice, po, grn, vendor

    def next_case_id(self, bank_id: str) -> str:
        """Allocate a per-bank, thread-safe case number."""
        with self.lock:
            next_number = self._case_counters.get(bank_id, 0) + 1
            self._case_counters[bank_id] = next_number
            self._write_snapshot_locked()
            return f"CASE-{next_number:04d}"

    def save_decision(self, bank_id: str, decision: dict[str, Any]) -> None:
        """Append a decision and persist a JSON snapshot atomically."""
        with self.lock:
            self._decisions.setdefault(bank_id, []).append(decision)
            self._write_snapshot_locked()

    def decisions(self, bank_id: str | None = None) -> list[dict[str, Any]]:
        """Return a defensive copy of saved decisions."""
        with self.lock:
            if bank_id is not None:
                return list(self._decisions.get(bank_id, []))
            return [entry for entries in self._decisions.values() for entry in entries]

    def _write_snapshot_locked(self) -> None:
        """Write the current in-memory state while the caller holds the lock."""
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"decisions": self._decisions, "case_counters": self._case_counters}
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.snapshot_path.parent,
                prefix=f"{self.snapshot_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_name = stream.name
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            for attempt in range(5):
                try:
                    os.replace(temp_name, self.snapshot_path)
                    temp_name = None
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.02 * (attempt + 1))
        finally:
            if temp_name is not None:
                Path(temp_name).unlink(missing_ok=True)


store = InMemoryStore()
