"""Phase 23: stored counter samples so GraphDB and WaitDB open with history."""

import asyncio
import importlib
import json
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from sentinel.adapters.oracle.activity import ActivityAccessError, ActivityUnavailable
from sentinel.collectors.normalization import utcnow
from sentinel.config.store import list_public_config, sync_public_config, update_public_config
from sentinel.graph import history
from sentinel.graph.sampler import GraphHistorySampler
from sentinel.persistence.models import CurrentObservation, DatabaseTarget, Observation
from sentinel.persistence.telemetry import history as metric_history

OPERATOR = {"X-Sentinel-Role": "operator", "X-Sentinel-Principal": "olga"}
ADMIN = {"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"}


def _client(settings, sessions) -> TestClient:
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)
    client = TestClient(app)
    client.__enter__()
    app.state.session_factory = sessions
    return client


def _samples(sessions) -> list[Observation]:
    with sessions() as session:
        return list(session.scalars(
            select(Observation).where(Observation.metric_name == history.METRIC).order_by(Observation.timestamp),
        ))


def test_a_sample_holds_the_counters_the_pages_use(settings) -> None:
    payload = asyncio.run(history.take_sample(settings, "PDBORCL"))
    assert payload is not None
    assert payload["counters"] == {
        "physical reads": 100, "redo entries": 40, "parse count (total)": 20, "execute count": 80,
    }
    assert payload["io"]["consistent gets"] == 8000
    assert payload["cpu_micro"] == 8000000
    assert payload["files"] == {"reads": 100, "writes": 20, "read_time_cs": 40, "write_time_cs": 10}
    assert [event["event"] for event in payload["events"]] == ["db file sequential read", "log file sync"]
    assert payload["events"][0] == {
        "event": "db file sequential read", "wait_class": "User I/O", "total_waits": 1000, "time_waited_micro": 2000000,
    }


def test_a_sample_runs_only_the_five_fixed_readings_on_one_reader(settings) -> None:
    asked: list[tuple[str, str]] = []

    async def reader(_settings, alias, kind):
        asked.append((alias, kind))
        return [], ""

    assert asyncio.run(history.take_sample(settings, "PDBORCL", reader)) is None
    assert asked == [("PDBORCL", kind) for kind in history.KINDS]
    assert set(history.KINDS) == {
        "get_rate_counters", "get_wait_events", "get_wait_cpu", "get_io_counters", "get_file_io",
    }


def test_a_denied_reading_is_left_out_and_the_rest_is_kept(settings) -> None:
    async def reader(_settings, _alias, kind):
        if kind == "get_file_io":
            raise ActivityAccessError("SYS.V_$FILESTAT")
        if kind == "get_wait_events":
            raise ActivityUnavailable(942)
        if kind == "get_rate_counters":
            return [{"name": "Physical Reads", "value": "12"}, {"name": "redo entries", "value": ""}], ""
        if kind == "get_wait_cpu":
            return [{"stat_name": "DB CPU", "value": "55"}], ""
        return [{"name": "consistent gets", "value": "7"}], ""

    payload = asyncio.run(history.take_sample(settings, "PDBORCL", reader))
    assert payload == {
        "counters": {"physical reads": 12}, "io": {"consistent gets": 7}, "events": [], "cpu_micro": 55, "files": {},
    }


def test_a_database_that_cannot_be_read_stores_nothing(settings) -> None:
    async def reader(_settings, _alias, _kind):
        raise ActivityUnavailable(12541)

    assert asyncio.run(history.take_sample(settings, "PDBORCL", reader)) is None


def test_the_sweep_stores_one_row_per_enabled_database(settings, sessions, make_target) -> None:
    first = make_target(alias="FINANCE")
    second = make_target(alias="WAREHOUSE")
    make_target(alias="RETIRED", enabled=False)
    stored = asyncio.run(history.sample_fleet(settings, sessions))
    rows = _samples(sessions)
    assert stored == 2
    assert {row.target_id for row in rows} == {first, second}
    row = rows[0]
    assert (row.metric_name, row.value, row.unit, row.source) == ("activity.sample", None, "json", "graph_history")
    assert json.loads(row.value_str)["counters"]["execute count"] == 80
    with sessions() as session:
        assert session.scalars(select(CurrentObservation)).all() == []
        target = session.get(DatabaseTarget, first)
        assert target.last_health_status in (None, "unknown")


