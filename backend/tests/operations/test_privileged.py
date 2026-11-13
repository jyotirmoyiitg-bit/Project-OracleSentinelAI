"""Privileged operations stay disabled and leave no execution row."""

import importlib
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from sentinel.operations.privileged import disabled
from sentinel.operations.privileged.disabled import DISABLED_MESSAGE, NOT_IMPLEMENTED_MESSAGE
from sentinel.persistence.models import AuditEvent, PrivilegedOperation


@pytest.fixture
def client(settings, sessions):
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)
    with TestClient(app) as test_client:
        app.state.session_factory = sessions
        yield test_client


def _headers(role: str, principal: str = "sentinel") -> dict[str, str]:
    return {"X-Sentinel-Role": role, "X-Sentinel-Principal": principal}


def test_privileged_execution_is_disabled_by_default(settings) -> None:
    assert settings.operations_execution_enabled is False
    assert settings.operations_execution_approved is False
    values = settings.model_dump()
    values["operations_execution_enabled"] = True
    with pytest.raises(ValidationError, match="OPERATIONS_EXECUTION_APPROVED"):
        type(settings).model_validate(values)


def test_privileged_endpoints_return_501_and_do_not_store_execution(client: TestClient, sessions) -> None:
    proposal_id = uuid.uuid4()
    listed = client.get("/api/v1/operations/privileged?enabled=true", headers=_headers("admin"))
    executed = client.post(f"/api/v1/operations/privileged/{proposal_id}/execute", headers=_headers("admin"))
    assert listed.status_code == 501
    assert executed.status_code == 501
    assert listed.json()["detail"] == DISABLED_MESSAGE
    assert executed.json()["detail"] == DISABLED_MESSAGE
    with sessions.begin() as session:
        stored = session.scalar(select(func.count()).select_from(PrivilegedOperation))
        audits = session.scalars(select(AuditEvent).where(AuditEvent.action == "privileged.access")).all()
    assert stored == 0
    assert {row.outcome for row in audits} == {"disabled"}
    assert all(row.detail["statement_sent"] is False for row in audits)


def test_a_viewer_cannot_open_privileged_operations(client: TestClient, sessions) -> None:
    denied = client.get("/api/v1/operations/privileged", headers=_headers("viewer", "guest"))
    assert denied.status_code == 403
    with sessions.begin() as session:
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "privileged.access", AuditEvent.principal == "guest"),
        )
    assert audit is not None
    assert audit.outcome == "denied"


def test_an_approved_flag_still_does_not_execute(settings, sessions) -> None:
    acknowledged = settings.model_copy(update={
        "operations_execution_enabled": True,
        "operations_execution_approved": True,
    })
    with patch("sentinel.config.settings.get_settings", return_value=acknowledged):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(acknowledged)
    with TestClient(app) as test_client:
        app.state.session_factory = sessions
        response = test_client.post(
            f"/api/v1/operations/privileged/{uuid.uuid4()}/execute",
            headers=_headers("admin"),
        )
    assert response.status_code == 501
    assert response.json()["detail"] == NOT_IMPLEMENTED_MESSAGE
    with sessions.begin() as session:
        stored = session.scalar(select(func.count()).select_from(PrivilegedOperation))
    assert stored == 0


def test_privileged_package_has_no_execution_tool() -> None:
    source = Path(disabled.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("oracledb", "subprocess", "sqlplus", "rman"):
        assert forbidden not in source
