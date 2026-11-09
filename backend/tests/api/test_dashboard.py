import importlib
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, update

from sentinel.collectors.normalization import PROBES, utcnow
from sentinel.persistence import session as persistence
from sentinel.persistence.models import CollectorCheckpoint, DatabaseTarget, PollRun
from sentinel.persistence.session import get_session


@pytest.fixture
def client(settings, sessions):
    # Import the module without consulting a developer's real .env.
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)

    def request_session():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    with TestClient(app) as client:
        yield client


def test_fleet_detail_metrics_and_offline(client, runner, sessions, make_target):
    healthy = make_target()
    offline = make_target("SYNTHETIC_OFFLINE")
    for target_id in (healthy, offline):
        for probe in PROBES:
            runner.run(target_id, probe)
    fleet = client.get("/api/v1/databases").json()
    assert fleet["total"] == 2
    assert {row["last_health_status"] for row in fleet["items"]} == {"healthy", "offline"}
    assert client.get("/api/v1/databases?health_status=offline").json()["total"] == 1
    assert client.get("/api/v1/databases?page_size=1").json()["items"][0]["id"]
    assert client.get("/api/v1/databases?environment=absent").json()["total"] == 0
    detail = client.get(f"/api/v1/databases/{healthy}").json()
    assert detail["tablespaces"][0]["autoextend"] is True
    assert detail["fra"]["used_pct"] == 28.7
    assert detail["archive"]["log_mode"] == "ARCHIVELOG"
    assert detail["compliance"] == {"pass": 0, "fail": 0, "unknown": 7}
    assert all(obs["id"] and obs["source"] and obs["timestamp"].endswith("+00:00")
               for obs in detail["latest_observations"])
    metrics = client.get(f"/api/v1/databases/{healthy}/metrics?metric_name=storage.tablespace.used_pct").json()
    assert len(metrics) == 1
    assert metrics[0]["value"] == 65.5
    assert client.get(f"/api/v1/databases/{healthy}/observations?limit=2").status_code == 200
    with sessions() as session:
        before = len(session.scalars(select(PollRun)).all())
    for _ in range(5):
        client.get("/api/v1/databases")
        client.get(f"/api/v1/databases/{healthy}/metrics")
    with sessions() as session:
        assert len(session.scalars(select(PollRun)).all()) == before


def test_empty_history_stale_status_and_no_history_scan(client, runner, sessions, make_target):
    target_id = make_target()
    assert client.get(f"/api/v1/databases/{target_id}/metrics").json() == []
    assert client.get(f"/api/v1/databases/{target_id}").json()["last_health_status"] == "unknown"
    for probe in PROBES:
        runner.run(target_id, probe)
    with sessions.begin() as session:
        session.execute(update(DatabaseTarget).values(health_valid_until=utcnow() - timedelta(seconds=1)))
    assert client.get("/api/v1/databases?health_status=unknown").json()["total"] == 1
    assert client.get(f"/api/v1/databases/{target_id}").json()["last_health_status"] == "unknown"
    statements = []
    engine = sessions.kw["bind"]

    def capture(_, __, statement, *___):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        client.get("/api/v1/databases")
        client.get(f"/api/v1/databases/{target_id}")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not any("FROM observations" in statement for statement in statements)


@pytest.mark.parametrize("query", [
    "limit=-1", "limit=5001", "start=invalid",
    "start=2026-01-02T00:00:00Z&end=2026-01-01T00:00:00Z",
    "start=2025-01-01T00:00:00Z&end=2026-01-01T00:00:00Z",
])
def test_bounded_metrics_reject_invalid_queries(client, make_target, query):
    target_id = make_target()
    assert client.get(f"/api/v1/databases/{target_id}/metrics?{query}").status_code == 422


def test_registry_validation_and_manual_connection_not_full_health(client):
    response = client.post("/api/v1/databases", json={"display_name": "New", "tns_alias": "SYNTHETIC_HEALTHY"})
    assert response.status_code == 201
    target_id = response.json()["id"]
    assert client.post(f"/api/v1/databases/{target_id}/test-connection").json()["status"] == "connected"
    assert client.get(f"/api/v1/databases/{target_id}").json()["last_health_status"] == "unknown"
    assert client.post("/api/v1/databases", json={
        "display_name": "Invalid", "poll_profile": {"intervals": {"storage": -1}},
    }).status_code == 422
    assert client.post("/api/v1/databases", json={
        "display_name": "Invalid", "poll_profile": {"name": []},
    }).status_code == 422
    assert client.get("/api/v1/databases/not-a-uuid").status_code == 400
    assert client.get("/api/v1/databases/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.put(f"/api/v1/databases/{target_id}", json={"enabled": None}).status_code == 422
    assert client.delete(f"/api/v1/databases/{target_id}").status_code == 204


def test_target_update_invalidates_current_not_history(client, runner, make_target):
    target_id = make_target()
    for probe in PROBES:
        runner.run(target_id, probe)
    assert client.get(f"/api/v1/databases/{target_id}").json()["last_health_status"] == "healthy"
    assert client.put(f"/api/v1/databases/{target_id}", json={"tns_alias": "NEW_ALIAS"}).status_code == 200
    detail = client.get(f"/api/v1/databases/{target_id}").json()
    assert detail["last_health_status"] == "unknown"
    assert detail["latest_observations"] == []
    assert client.get(f"/api/v1/databases/{target_id}/metrics").json()


def test_stale_readings_are_flagged(client, runner, sessions, make_target):
    target_id = make_target()
    for probe in PROBES:
        runner.run(target_id, probe)
    with sessions.begin() as session:
        session.execute(update(CollectorCheckpoint).where(
            CollectorCheckpoint.target_id == target_id,
            CollectorCheckpoint.collector_type == "storage",
        ).values(last_success_at=utcnow() - timedelta(days=1)))
        session.get(DatabaseTarget, target_id).health_valid_until = utcnow() - timedelta(seconds=1)
    detail = client.get(f"/api/v1/databases/{target_id}").json()
    assert detail["last_health_status"] == "unknown"
    assert all(obs["quality"] == "stale" for obs in detail["latest_observations"]
               if obs["metric_name"].startswith("storage.tablespace."))


def test_readiness_returns_service_unavailable_on_failure(client):
    assert client.get("/health/live").json() == {"status": "alive"}
    assert client.get("/health/ready").status_code == 200
    with patch.object(persistence.engine, "connect", side_effect=RuntimeError("test")):
        assert client.get("/health/ready").status_code == 503
