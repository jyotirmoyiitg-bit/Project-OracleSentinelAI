"""Proposal-only changes, RBAC, and an audit trail that cannot be edited."""

import importlib
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DatabaseError

from sentinel.auth.rbac import PermissionDenied
from sentinel.collectors.normalization import utcnow
from sentinel.knowledge.acl import Caller
from sentinel.observability.audit import AuditImmutable
from sentinel.observability.retention import RetentionDenied, purge_audit
from sentinel.operations.proposals import execution_enabled, refuse_execution
from sentinel.persistence.models import AuditEvent, CurrentObservation, DatabaseEntitlement, OperationProposal


@pytest.fixture
def client(settings, sessions):
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)
    with TestClient(app) as test_client:
        app.state.session_factory = sessions
        yield test_client


def _measure(sessions, target_id: uuid.UUID, free_bytes: int = 20 * 1024 ** 3) -> None:
    now = utcnow()
    with sessions.begin() as session:
        session.add(CurrentObservation(
            target_id=target_id, metric_name="config.parameter", label_key="omf",
            observation_id=uuid.uuid4(), probe_type="configuration", timestamp=now,
            value_str="/u01/oradata", labels={"parameter": "db_create_file_dest"},
            quality="measured", source="v$parameter",
        ))
        session.add(CurrentObservation(
            target_id=target_id, metric_name="storage.fra.space_limit", label_key="limit",
            observation_id=uuid.uuid4(), probe_type="fra_archive", timestamp=now,
            value=float(free_bytes + 1024), quality="measured", source="v$recovery_file_dest", labels={},
        ))
        session.add(CurrentObservation(
            target_id=target_id, metric_name="storage.fra.space_used", label_key="used",
            observation_id=uuid.uuid4(), probe_type="fra_archive", timestamp=now,
            value=1024, quality="measured", source="v$recovery_file_dest", labels={},
        ))


def _headers(role: str, principal: str) -> dict[str, str]:
    return {"X-Sentinel-Role": role, "X-Sentinel-Principal": principal}


