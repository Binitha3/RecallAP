"""Scaffold smoke checks that do not need external services."""

from fastapi import HTTPException

from backend.main import FRONTEND_DIR, app, get_invoice, health, list_invoices


def test_health_reports_service_and_configuration() -> None:
    """The health response is available before integrations are configured."""
    result = health()
    assert result["status"] == "ok"
    assert isinstance(result["hindsight_ok"], bool)
    assert isinstance(result["llm_ok"], bool)


def test_openapi_and_static_mount_are_registered() -> None:
    """The app builds its API schema and mounts the frontend at the root."""
    schema = app.openapi()
    assert "/api/health" in schema["paths"]
    assert any(getattr(route, "name", "") == "frontend" for route in app.routes)
    assert FRONTEND_DIR.is_dir()


def test_invoice_routes_return_contract_shapes() -> None:
    """The invoice list and detail functions expose the frozen response models."""
    rows = list_invoices().root
    detail = get_invoice("INV-2025-0731")

    assert len(rows) == 3
    assert rows[0].vendor_name == "Deccan Freight Lines"
    assert detail.invoice.total == 104000
    assert detail.po is not None and detail.po.total == 100000
    assert detail.vendor.bank_account == "XXXX4821"


def test_invoice_route_filters_and_returns_clean_not_found() -> None:
    """Status filters work and unknown invoice identifiers map to HTTP 404."""
    assert list_invoices(status="CLEAN").root == []
    try:
        get_invoice("no-such-invoice")
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Invoice or vendor not found"
    else:
        raise AssertionError("unknown invoice should raise HTTP 404")


