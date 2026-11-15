"""Phase 20: every changing route has a permission, sessions can be revoked, and actions are audited."""

import importlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from sentinel.auth.sessions import forget
from sentinel.auth.throttle import LoginThrottle
from sentinel.config.settings import Settings
from sentinel.persistence.models import AuditEvent, Incident, OperatorAccount

VIEWER = {"X-Sentinel-Role": "viewer", "X-Sentinel-Principal": "vera"}
OPERATOR = {"X-Sentinel-Role": "operator", "X-Sentinel-Principal": "olga"}
DBA = {"X-Sentinel-Role": "dba", "X-Sentinel-Principal": "dana"}
ADMIN = {"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"}
BOOT_PASSWORD = "Bootstrap-Pass-2026"


def _client(settings, sessions) -> TestClient:
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)
    client = TestClient(app)
    client.__enter__()
    app.state.session_factory = sessions
    return client


def _actions(sessions) -> list[tuple[str, str, str]]:
    with sessions() as session:
        rows = session.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at)).all()
        return [(row.action, row.principal, row.outcome) for row in rows]


@pytest.fixture
def signed(settings):
    forget()
    return settings.model_copy(update={
        "auth_required": True,
        "auth_bootstrap_username": "sentinel",
        "auth_bootstrap_password": BOOT_PASSWORD,
        "auth_bootstrap_role": "admin",
        "auth_password_iterations": 1000,
        "auth_max_failed_logins": 3,
    })


def test_registry_changes_need_database_manage_and_are_audited(settings, sessions, make_target) -> None:
    target_id = make_target(alias="PDBORCL")
    client = _client(settings, sessions)
    try:
        for headers in (VIEWER, OPERATOR):
            assert client.post("/api/v1/databases", json={"display_name": "X", "tns_alias": "SYNTHETIC_PDB"}, headers=headers).status_code == 403
            assert client.put(f"/api/v1/databases/{target_id}", json={"enabled": False}, headers=headers).status_code == 403
            assert client.post(f"/api/v1/databases/{target_id}/test-connection", headers=headers).status_code == 403
            assert client.post("/api/v1/databases/test-tns-connection", json={"tns_alias": "A", "display_name": "A"}, headers=headers).status_code == 403
            assert client.post("/api/v1/databases/register-from-tns", json={"tns_alias": "A", "display_name": "A"}, headers=headers).status_code == 403
            assert client.delete(f"/api/v1/databases/{target_id}", headers=headers).status_code == 403
            assert client.get("/api/v1/databases", headers=headers).status_code == 200
        created = client.post("/api/v1/databases", json={"display_name": "New", "tns_alias": "SYNTHETIC_PDB"}, headers=DBA)
        updated = client.put(f"/api/v1/databases/{target_id}", json={"owner": "Finance"}, headers=DBA)
        removed = client.delete(f"/api/v1/databases/{target_id}", headers=DBA)
    finally:
        client.__exit__(None, None, None)
    assert (created.status_code, updated.status_code, removed.status_code) == (201, 200, 204)
    assert [item for item in _actions(sessions) if item[0].startswith("database.")] == [
        ("database.register", "dana", "created"),
        ("database.update", "dana", "updated"),
        ("database.remove", "dana", "removed"),
    ]


def test_incident_actions_need_permission_and_use_the_caller_as_actor(settings, sessions, make_target) -> None:
    target_id = make_target(alias="PDBORCL")
    with sessions.begin() as session:
        incident = Incident(fingerprint="f1", target_id=target_id, rule_id="r1", state="new", severity="critical", message="m")
        session.add(incident)
        session.flush()
        incident_id = str(incident.id)
    client = _client(settings, sessions)
    try:
        denied = client.post(f"/api/v1/incidents/{incident_id}/acknowledge", json={"actor": "sentinel"}, headers=VIEWER)
        allowed = client.post(f"/api/v1/incidents/{incident_id}/acknowledge", json={"actor": "someone-else"}, headers=OPERATOR)
        detail = client.get(f"/api/v1/incidents/{incident_id}", headers=VIEWER)
    finally:
        client.__exit__(None, None, None)
    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert "someone-else" not in detail.text
    assert "olga" in detail.text
    assert ("incident.acknowledge", "olga", "acknowledged") in _actions(sessions)


