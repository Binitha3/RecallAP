"""Scaffold smoke checks that do not need external services."""

from fastapi import HTTPException, Response

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

    assert len(rows) == len(main_module.store.all_invoices())
    deccan_row = next(row for row in rows if row.id == "INV-2025-7301")
    assert deccan_row.vendor_name == "Deccan Freight Lines"
    assert deccan_row.exception_types == ["PRICE_MISMATCH"]
    assert detail.invoice.total == 104000
    assert detail.po is not None and detail.po.total == 100000
    assert detail.vendor.bank_account == "XXXX4821"
    missing_po = get_invoice("INV-2025-7304")
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


def test_process_route_forwards_memory_mode_and_disables_caching(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from backend.models import DecisionAction

    captured = {}

    def fake_process(invoice_id, *, memory_enabled, bank_id):
        captured.update(
            invoice_id=invoice_id,
            memory_enabled=memory_enabled,
            bank_id=bank_id,
        )
        return {
            "invoice_id": invoice_id,
            "memory_used": memory_enabled,
            "decision": {
                "action": DecisionAction.ESCALATE,
                "confidence": 0.5,
                "rationale": "Test response.",
            },
            "guardrails": {"passed": False, "triggered": []},
            "final_status": "NEEDS_REVIEW",
            "latency_ms": 1,
        }

    monkeypatch.setattr(main_module, "process_invoice", fake_process)

    response = TestClient(app).post(
        "/api/invoices/INV-2025-7301/process?memory=on&bank=compare-test"
    )

    assert response.status_code == 200
    assert captured == {
        "invoice_id": "INV-2025-7301",
        "memory_enabled": True,
        "bank_id": "compare-test",
    }
    assert response.json()["memory_used"] is True
    assert response.headers["cache-control"] == "no-store"


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
    captured: list[tuple[str | None, str]] = []
    monkeypatch.setattr(main_module, "new_bank_id", lambda bank=None: "fresh-bank")
    monkeypatch.setattr(main_module.memory, "create_bank", lambda bank: captured.append(("create", bank)))
    monkeypatch.setattr(main_module, "register_bank_id", lambda alias, bank: captured.append((alias, bank)))

    search_response = Response()
    search = main_module.search_memories(search_response, "precedent", "bank-a")
    briefing = main_module.vendor_briefing("V-1004")
    metrics_response = Response()
    metrics_result = main_module.metrics(metrics_response, "off")
    reset = main_module.reset_demo(main_module.DemoResetRequest(clear_memory=True), bank=None)

    assert search[0].type == "resolution_case"
    assert search_response.headers["cache-control"] == "no-store"
    assert search_response.headers["x-recallap-bank-id"] == "bank-a"
    assert briefing.vendor_id == "V-1004" and briefing.briefing_text == "Net 30."
    assert metrics_result.points == []
    assert metrics_response.headers["cache-control"] == "no-store"
    assert reset.ok is True
    assert captured == [("create", "fresh-bank"), (None, "fresh-bank")]


def test_resolve_route_reports_bank_and_retained_state(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    captured = {}

    def fake_resolve(invoice_id, action, note, approver, bank_id, *, reusable_precedent):
        captured.update(invoice_id=invoice_id, bank_id=bank_id)
        return {"case_id": "CASE-0007", "status": "AUTO_RESOLVED", "retained": True}

    monkeypatch.setattr(main_module, "resolve_invoice", fake_resolve)
    response = TestClient(app).post(
        "/api/invoices/INV-2025-7301/resolve?bank=recallap-demo",
        json={"action": "APPROVE", "note": "test", "approver": "Test"},
    )

    assert response.status_code == 200
    assert response.json()["retained"] is True
    assert response.headers["x-recallap-retained"] == "true"
    assert response.headers["x-recallap-bank-id"] == main_module.resolve_bank_id("recallap-demo")
    assert captured["bank_id"] == main_module.resolve_bank_id("recallap-demo")




