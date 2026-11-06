"""Positive, negative, threshold, and ordering tests for the exception rules."""

from backend.detectors import run_all
from backend.models import GoodsReceiptNote, Invoice, PurchaseOrder, Vendor


def make_vendor(**changes: object) -> Vendor:
    values: dict[str, object] = {
        "id": "V-1004",
        "name": "Deccan Freight Lines",
        "gstin": "36ABCDE1234F1Z5",
        "bank_account": "XXXX4821",
        "payment_terms": "Net 30",
        "category": "Logistics",
        "currency": "INR",
    }
    values.update(changes)
    return Vendor.model_validate(values)


def make_po(**changes: object) -> PurchaseOrder:
    values: dict[str, object] = {
        "po_number": "PO-1",
        "vendor_id": "V-1004",
        "total": 100000,
        "currency": "INR",
        "lines": [{"item": "Freight", "qty": 10, "unit_price": 10000}],
    }
    values.update(changes)
    return PurchaseOrder.model_validate(values)


def make_invoice(**changes: object) -> Invoice:
    values: dict[str, object] = {
        "id": "INV-1",
        "vendor_id": "V-1004",
        "po_number": "PO-1",
        "invoice_number": "DFL/1",
        "date": "2025-03-12",
        "currency": "INR",
        "gstin": "36ABCDE1234F1Z5",
        "gst_rate": 18,
        "bank_account": "XXXX4821",
        "lines": [{"item": "Freight", "qty": 10, "unit_price": 10000}],
        "total": 100000,
        "seq": 1,
    }
    values.update(changes)
    return Invoice.model_validate(values)


def make_grn(**changes: object) -> GoodsReceiptNote:
    values: dict[str, object] = {
        "po_number": "PO-1",
        "received": [{"item": "Freight", "qty": 10}],
    }
    values.update(changes)
    return GoodsReceiptNote.model_validate(values)


_DEFAULT_PO = object()


def detected_types(
    invoice: Invoice,
    po: PurchaseOrder | None | object = _DEFAULT_PO,
    grn: GoodsReceiptNote | None = None,
    vendor: Vendor | None = None,
    known: list[Invoice] | None = None,
) -> list[str]:
    return [
        exception.type
        for exception in run_all(
            invoice,
            make_po() if po is _DEFAULT_PO else po,
            grn if grn is not None else make_grn(),
            vendor if vendor is not None else make_vendor(),
            known or [],
        )
    ]


def test_price_mismatch_positive_negative_and_signed_variance() -> None:
    positive = run_all(make_invoice(total=104000), make_po(), make_grn(), make_vendor())
    negative = run_all(make_invoice(total=99400), make_po(), make_grn(), make_vendor())

    assert positive[0].type == "PRICE_MISMATCH"
    assert positive[0].variance_pct == 4.0
    assert negative[0].type == "PRICE_MISMATCH"
    assert negative[0].variance_pct == -0.6


def test_price_mismatch_0_4_percent_is_not_flagged() -> None:
    assert "PRICE_MISMATCH" not in detected_types(make_invoice(total=100400))


def test_price_mismatch_exactly_5_percent_is_flagged() -> None:
    exceptions = run_all(make_invoice(total=105000), make_po(), make_grn(), make_vendor())
    price = next(exception for exception in exceptions if exception.type == "PRICE_MISMATCH")
    assert price.variance_pct == 5.0
    assert price.severity == "medium"


def test_quantity_mismatch_positive_negative() -> None:
    assert "QTY_MISMATCH" in detected_types(
        make_invoice(lines=[{"item": "Freight", "qty": 8, "unit_price": 10000}])
    )
    assert "QTY_MISMATCH" not in detected_types(make_invoice())


def test_missing_po_positive_negative() -> None:
    assert "MISSING_PO" in detected_types(make_invoice(po_number=""), po=None)
    assert "MISSING_PO" in detected_types(make_invoice(po_number="PO-X"), po=None)
    assert "MISSING_PO" not in detected_types(make_invoice(), po=make_po())


def test_gstin_mismatch_positive_negative() -> None:
    assert "GSTIN_MISMATCH" in detected_types(make_invoice(gstin="36ZZZZZ9999Z1Z9"))
    assert "GSTIN_MISMATCH" not in detected_types(make_invoice(gstin="36abcde1234f1z5"))


def test_tax_rate_mismatch_positive_negative() -> None:
    assert "TAX_RATE_MISMATCH" in detected_types(make_invoice(gst_rate=12))
    assert "TAX_RATE_MISMATCH" not in detected_types(make_invoice(gst_rate=18))


def test_currency_mismatch_positive_negative() -> None:
    assert "CURRENCY_MISMATCH" in detected_types(make_invoice(currency="USD"))
    assert "CURRENCY_MISMATCH" not in detected_types(make_invoice(currency="inr"))


def test_duplicate_invoice_positive_negative() -> None:
    other = make_invoice(id="INV-OLD", invoice_number="dfl/1")
    assert "DUPLICATE_INVOICE" in detected_types(make_invoice(), known=[other])
    far_old = make_invoice(id="INV-OLD", invoice_number="DFL/OLD", date="2025-02-01")
    assert "DUPLICATE_INVOICE" not in detected_types(make_invoice(), known=[far_old])


def test_bank_details_changed_positive_negative() -> None:
    assert "BANK_DETAILS_CHANGED" in detected_types(make_invoice(bank_account="XXXX9999"))
    assert "BANK_DETAILS_CHANGED" not in detected_types(make_invoice(bank_account="XXXX4821"))


def test_ordering_is_severity_then_detector_contract_order() -> None:
    invoice = make_invoice(
        total=105000,
        gstin="36WRONG0000X1Z0",
        gst_rate=12,
        bank_account="XXXX9999",
        currency="USD",
        lines=[{"item": "Freight", "qty": 8, "unit_price": 10000}],
    )
    duplicate = make_invoice(id="INV-OTHER", invoice_number="DFL/1")
    exceptions = run_all(invoice, make_po(), make_grn(), make_vendor(), [duplicate])

    assert [exception.type for exception in exceptions] == [
        "BANK_DETAILS_CHANGED",
        "GSTIN_MISMATCH",
        "DUPLICATE_INVOICE",
        "PRICE_MISMATCH",
        "QTY_MISMATCH",
        "TAX_RATE_MISMATCH",
        "CURRENCY_MISMATCH",
    ]