def test_chat_thread_belongs_to_the_operator_who_opened_it(settings, sessions) -> None:
    client = _client(settings, sessions)
    try:
        thread = client.post("/api/v1/chat/threads", json={"scope_type": "fleet"}, headers=ADMIN).json()
        other_read = client.get(f"/api/v1/chat/threads/{thread['id']}/messages", headers=VIEWER)
        other_post = client.post(f"/api/v1/chat/threads/{thread['id']}/messages", json={"content": "Summarize open incidents"}, headers=VIEWER)
        own_post = client.post(f"/api/v1/chat/threads/{thread['id']}/messages", json={"content": "Summarize open incidents"}, headers=ADMIN)
        own_read = client.get(f"/api/v1/chat/threads/{thread['id']}/messages", headers=ADMIN)
    finally:
        client.__exit__(None, None, None)
    assert (other_read.status_code, other_post.status_code) == (404, 404)
    assert (own_post.status_code, own_read.status_code) == (200, 200)
    questions = [item for item in _actions(sessions) if item[0] == "chat.question"]
    assert len(questions) == 1
    assert questions[0][1] == "sentinel"


def test_sign_in_is_audited_throttled_and_revocable(signed, sessions) -> None:
    client = _client(signed, sessions)
    try:
        assert client.get("/api/v1/databases").status_code == 401
        assert client.get("/metrics").status_code == 200
        for _attempt in range(3):
            assert client.post("/api/v1/auth/login", json={"username": "sentinel", "password": "wrong-password", "local": True}).status_code == 401
        locked = client.post("/api/v1/auth/login", json={"username": "sentinel", "password": BOOT_PASSWORD, "local": True})
        assert locked.status_code == 429
        assert int(locked.headers["Retry-After"]) > 0
        client.app.state.login_throttle = LoginThrottle(3, 900, 900)
        ok = client.post("/api/v1/auth/login", json={"username": "Sentinel", "password": BOOT_PASSWORD, "local": True})
        assert ok.status_code == 200
        assert ok.json()["role"] == "admin"
        assert client.get("/api/v1/databases").status_code == 200

        created = client.post("/api/v1/admin/operators", json={"username": "Olga.Ops", "password": "Operator-Pass-1", "role": "operator"})
        assert created.status_code == 201
        assert created.json() == {"username": "olga.ops", "role": "operator", "enabled": True, "created_at": created.json()["created_at"]}
        assert client.post("/api/v1/admin/operators", json={"username": "olga.ops", "password": "Operator-Pass-1", "role": "operator"}).status_code == 409
        assert client.post("/api/v1/admin/operators", json={"username": "weak", "password": "short", "role": "operator"}).status_code == 422
        assert [item["username"] for item in client.get("/api/v1/admin/operators").json()["items"]] == ["olga.ops", "sentinel"]
        assert client.post("/api/v1/admin/operators/sentinel/enabled", json={"enabled": False}).status_code == 403

        operator = TestClient(client.app)
        signed_in = operator.post("/api/v1/auth/login", json={"username": "olga.ops", "password": "Operator-Pass-1", "local": True})
        assert signed_in.status_code == 200
        assert operator.get("/api/v1/databases").status_code == 200
        assert operator.get("/api/v1/admin/operators").status_code == 403
        assert operator.post("/api/v1/admin/operators", json={"username": "evil", "password": "Operator-Pass-1", "role": "admin"}).status_code == 403

        # A mixed-case name reaches the same account. The role change signs the old session out.
        assert client.post("/api/v1/admin/operators/Olga.Ops/role", json={"role": "dba"}).status_code == 200
        assert operator.get("/api/v1/databases").status_code == 401
        assert operator.get("/api/v1/auth/session").json()["authenticated"] is False
        assert operator.post("/api/v1/auth/login", json={"username": "olga.ops", "password": "Operator-Pass-1", "local": True}).json()["role"] == "dba"

        changed = operator.post("/api/v1/auth/password", json={"current_password": "Operator-Pass-1", "new_password": "Operator-Pass-2"})
        assert changed.status_code == 200
        assert operator.get("/api/v1/databases").status_code == 200
        assert operator.post("/api/v1/auth/password", json={"current_password": "nope", "new_password": "Operator-Pass-3"}).status_code == 403

        assert client.post("/api/v1/admin/operators/olga.ops/enabled", json={"enabled": False}).status_code == 200
        assert operator.get("/api/v1/databases").status_code == 401
        assert operator.post("/api/v1/auth/login", json={"username": "olga.ops", "password": "Operator-Pass-2", "local": True}).status_code == 401

        assert client.post("/api/v1/auth/logout").status_code == 200
    finally:
        client.__exit__(None, None, None)
    actions = _actions(sessions)
    assert ("auth.login", "sentinel", "denied") in actions
    assert ("auth.login", "sentinel", "locked") in actions
    assert ("auth.login", "sentinel", "success") in actions
    assert ("operator.create", "sentinel", "created") in actions
    assert ("operator.role", "sentinel", "updated") in actions
    assert ("operator.enabled", "sentinel", "disabled") in actions
    assert ("auth.password", "olga.ops", "updated") in actions
    assert ("auth.logout", "sentinel", "success") in actions
    with sessions() as session:
        stored = session.scalar(select(OperatorAccount).where(OperatorAccount.username == "olga.ops"))
        assert stored is not None
        assert "Operator-Pass" not in stored.password_hash