def test_extend_users_is_a_pending_proposal_and_cannot_execute(client: TestClient, sessions, make_target) -> None:
    target_id = make_target("PDBORCL")
    _measure(sessions, target_id)
    created = client.post(
        "/api/v1/operations/proposals",
        json={"target_id": str(target_id), "request_text": "Extend USERS by 10 GB"},
        headers=_headers("admin", "sentinel"),
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "pending"
    assert body["execution_allowed"] is False
    assert body["execution_log"] == []
    assert body["preview_sql"] == "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10240M"
    assert "oracledb" not in body["preview_sql"]
    listed = client.get("/api/v1/operations/proposals", headers=_headers("operator", "sentinel"))
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == body["id"]
    blocked = client.post(f"/api/v1/operations/proposals/{body['id']}/execute", headers=_headers("admin", "sentinel"))
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "Release 1 does not execute proposals."
    assert execution_enabled() is False
    with pytest.raises(PermissionDenied):
        refuse_execution()


def test_unapproved_expired_and_self_approval_are_denied(client: TestClient, sessions, make_target) -> None:
    target_id = make_target()
    _measure(sessions, target_id)
    created = client.post(
        "/api/v1/operations/proposals",
        json={"target_id": str(target_id), "request_text": "Extend USERS by 10 GB"},
        headers=_headers("dba", "ada"),
    )
    assert created.status_code == 403
    with sessions.begin() as session:
        session.add(DatabaseEntitlement(principal="ada", target_id=target_id, environment=""))
        session.add(DatabaseEntitlement(principal="kim", target_id=target_id, environment=""))
    created = client.post(
        "/api/v1/operations/proposals",
        json={"target_id": str(target_id), "request_text": "Extend USERS by 10 GB"},
        headers=_headers("dba", "ada"),
    )
    assert created.status_code == 201
    proposal_id = created.json()["id"]
    own = client.post(
        f"/api/v1/operations/proposals/{proposal_id}/approve",
        json={"reason": "I requested this"},
        headers=_headers("dba", "ada"),
    )
    assert own.status_code == 403
    approved = client.post(
        f"/api/v1/operations/proposals/{proposal_id}/approve",
        json={"reason": "Capacity is available"},
        headers=_headers("platform_admin", "kim"),
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["execution_allowed"] is False
    again = client.post(
        f"/api/v1/operations/proposals/{proposal_id}/reject",
        json={"reason": "Too late"},
        headers=_headers("platform_admin", "kim"),
    )
    assert again.status_code == 409
    with sessions.begin() as session:
        pending = OperationProposal(
            requester="ada", requester_role="dba", action="tablespace.add_omf_datafile",
            target_id=target_id, parameters={"tablespace": "USERS", "size_mb": 10240},
            preview_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 10240M",
            impact="not executed", risk="medium", status="pending", expires_at=utcnow().replace(year=2020),
            correlation_id=str(uuid.uuid4()), execution_log=[],
        )
        session.add(pending)
        session.flush()
        expired_id = pending.id
    expired = client.post(
        f"/api/v1/operations/proposals/{expired_id}/approve",
        json={"reason": "After the window"},
        headers=_headers("platform_admin", "kim"),
    )
    assert expired.status_code == 409
    assert "expired" in expired.json()["detail"]


def test_roles_and_unsafe_requests_are_rejected(client: TestClient, sessions, make_target) -> None:
    target_id = make_target()
    _measure(sessions, target_id)
    viewer = client.get("/api/v1/operations/proposals", headers=_headers("viewer", "view"))
    assert viewer.status_code == 403
    auditor = client.get("/api/v1/audit/events", headers=_headers("auditor", "audit"))
    assert auditor.status_code == 200
    operator = client.post(
        "/api/v1/operations/proposals",
        json={"target_id": str(target_id), "request_text": "Extend USERS by 10 GB"},
        headers=_headers("operator", "op"),
    )
    assert operator.status_code == 403
    for text_value in (
        "DROP TABLESPACE USERS",
        "Extend USERS;DROP by 10 GB",
        "Extend USERS by 10 GB; shutdown",
        "ALTER SYSTEM SET OPEN_CURSORS=100",
    ):
        rejected = client.post(
            "/api/v1/operations/proposals",
            json={"target_id": str(target_id), "request_text": text_value},
            headers=_headers("admin", "sentinel"),
        )
        assert rejected.status_code == 400
    other = make_target("OTHER")
    _measure(sessions, other)
    with sessions.begin() as session:
        session.add(DatabaseEntitlement(principal="ada", target_id=target_id, environment=""))
    cross = client.post(
        "/api/v1/operations/proposals",
        json={"target_id": str(other), "request_text": "Extend USERS by 10 GB"},
        headers=_headers("dba", "ada"),
    )
    assert cross.status_code == 403


def test_audit_events_cannot_be_updated_or_deleted(sessions) -> None:
    with sessions.begin() as session:
        session.add(AuditEvent(
            principal="sentinel", service="api", action="proposal.create", target="proposal",
            correlation_id=str(uuid.uuid4()), outcome="pending", evidence_digest="a" * 64, detail={},
        ))
    with sessions.begin() as session:
        row = session.scalars(select(AuditEvent)).one()
        row.outcome = "tampered"
        with pytest.raises(AuditImmutable):
            session.flush()
    with sessions.begin() as session, pytest.raises(DatabaseError, match="immutable"):
        session.execute(text("UPDATE audit_events SET outcome = 'tampered'"))
    with pytest.raises(RetentionDenied):
        purge_audit(utcnow())


def test_credential_reference_rejects_a_raw_secret(client: TestClient, make_target) -> None:
    target_id = make_target()
    raw = client.post(
        f"/api/v1/admin/targets/{target_id}/credential-rotation",
        json={"secret_reference": "PlainPasswordValue"},
        headers=_headers("admin", "sentinel"),
    )
    assert raw.status_code == 400
    rotated = client.post(
        f"/api/v1/admin/targets/{target_id}/credential-rotation",
        json={"secret_reference": "secret://ORACLE_PASSWORD"},
        headers=_headers("admin", "sentinel"),
    )
    assert rotated.status_code == 200
    assert rotated.json()["secret_reference"] == "secret://ORACLE_PASSWORD"
    assert "PlainPasswordValue" not in rotated.text
    platform = client.get("/api/v1/admin/platform", headers=_headers("security_auditor", "audit"))
    assert platform.status_code == 403
    platform = client.get("/api/v1/admin/platform", headers=_headers("admin", "sentinel"))
    assert platform.status_code == 200
    assert platform.json()["execution_allowed"] is False


def test_rbac_service_layer_rejects_a_viewer() -> None:
    from sentinel.operations.proposals import list_proposals

    class Empty:
        def scalars(self, _statement):
            raise AssertionError("the database must not be queried")

    with pytest.raises(PermissionDenied):
        list_proposals(Empty(), Caller("viewer", "view"), None, None)  # type: ignore[arg-type]
