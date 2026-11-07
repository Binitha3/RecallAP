"""Integration validation for the committed Member 3 fixture set."""

from backend.detectors import run_all
from backend.store import store


def test_all_member3_invoices_resolve_and_validate() -> None:
    invoices = store.all_invoices()
    assert len(invoices) == 180

    for invoice in invoices:
        loaded_invoice, po, grn, vendor = store.load_context(invoice.id)
        assert loaded_invoice.id == invoice.id
        assert vendor.id == invoice.vendor_id
        if invoice.po_number:
            assert po is not None
            assert po.vendor_id == invoice.vendor_id
            assert grn is not None
        else:
            assert po is None
            assert grn is None
            detected = run_all(invoice, po, grn, vendor, invoices)
            assert "MISSING_PO" in {item.type for item in detected}
