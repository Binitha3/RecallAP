"""Tests for JSON fixture loading and the thread-safe decision snapshot."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.store import InMemoryStore, STUB_DIR


def test_stub_loads_three_invoice_summaries() -> None:
    store = InMemoryStore(data_dir=Path("data-does-not-exist"), stub_dir=STUB_DIR)
    summaries = store.list_summaries()

    assert [row["id"] for row in summaries] == [
        "INV-2025-0731",
        "INV-2025-0732",
        "INV-2025-0801",
    ]
    assert summaries[0]["vendor_name"] == "Deccan Freight Lines"
    assert summaries[2]["amount"] == 32000


def test_load_context_handles_missing_purchase_order() -> None:
    store = InMemoryStore(data_dir=Path("data-does-not-exist"), stub_dir=STUB_DIR)

    invoice, po, grn, vendor = store.load_context("INV-2025-0801")

    assert invoice.po_number == ""
    assert po is None
    assert grn is None
    assert vendor.name == "Godavari IT Services"


def test_case_counter_is_per_bank_and_thread_safe(tmp_path: Path) -> None:
    store = InMemoryStore(
        data_dir=Path("data-does-not-exist"),
        stub_dir=STUB_DIR,
        snapshot_path=tmp_path / "snapshot.json",
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: store.next_case_id("recallap-demo"), range(32)))

    assert len(set(ids)) == 32
    assert set(ids) == {f"CASE-{number:04d}" for number in range(1, 33)}
    assert store.next_case_id("other-bank") == "CASE-0001"


def test_case_counter_sync_keeps_local_counter_above_hindsight_max(tmp_path: Path) -> None:
    store = InMemoryStore(
        data_dir=Path("data-does-not-exist"),
        stub_dir=STUB_DIR,
        snapshot_path=tmp_path / "snapshot.json",
    )
    store.sync_case_counter("bank-a", 5)

    assert store.case_counter_synced("bank-a") is True
    assert store.next_case_id("bank-a") == "CASE-0006"


def test_decisions_survive_snapshot_reload(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    store = InMemoryStore(
        data_dir=Path("data-does-not-exist"), stub_dir=STUB_DIR, snapshot_path=snapshot
    )
    entry = {"invoice_id": "INV-2025-0731", "final_status": "NEEDS_REVIEW"}
    store.save_decision("recallap-demo", entry)

    reloaded = InMemoryStore(
        data_dir=Path("data-does-not-exist"), stub_dir=STUB_DIR, snapshot_path=snapshot
    )
    assert reloaded.decisions("recallap-demo") == [entry]


def test_accounts_are_masked() -> None:
    from backend.models import Vendor

    vendor = Vendor(
        id="V-9",
        name="Example Vendor",
        bank_account="1234 5678 9012 4821",
    )
    assert vendor.bank_account == "XXXX4821"


def test_resolution_reservation_is_idempotent_and_per_bank(tmp_path: Path) -> None:
    store = InMemoryStore(
        data_dir=Path("data-does-not-exist"),
        stub_dir=STUB_DIR,
        snapshot_path=tmp_path / "snapshot.json",
    )
    payload = {"invoice_id": "INV-1", "final_status": "AUTO_RESOLVED"}

    first, created_first = store.reserve_resolution("bank-a", "sig-1", payload)
    repeated, created_repeat = store.reserve_resolution("bank-a", "sig-1", payload)
    other_bank, created_other_bank = store.reserve_resolution("bank-b", "sig-1", payload)

    assert created_first is True
    assert created_repeat is False
    assert repeated["case_id"] == first["case_id"]
    assert created_other_bank is True
    assert other_bank["case_id"] == "CASE-0001"
