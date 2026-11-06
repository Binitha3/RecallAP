"""Deterministic invoice exception detectors."""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Iterable

from backend.models import (
    ExceptionSeverity,
    GoodsReceiptNote,
    Invoice,
    InvoiceException,
    PurchaseOrder,
    Vendor,
)

DETECTOR_ORDER = (
    "PRICE_MISMATCH",
    "QTY_MISMATCH",
    "MISSING_PO",
    "GSTIN_MISMATCH",
    "TAX_RATE_MISMATCH",
    "CURRENCY_MISMATCH",
    "DUPLICATE_INVOICE",
    "BANK_DETAILS_CHANGED",
)
SEVERITY_RANK = {
    ExceptionSeverity.CRITICAL: 0,
    ExceptionSeverity.HIGH: 1,
    ExceptionSeverity.MEDIUM: 2,
    ExceptionSeverity.LOW: 3,
}

# Demo category tax expectations. Unknown categories are not guessed.
EXPECTED_GST_RATES = {
    "stationery": 18.0,
    "it services": 18.0,
    "facilities": 12.0,
    "logistics": 18.0,
    "packaging": 18.0,
    "components": 18.0,
    "cloud hosting": 18.0,
    "catering": 5.0,
    "raw material": 18.0,
    "printing": 18.0,
    "security": 18.0,
    "chemicals": 18.0,
}


def _exception(
    exception_type: str,
    detail: str,
    severity: ExceptionSeverity,
    variance_pct: float | None = None,
) -> InvoiceException:
    """Create a typed exception result."""
    return InvoiceException(
        type=exception_type,
        detail=detail,
        severity=severity,
        variance_pct=variance_pct,
    )


def detect_price_mismatch(invoice: Invoice, po: PurchaseOrder | None) -> InvoiceException | None:
    """Flag absolute price variance strictly above 0.5%, preserving its sign."""
    if po is None or po.total <= 0:
        return None
    variance = (invoice.total - po.total) / po.total * 100
    if abs(variance) <= 0.5:
        return None
    magnitude = abs(variance)
    severity = (
        ExceptionSeverity.LOW
        if magnitude < 5.0
        else ExceptionSeverity.MEDIUM
        if magnitude < 10.0
        else ExceptionSeverity.HIGH
    )
    direction = "above" if variance > 0 else "below"
    return _exception(
        "PRICE_MISMATCH",
        f"Invoice total is {abs(variance):.1f}% {direction} the PO total.",
        severity,
        variance_pct=round(variance, 4),
    )


def detect_quantity_mismatch(
    invoice: Invoice, grn: GoodsReceiptNote | None
) -> InvoiceException | None:
    """Flag billed line quantities that differ from the received quantities."""
    if grn is None:
        return None
    received = {line.item.strip().casefold(): line.qty for line in grn.received}
    mismatches = [
        f"{line.item}: billed {line.qty:g}, received {received.get(line.item.strip().casefold(), 0):g}"
        for line in invoice.lines
        if not math.isclose(
            line.qty,
            received.get(line.item.strip().casefold(), 0.0),
            rel_tol=0,
            abs_tol=1e-9,
        )
    ]
    if not mismatches:
        return None
    return _exception("QTY_MISMATCH", "Quantity mismatch: " + "; ".join(mismatches) + ".", ExceptionSeverity.MEDIUM)


def detect_missing_po(invoice: Invoice, po: PurchaseOrder | None) -> InvoiceException | None:
    """Flag an empty PO number or a number that does not resolve to a PO."""
    if invoice.po_number.strip() and po is not None:
        return None
    text = "Invoice has no purchase order number." if not invoice.po_number.strip() else "Invoice references an unknown purchase order."
    return _exception("MISSING_PO", text, ExceptionSeverity.MEDIUM)


def detect_gstin_mismatch(invoice: Invoice, vendor: Vendor) -> InvoiceException | None:
    """Flag invoice GSTIN that differs from the vendor master."""
    if invoice.gstin.strip().upper() == vendor.gstin.strip().upper():
        return None
    return _exception(
        "GSTIN_MISMATCH",
        f"Invoice GSTIN does not match vendor master for {vendor.name}.",
        ExceptionSeverity.HIGH,
    )


