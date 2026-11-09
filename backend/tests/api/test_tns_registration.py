from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from sentinel.adapters.oracle.connection import tns_directory
from sentinel.api.v1.databases import router
from sentinel.collectors.alert_flow import AlertEvaluationRunner
from sentinel.collectors.jobs import CollectionRunner
from sentinel.collectors.normalization import PROBES, utcnow
from sentinel.collectors.scheduler import CollectorScheduler
from sentinel.persistence.models import DatabaseTarget
from sentinel.persistence.session import get_session


@pytest.fixture
def client(settings, sessions, tmp_path):
    directory = tmp_path / "tns"
    directory.mkdir()
    (directory / "tnsnames.ora").write_text(
        "\n".join(
            f"{name}=(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=localhost)(PORT=1521))"
            f"(CONNECT_DATA=(SERVICE_NAME={name.lower()})))"
            for name in ("FINANCE", "WAREHOUSE", "REPORTING", "DEVELOPMENT")
        ), encoding="utf-8",
    )
    settings.tns_admin = str(directory)
    settings.synthetic_mode = False
    app = FastAPI()
    app.state.settings = settings
    app.include_router(router)

    def request_session():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    with TestClient(app) as client:
        yield client


@pytest.fixture
def connection(monkeypatch):
    mock = AsyncMock(return_value={"status": "connected", "message": "ok", "version": "19c"})
    monkeypatch.setattr("sentinel.adapters.oracle.connection.OracleConnectionManager.test_connection", mock)
    return mock


def payload(alias="FINANCE"):
    return {"tns_alias": alias, "display_name": f"{alias} database", "environment": "production", "owner": "DBA"}


def test_alias_picker_test_and_registration_persist_across_requests(client, connection, sessions):
    aliases = client.get("/api/v1/databases/tns-aliases")
    assert aliases.status_code == 200
    assert {item["name"] for item in aliases.json()["items"]} == {"FINANCE", "WAREHOUSE", "REPORTING", "DEVELOPMENT"}
    assert not any(item["registered"] for item in aliases.json()["items"])
    assert "localhost" not in aliases.text
    test = client.post("/api/v1/databases/test-tns-connection", json=payload())
    assert test.status_code == 200
    assert test.json()["status"] == "connected"
    assert client.get("/api/v1/databases").json()["total"] == 0
    registered = client.post("/api/v1/databases/register-from-tns", json=payload(" finance "))
    assert registered.status_code == 201
    target = registered.json()
    assert target["tns_alias"] == "FINANCE"
    assert target["enabled"] is True
    assert target["last_health_status"] == "unknown"
    assert target["last_connection_at"] is not None
    assert "password" not in registered.text
    assert client.get("/api/v1/databases").json()["items"][0]["id"] == target["id"]
    assert next(item for item in client.get("/api/v1/databases/tns-aliases").json()["items"]
                if item["name"] == "FINANCE")["registered"] is True
    duplicate = client.post("/api/v1/databases/register-from-tns", json=payload("finance"))
    assert duplicate.status_code == 409
    assert client.get("/api/v1/databases").json()["total"] == 1
    with sessions() as session:
        stored = session.scalar(select(DatabaseTarget))
        assert stored.tns_alias == "FINANCE"
        assert stored.environment == "production"
        assert stored.owner == "DBA"
    assert connection.await_count == 2


def test_four_targets_reload_into_a_new_scheduler(client, connection, runner, settings, sessions):
    ids = []
    for alias in ("FINANCE", "WAREHOUSE", "REPORTING", "DEVELOPMENT"):
        response = client.post("/api/v1/databases/register-from-tns", json=payload(alias))
        assert response.status_code == 201
        ids.append(response.json()["id"])
    assert client.get("/api/v1/databases").json()["total"] == 4
    settings.scheduler_enabled = True
    for _ in range(2):
        engine = create_engine(settings.database_url)
        try:
            restarted_sessions = sessionmaker(bind=engine, expire_on_commit=False)
            restarted_runner = CollectionRunner(restarted_sessions, settings, runner.adapters)
            scheduler = CollectorScheduler(restarted_runner, AlertEvaluationRunner(restarted_sessions, settings, runner.adapters))
            scheduler.reconcile()
            jobs = scheduler.scheduler.get_jobs()
            assert len(jobs) == 4 * (len(PROBES) + 1)
            assert {str(job.args[0]) for job in jobs} == set(ids)
        finally:
            engine.dispose()
    assert client.get("/api/v1/databases/summary").json() == {
        "total": 4, "healthy": 0, "warning": 0, "critical": 0,
        "offline": 0, "unknown": 4, "disabled": 0, "scheduler_enabled": True,
    }


def test_failed_connection_does_not_register_or_leak_driver_text(client, connection):
    connection.return_value = {"status": "error", "message": "fixture-sensitive-driver-text"}
    response = client.post("/api/v1/databases/register-from-tns", json=payload())
    assert response.status_code == 422
    assert "fixture-sensitive-driver-text" not in response.text
    assert client.get("/api/v1/databases").json()["total"] == 0


@pytest.mark.parametrize("fields", [
    {"tns_alias": "NOT_CONFIGURED"}, {"display_name": " "}, {"environment": " "}, {"tns_alias": ""},
])
def test_registration_rejects_invalid_fields(client, connection, fields):
    assert client.post("/api/v1/databases/register-from-tns", json={**payload(), **fields}).status_code == 422
    connection.assert_not_awaited()


def test_alias_errors_are_explicit_and_do_not_expose_paths(client, settings):
    settings.tns_admin = None
    assert client.get("/api/v1/databases/tns-aliases").status_code == 503
    settings.tns_admin = "missing-tns-directory"
    response = client.get("/api/v1/databases/tns-aliases")
    assert response.status_code == 503
    assert "missing-tns-directory" not in response.text


def test_relative_tns_directory_is_independent_of_process_working_directory(settings, monkeypatch, tmp_path):
    settings.tns_admin = "tns"
    monkeypatch.chdir(tmp_path)
    assert tns_directory(settings) == Path(__file__).resolve().parents[3] / "tns"


def test_summary_counts_entire_fleet_including_stale_and_disabled(client, sessions, make_target):
    now = utcnow()
    for status, enabled, until in [
        ("healthy", True, now + timedelta(minutes=10)), ("warning", True, now + timedelta(minutes=10)),
        ("offline", True, now + timedelta(minutes=10)), ("critical", True, now + timedelta(minutes=10)),
        ("healthy", True, now - timedelta(minutes=10)), ("healthy", False, now + timedelta(minutes=10)),
    ]:
        target_id = make_target()
        with sessions.begin() as session:
            target = session.get(DatabaseTarget, target_id)
            target.last_health_status = status
            target.enabled = enabled
            target.health_valid_until = until
    summary = client.get("/api/v1/databases/summary").json()
    assert summary == {
        "total": 6, "healthy": 1, "warning": 1, "critical": 1,
        "offline": 1, "unknown": 1, "disabled": 1, "scheduler_enabled": False,
    }
    assert client.get("/api/v1/databases?page_size=1").json()["total"] == 6