def test_one_slow_database_does_not_stop_the_others(settings, sessions, make_target, monkeypatch) -> None:
    make_target(alias="SLOW")
    quick = make_target(alias="QUICK")

    async def take(_settings, alias, reader=None):
        if alias == "SLOW":
            raise TimeoutError
        return {"counters": {"execute count": 5}, "io": {}, "events": [], "cpu_micro": None, "files": {}}

    monkeypatch.setattr(history, "take_sample", take)
    assert asyncio.run(history.sample_fleet(settings, sessions)) == 1
    assert [row.target_id for row in _samples(sessions)] == [quick]


def test_history_returns_samples_oldest_first_with_their_age(settings, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    now = utcnow()
    with sessions.begin() as session:
        for minutes, executes in ((50, 100), (20, 400), (90, 1)):
            history.store_sample(
                session, target_id,
                {"counters": {"execute count": executes}, "io": {}, "events": [], "cpu_micro": 7, "files": {"reads": 3}},
                now - timedelta(minutes=minutes),
            )
        session.add(Observation(
            target_id=target_id, metric_name=history.METRIC, timestamp=now - timedelta(minutes=5),
            value=None, value_str="not json", unit="json", labels={},
        ))
    with sessions() as session:
        samples = history.read_history(session, target_id, 60, now)
    assert [sample.counters[0].value for sample in samples] == [100, 400]
    assert [round(sample.age_seconds) for sample in samples] == [3000, 1200]
    assert samples[0].cpu_micro == 7
    assert samples[0].files.reads == 3
    assert samples[0].files.writes is None


def test_history_route_is_open_to_an_operator_and_does_not_read_oracle(settings, sessions, make_target, monkeypatch) -> None:
    target_id = make_target(alias="FINANCE")
    retired = make_target(alias="RETIRED", enabled=False)
    asyncio.run(history.sample_fleet(settings, sessions))

    async def refuse(*_args, **_kwargs):
        raise AssertionError("The history route must not read Oracle")

    monkeypatch.setattr(history, "take_sample", refuse)
    client = _client(settings, sessions)
    try:
        operator = client.get(f"/api/v1/databases/{target_id}/graph/history", headers=OPERATOR)
        viewer = client.get(
            f"/api/v1/databases/{target_id}/graph/history",
            headers={"X-Sentinel-Role": "viewer", "X-Sentinel-Principal": "vera"},
        )
        disabled = client.get(f"/api/v1/databases/{retired}/graph/history", headers=ADMIN)
        missing = client.get("/api/v1/databases/00000000-0000-0000-0000-000000000000/graph/history", headers=ADMIN)
        too_short = client.get(f"/api/v1/databases/{target_id}/graph/history?minutes=1", headers=ADMIN)
        too_long = client.get(f"/api/v1/databases/{target_id}/graph/history?minutes=5000", headers=ADMIN)
        unknown = client.get(
            f"/api/v1/databases/{target_id}/graph/history",
            headers={"X-Sentinel-Role": "guest", "X-Sentinel-Principal": "pat"},
        )
    finally:
        client.__exit__(None, None, None)
    assert operator.status_code == 200
    assert viewer.status_code == 200
    body = operator.json()
    assert len(body["samples"]) == 1
    sample = body["samples"][0]
    assert {item["name"]: item["value"] for item in sample["counters"]}["physical reads"] == 100
    assert sample["events"][1] == {
        "event": "log file sync", "wait_class": "Commit", "total_waits": 100, "time_waited_micro": 500000,
    }
    assert sample["files"] == {"reads": 100, "writes": 20, "read_time_cs": 40, "write_time_cs": 10}
    assert sample["age_seconds"] >= 0
    assert body["interval_seconds"] == 300
    assert body["enabled"] is False
    assert body["caption"].startswith("Stored history is off.")
    assert "select" not in operator.text.lower()
    assert "v$" not in operator.text.lower()
    assert (disabled.status_code, missing.status_code) == (404, 404)
    assert (too_short.status_code, too_long.status_code, unknown.status_code) == (422, 422, 400)


def test_caption_says_how_often_a_sample_is_taken(settings) -> None:
    running = settings.model_copy(update={"scheduler_enabled": True})
    assert history.caption(running, 0) == "No stored sample yet. One is taken every 5 minutes while the scheduler runs."
    assert history.caption(running, 3).startswith("Earlier points are stored samples taken every 5 minutes.")
    minute = running.model_copy(update={"graph_history_interval_seconds": 60})
    assert "every minute" in history.caption(minute, 1)
    off = running.model_copy(update={"graph_history_enabled": False})
    assert history.caption(off, 3).startswith("Stored history is off.")


def test_retention_removes_only_old_samples(settings, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    now = utcnow()
    with sessions.begin() as session:
        for hours in (30, 2):
            history.store_sample(session, target_id, {"counters": {"execute count": hours}}, now - timedelta(hours=hours))
        session.add(Observation(
            target_id=target_id, metric_name="storage.tablespace.used_pct", timestamp=now - timedelta(hours=30), value=61.0,
        ))
    assert history.delete_expired(settings, sessions, now) == 1
    with sessions() as session:
        left = session.scalars(select(Observation).order_by(Observation.timestamp)).all()
    assert [(row.metric_name, row.value) for row in left] == [
        ("storage.tablespace.used_pct", 61.0), ("activity.sample", None),
    ]


def test_metric_lists_leave_stored_samples_out_unless_asked_by_name(settings, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    now = utcnow()
    with sessions.begin() as session:
        history.store_sample(session, target_id, {"counters": {"execute count": 1}}, now)
        session.add(Observation(target_id=target_id, metric_name="storage.tablespace.used_pct", timestamp=now, value=61.0))
    start, end = now - timedelta(hours=1), now + timedelta(hours=1)
    with sessions() as session:
        everything = metric_history(session, target_id, start, end, None, None, 100)
        named = metric_history(session, target_id, start, end, "activity.sample", None, 100)
    assert [row.metric_name for row in everything] == ["storage.tablespace.used_pct"]
    assert [row.metric_name for row in named] == ["activity.sample"]


def test_the_timer_samples_when_due_and_follows_a_changed_interval(settings, sessions, make_target, monkeypatch) -> None:
    make_target(alias="FINANCE")
    sampler = GraphHistorySampler(settings, sessions)
    try:
        assert sampler.tick() == 1
        assert sampler.tick() == 0
        assert len(_samples(sessions)) == 1
        sampler.last_run = utcnow() - timedelta(seconds=290)
        assert sampler.tick() == 1
        sampler.last_run = utcnow() - timedelta(seconds=120)
        assert sampler.tick() == 0
        settings.graph_history_interval_seconds = 60
        assert sampler.tick() == 1
        settings.graph_history_enabled = False
        sampler.last_run = None
        assert sampler.tick() == 0
        assert len(_samples(sessions)) == 3

        async def broken(*_args, **_kwargs):
            raise RuntimeError("driver text that must not escape")

        settings.graph_history_enabled = True
        monkeypatch.setattr("sentinel.graph.sampler.sample_fleet", broken)
        assert sampler.tick() == 0
    finally:
        sampler.stop()


def test_settings_are_in_admin_and_checked(settings, sessions) -> None:
    with sessions.begin() as session:
        sync_public_config(session, settings)
        items = {item["key"]: item for item in list_public_config(session) if item["section"] == "Graph history"}
        assert set(items) == {
            "graph_history_enabled", "graph_history_interval_seconds", "graph_history_retention_hours",
        }
        assert all(item["editable"] for item in items.values())
        assert (items["graph_history_enabled"]["value"], items["graph_history_interval_seconds"]["value"]) == (True, 300)
        assert update_public_config(session, settings, "graph_history_interval_seconds", 120) == 120
        assert settings.graph_history_interval_seconds == 120
        for key, value in (
            ("graph_history_interval_seconds", 5), ("graph_history_retention_hours", 0), ("graph_history_retention_hours", 500),
        ):
            try:
                update_public_config(session, settings, key, value)
            except Exception as exc:  # noqa: BLE001
                assert type(exc).__name__ == "ConfigUpdateError"
            else:
                raise AssertionError(f"{key}={value} was accepted")
