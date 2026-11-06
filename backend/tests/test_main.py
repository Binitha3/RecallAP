"""Scaffold smoke checks that do not need external services."""

from backend.main import FRONTEND_DIR, app, health


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


