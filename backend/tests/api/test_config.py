"""An administrator can change stored settings. Secrets stay out of the table."""

import importlib
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from sentinel.persistence.models import AppConfig, AuditEvent


def _client(settings, sessions) -> TestClient:
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)
    client = TestClient(app)
    client.__enter__()
    app.state.session_factory = sessions
    return client


def test_admin_can_change_a_stored_setting(settings, sessions) -> None:
    client = _client(settings, sessions)
    try:
        denied = client.put(
            "/api/v1/admin/config/log_level",
            json={"value": "DEBUG"},
            headers={"X-Sentinel-Role": "dba", "X-Sentinel-Principal": "dana"},
        )
        saved = client.put(
            "/api/v1/admin/config/log_level",
            json={"value": "DEBUG"},
            headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"},
        )
        secret = client.put(
            "/api/v1/admin/config/log_level",
            json={"value": "secret://ORACLE_PASSWORD"},
            headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"},
        )
        fixed = client.put(
            "/api/v1/admin/config/ai_fallback_provider",
            json={"value": "hosted"},
            headers={"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"},
        )
        listed = client.get("/api/v1/admin/config", headers={"X-Sentinel-Role": "admin"})
    finally:
        client.__exit__(None, None, None)
    assert denied.status_code == 403
    assert saved.status_code == 200
    assert saved.json()["value"] == "DEBUG"
    assert secret.status_code == 400
    assert fixed.status_code == 400
    assert settings.log_level == "DEBUG"
    assert any(item["key"] == "log_level" and item["value"] == "DEBUG" for item in listed.json()["items"])
    with sessions.begin() as session:
        stored = session.get(AppConfig, "log_level")
        audit = session.scalar(select(AuditEvent).where(AuditEvent.action == "config.update", AuditEvent.outcome == "updated"))
    assert stored is not None and stored.value == "DEBUG"
    assert audit is not None and audit.detail["key"] == "log_level"
    assert "oracle_password" not in {item["key"] for item in listed.json()["items"]}
