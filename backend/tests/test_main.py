"""Scaffold smoke checks that do not need external services."""

from fastapi import HTTPException

from backend import main as main_module
from backend.main import FRONTEND_DIR, app, get_invoice, health, list_invoices


def test_health_reports_live_integration_checks(monkeypatch) -> None:
    """The health response reports integrations without requiring network access in tests."""
    monkeypatch.setattr(main_module, "_check_hindsight", lambda: False)
    monkeypatch.setattr(main_module, "_check_llm", lambda: True)
    result = health()
    assert result["status"] == "ok"
    assert result["hindsight_ok"] is False
    assert result["llm_ok"] is True


def test_openapi_and_static_mount_are_registered() -> None:
    """The app builds its API schema and mounts the frontend at the root."""
    schema = app.openapi()
    assert "/api/health" in schema["paths"]
    assert any(getattr(route, "name", "") == "frontend" for route in app.routes)
    assert FRONTEND_DIR.is_dir()


def test_invoice_routes_return_contract_shapes() -> None:
    """The invoice list and detail functions expose the frozen response models."""
    rows = list_invoices().root
    detail = get_invoice("INV-2025-7301")

    assert len(rows) == 60
    deccan_row = next(row for row in rows if row.id == "INV-2025-7301")
    assert deccan_row.vendor_name == "Deccan Freight Lines"
    assert deccan_row.exception_types == ["PRICE_MISMATCH"]
    assert detail.invoice.total == 104000
    assert detail.po is not None and detail.po.total == 100000
    assert detail.vendor.bank_account == "XXXX4821"
    missing_po = get_invoice("INV-2025-7314")
    assert "MISSING_PO" in [exception.type for exception in missing_po.exceptions]


def test_invoice_route_filters_and_returns_clean_not_found() -> None:
    """Status filters work and unknown invoice identifiers map to HTTP 404."""
    assert list_invoices(status="no-such-status").root == []
    try:
        get_invoice("no-such-invoice")
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Invoice or vendor not found"
    else:
        raise AssertionError("unknown invoice should raise HTTP 404")


def test_memory_routes_metrics_and_demo_reset(monkeypatch) -> None:
    from backend.models import MetricsResponse, RecalledMemory

    monkeypatch.setattr(
        main_module.memory,
        "search_memory",
        lambda bank, query, limit: [RecalledMemory(case_id="CASE-0001", memory_type="resolution_case", text="case")],
    )
    monkeypatch.setattr(main_module.memory, "reflect_vendor", lambda bank, vendor: "Net 30.")
    monkeypatch.setattr(main_module, "get_metrics", lambda mode: MetricsResponse(points=[], summary={}))
    monkeypatch.setattr(main_module.store, "clear_bank", lambda bank: None)
    captured: list[str] = []
    monkeypatch.setattr(main_module, "set_active_bank_id", captured.append)

    search = main_module.search_memories("precedent", "bank-a")
    briefing = main_module.vendor_briefing("V-1004")
    metrics_result = main_module.metrics("off")
    reset = main_module.reset_demo(main_module.DemoResetRequest(clear_memory=True))

    assert search[0].type == "resolution_case"
    assert briefing.vendor_id == "V-1004" and briefing.briefing_text == "Net 30."
    assert metrics_result.points == []
    assert reset.ok is True
    assert captured and captured[0].startswith("recallap-demo-demo-")