def detect_tax_rate_mismatch(invoice: Invoice, vendor: Vendor) -> InvoiceException | None:
    """Flag GST rates that differ from the configured expectation for a category."""
    expected = EXPECTED_GST_RATES.get(vendor.category.strip().casefold())
    if expected is None or math.isclose(invoice.gst_rate, expected, rel_tol=0, abs_tol=1e-6):
        return None
    return _exception(
        "TAX_RATE_MISMATCH",
        f"Invoice GST rate is {invoice.gst_rate:g}%; expected {expected:g}% for {vendor.category}.",
        ExceptionSeverity.MEDIUM,
    )


def detect_currency_mismatch(invoice: Invoice, po: PurchaseOrder | None) -> InvoiceException | None:
    """Flag invoice currency that differs from the purchase order currency."""
    if po is None or invoice.currency.strip().upper() == po.currency.strip().upper():
        return None
    return _exception(
        "CURRENCY_MISMATCH",
        f"Invoice currency {invoice.currency} differs from PO currency {po.currency}.",
        ExceptionSeverity.MEDIUM,
    )


def _is_duplicate(invoice: Invoice, other: Invoice) -> bool:
    """Return whether two records meet either deterministic duplicate condition."""
    if invoice.id == other.id or invoice.vendor_id != other.vendor_id:
        return False
    number_a = invoice.invoice_number.strip().casefold()
    number_b = other.invoice_number.strip().casefold()
    if number_a and number_a == number_b:
        return True
    try:
        days_apart = abs((date.fromisoformat(invoice.date) - date.fromisoformat(other.date)).days)
    except ValueError:
        return False
    return (
        Decimal(str(invoice.total)) == Decimal(str(other.total))
        and days_apart <= 7
    )


def detect_duplicate_invoice(
    invoice: Invoice, known_invoices: Iterable[Invoice] = ()
) -> InvoiceException | None:
    """Flag a repeated vendor/invoice number or same amount/date within seven days."""
    duplicate = next((other for other in known_invoices if _is_duplicate(invoice, other)), None)
    if duplicate is None:
        return None
    return _exception(
        "DUPLICATE_INVOICE",
        f"Invoice matches {duplicate.invoice_number} for the same vendor.",
        ExceptionSeverity.HIGH,
    )


def detect_bank_details_changed(invoice: Invoice, vendor: Vendor) -> InvoiceException | None:
    """Flag invoice bank details that differ from the masked vendor master value."""
    invoice_account = invoice.bank_account.strip().casefold()
    vendor_account = vendor.bank_account.strip().casefold()
    if not invoice_account or not vendor_account or invoice_account == vendor_account:
        return None
    return _exception(
        "BANK_DETAILS_CHANGED",
        "Invoice bank details differ from the vendor master; verify with a known contact.",
        ExceptionSeverity.CRITICAL,
    )


def run_all(
    invoice: Invoice,
    po: PurchaseOrder | None,
    grn: GoodsReceiptNote | None,
    vendor: Vendor,
    known_invoices: Iterable[Invoice] = (),
) -> list[InvoiceException]:
    """Run all eight pure detectors and sort by severity, then contract order."""
    checks = (
        detect_price_mismatch(invoice, po),
        detect_quantity_mismatch(invoice, grn),
        detect_missing_po(invoice, po),
        detect_gstin_mismatch(invoice, vendor),
        detect_tax_rate_mismatch(invoice, vendor),
        detect_currency_mismatch(invoice, po),
        detect_duplicate_invoice(invoice, known_invoices),
        detect_bank_details_changed(invoice, vendor),
    )
    priority = {exception_type: index for index, exception_type in enumerate(DETECTOR_ORDER)}
    return sorted(
        (result for result in checks if result is not None),
        key=lambda result: (SEVERITY_RANK[result.severity], priority[result.type]),
    )