def test_responses_carry_security_headers(settings, sessions) -> None:
    client = _client(settings, sessions)
    try:
        api = client.get("/api/v1/databases")
        live = client.get("/health/live")
    finally:
        client.__exit__(None, None, None)
    assert api.headers["x-content-type-options"] == "nosniff"
    assert api.headers["x-frame-options"] == "DENY"
    assert api.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in api.headers["content-security-policy"]
    assert live.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in live.headers


def test_production_refuses_unsafe_settings(tmp_path) -> None:
    base: dict[str, object] = {
        "_env_file": None,
        "database_url": f"sqlite:///{tmp_path / 's.db'}",
        "app_env": "production",
        "auth_bootstrap_username": "sentinel",
        "auth_bootstrap_password": BOOT_PASSWORD,
    }
    with pytest.raises(ValueError, match="AUTH_REQUIRED"):
        Settings(**base)
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(**base, auth_required=True)
    with pytest.raises(ValueError, match="AUTH_COOKIE_SECURE"):
        Settings(**base, auth_required=True, secret_key="k" * 44)
    safe = Settings(**base, auth_required=True, secret_key="k" * 44, auth_cookie_secure=True)
    assert safe.app_env == "production"
    from sentinel.auth.middleware import metrics_are_public

    assert metrics_are_public(safe) is False
    assert metrics_are_public(safe.model_copy(update={"metrics_public": True})) is True


def test_knowledge_upload_is_admin_only_checked_and_audited(settings, sessions, tmp_path) -> None:
    intake = tmp_path / "intake"
    scoped = settings.model_copy(update={"knowledge_intake_path": str(intake), "knowledge_max_file_bytes": 2048})
    client = _client(scoped, sessions)
    body = b"# Runbook\n\nWhen ORA-00257 appears, check the archive destination and the recovery area before anything else.\n" * 3
    try:
        denied = client.post("/api/v1/knowledge/upload?filename=runbook.md&category=Knowledge", content=body, headers=OPERATOR)
        saved = client.post("/api/v1/knowledge/upload?filename=runbook.md&category=Knowledge", content=body, headers=ADMIN)
        again = client.post("/api/v1/knowledge/upload?filename=runbook.md&category=Knowledge", content=body, headers=ADMIN)
        replaced = client.post("/api/v1/knowledge/upload?filename=runbook.md&category=Knowledge&replace=true", content=body, headers=ADMIN)
        traversal = client.post("/api/v1/knowledge/upload?filename=..%2Fevil.md&category=Knowledge", content=body, headers=ADMIN)
        wrong_type = client.post("/api/v1/knowledge/upload?filename=tool.exe&category=Knowledge", content=body, headers=ADMIN)
        fake_pdf = client.post("/api/v1/knowledge/upload?filename=fake.pdf&category=Policy", content=body, headers=ADMIN)
        too_big = client.post("/api/v1/knowledge/upload?filename=big.md&category=Knowledge", content=b"a" * 4096, headers=ADMIN)
        listed = client.get("/api/v1/knowledge/documents", headers=ADMIN)
    finally:
        client.__exit__(None, None, None)
    assert denied.status_code == 403
    assert saved.status_code == 201
    assert saved.json()["relative_path"] == "ora-19c-manual/runbook.md"
    assert saved.json()["indexed"] == 1
    assert again.status_code == 409
    assert replaced.status_code == 201
    assert replaced.json()["replaced"] is True
    assert (traversal.status_code, wrong_type.status_code, fake_pdf.status_code, too_big.status_code) == (422, 422, 422, 413)
    assert (intake / "ora-19c-manual" / "runbook.md").read_bytes() == body
    assert not (intake / "evil.md").exists()
    assert [item["file_name"] for item in listed.json()["items"]] == ["runbook.md"]
    uploads = [item for item in _actions(sessions) if item[0] == "knowledge.upload"]
    assert uploads == [("knowledge.upload", "sentinel", "created"), ("knowledge.upload", "sentinel", "replaced")]
