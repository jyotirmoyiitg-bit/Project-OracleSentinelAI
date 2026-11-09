import asyncio
import uuid
from datetime import timedelta
from threading import Event
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import func, inspect, select, update

from sentinel.adapters.oracle.capabilities import OracleCapabilityDetector
from sentinel.adapters.oracle.connection import OracleConnectionManager
from sentinel.collectors import checkpoints
from sentinel.collectors.health_status import compute_health
from sentinel.collectors.normalization import PROBES, normalize, utcnow
from sentinel.collectors.profiles import interval_for
from sentinel.collectors.scheduler import CollectorScheduler
from sentinel.domain.models import Observation
from sentinel.persistence.models import (
    CollectorCheckpoint,
    CurrentObservation,
    DatabaseTarget,
    DiagnosticEvent,
    PollRun,
)
from sentinel.persistence.models import (
    Observation as StoredObservation,
)
from sentinel.persistence.telemetry import checkpoints_for, current_for


@pytest.mark.parametrize(("alias", "expected"), [
    ("SYNTHETIC_HEALTHY", "healthy"), ("SYNTHETIC_CRITICAL", "critical"),
    ("SYNTHETIC_OFFLINE", "offline"), ("SYNTHETIC_DENIED", "unknown"),
])
def test_synthetic_pipeline(runner, sessions, make_target, settings, alias, expected):
    target_id = make_target(alias)
    for probe in PROBES:
        runner.run(target_id, probe)
    with sessions() as session:
        target = session.get(DatabaseTarget, target_id)
        assert target.last_health_status == expected
        assert compute_health(target, checkpoints_for(session, [target_id]), current_for(session, [target_id]),
                              settings, utcnow()) == expected
        assert session.scalar(select(func.count()).select_from(PollRun)) == 6
        if expected in {"healthy", "critical"}:
            assert len(current_for(session, [target_id])) > 10
            point = checkpoints.get_checkpoint(session, target_id, "storage")
            assert point.cursor["version"] == 1
            assert point.lease_expires_at is None
        else:
            assert session.scalar(select(func.count()).select_from(DiagnosticEvent)) > 0


def test_stale_evidence_unknown_and_recovery_persists(runner, sessions, make_target, settings):
    target_id = make_target()
    for probe in PROBES:
        assert runner.run(target_id, probe) == "success"
    with sessions.begin() as session:
        session.execute(update(CollectorCheckpoint).where(
            CollectorCheckpoint.collector_type == "storage",
        ).values(last_success_at=utcnow() - timedelta(days=1)))
    with sessions() as session:
        assert compute_health(session.get(DatabaseTarget, target_id), checkpoints_for(session, [target_id]),
                              current_for(session, [target_id]), settings, utcnow()) == "unknown"
    assert runner.run(target_id, "storage") == "success"
    with sessions() as session:
        assert checkpoints.get_checkpoint(session, target_id, "storage").lease_version == 2
        assert session.get(DatabaseTarget, target_id).last_health_status == "healthy"


def test_missing_connection_configuration_unknown_not_offline(runner, sessions, make_target):
    target_id = make_target()
    with sessions.begin() as session:
        session.get(DatabaseTarget, target_id).tns_alias = None
    assert runner.run(target_id, "connectivity") == "failed"
    with sessions() as session:
        assert session.get(DatabaseTarget, target_id).last_health_status == "unknown"


def test_failed_measurement_preserves_last_good_checkpoint(runner, sessions, make_target):
    target_id = make_target()
    runner.run(target_id, "storage")
    with sessions() as session:
        cursor = checkpoints.get_checkpoint(session, target_id, "storage").cursor
    with patch.object(runner, "adapters", side_effect=PermissionError("fixture")):
        assert runner.run(target_id, "storage") == "failed"
    with sessions() as session:
        point = checkpoints.get_checkpoint(session, target_id, "storage")
        assert point.cursor == cursor
        assert point.error_class == "PermissionError"
        assert len(current_for(session, [target_id])) == 3


def test_inflight_old_configuration_cannot_commit(runner, sessions, make_target):
    target_id = make_target()
    original = runner.adapters

    def change_target(target):
        with sessions.begin() as session:
            session.get(DatabaseTarget, target_id).configuration_version += 1
        return original(target)

    runner.adapters = change_target
    assert runner.run(target_id, "storage") == "obsolete"
    with sessions() as session:
        assert not current_for(session, [target_id])
        assert session.scalar(select(PollRun.status)) == "obsolete"


def test_leases_deduplicate_and_fence_expired_worker(sessions, make_target):
    target_id, now = make_target(), utcnow()
    with sessions.begin() as session:
        assert checkpoints.claim(session, target_id, "storage", now, 60) == 1
    with sessions.begin() as session:
        assert checkpoints.claim(session, target_id, "storage", now, 60) is None
    with sessions.begin() as session:
        assert checkpoints.claim(session, target_id, "storage", now + timedelta(seconds=61), 60) == 2
        assert not checkpoints.finish(session, target_id, "storage", 1, now + timedelta(seconds=62), None)
        assert checkpoints.finish(session, target_id, "storage", 2, now + timedelta(seconds=62), None)


def test_crash_recovery_marks_prior_run_interrupted(runner, sessions, make_target):
    target_id, now = make_target(), utcnow()
    with sessions.begin() as session:
        checkpoints.claim(session, target_id, "storage", now - timedelta(seconds=90), 60)
        session.add(PollRun(
            target_id=target_id, probe_type="storage", started_at=now - timedelta(seconds=90),
            status="running", lease_version=1,
        ))
    assert runner.run(target_id, "storage") == "success"
    with sessions() as session:
        assert set(session.scalars(select(PollRun.status))) == {"interrupted", "success"}


def test_runner_replaces_latest_but_retains_history(runner, sessions, make_target):
    target_id = make_target()
    runner.run(target_id, "storage")
    runner.run(target_id, "storage")
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(CurrentObservation)) == 3
        assert session.scalar(select(func.count()).select_from(StoredObservation)) == 6


def test_retention_preserves_current_snapshot(runner, sessions, make_target):
    target_id = make_target()
    runner.run(target_id, "storage")
    with sessions.begin() as session:
        session.execute(update(StoredObservation).values(timestamp=utcnow() - timedelta(days=31)))
    runner.retain()
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(StoredObservation)) == 0
        assert session.scalar(select(func.count()).select_from(CurrentObservation)) == 3


def test_normalization_and_invalid_measurements():
    result = normalize([Observation(
        "alias", "storage.tablespace.used_pct", 87, unit="%", labels={"tablespace": "USERS", "source": "query"},
    )], str(uuid.uuid4()), "storage")[0]
    assert result.unit == "percent"
    assert result.labels["tablespace_name"] == "USERS"
    assert result.source == "query"
    with pytest.raises(ValueError, match="Non-finite"):
        normalize([Observation("alias", "storage.used_pct", float("nan"))], "id", "storage")
    duplicate = Observation("alias", "storage.used_pct", 10)
    with pytest.raises(ValueError, match="Duplicate observation"):
        normalize([duplicate, duplicate], "id", "storage")


@pytest.mark.parametrize(("value", "expected"), [(85, "healthy"), (85.01, "warning"), (95, "warning"), (95.01, "critical")])
def test_exact_health_thresholds(value, expected, runner, sessions, make_target, settings):
    target_id = make_target()
    for probe in PROBES:
        runner.run(target_id, probe)
    with sessions.begin() as session:
        session.execute(update(CurrentObservation).where(
            CurrentObservation.metric_name == "storage.tablespace.used_pct",
        ).values(value=value))
    with sessions() as session:
        assert compute_health(session.get(DatabaseTarget, target_id), checkpoints_for(session, [target_id]),
                              current_for(session, [target_id]), settings, utcnow()) == expected


def test_scheduler_dedup_profiles_and_disabled_cleanup(runner, sessions, make_target, settings):
    target_id = make_target(poll_profile={"name": "lightweight"})
    service = CollectorScheduler(runner)
    service.reconcile()
    service.reconcile()
    assert len(service.scheduler.get_jobs()) == 6
    assert service.scheduler.get_job(f"poll:{target_id}:storage").trigger.interval.total_seconds() == 600
    with sessions.begin() as session:
        session.get(DatabaseTarget, target_id).enabled = False
    service.reconcile()
    assert not service.scheduler.get_jobs()
    assert interval_for(settings, {}, "production", "connectivity") == 30


def test_scheduler_restart_recovers_due_time_from_checkpoint(runner, make_target):
    target_id = make_target()
    runner.run(target_id, "storage")
    restarted = CollectorScheduler(runner)
    restarted.reconcile()
    job = restarted.scheduler.get_job(f"poll:{target_id}:storage")
    assert 290 < (job.next_run_time.replace(tzinfo=None) - utcnow()).total_seconds() <= 300


def test_offline_status_expires_to_unknown(runner, sessions, make_target, settings):
    target_id = make_target("SYNTHETIC_OFFLINE")
    runner.run(target_id, "connectivity")
    with sessions() as session:
        target = session.get(DatabaseTarget, target_id)
        assert compute_health(target, checkpoints_for(session, [target_id]), [], settings,
                              utcnow() + timedelta(seconds=181)) == "unknown"


def test_scheduler_runs_and_gracefully_stops(runner, make_target):
    make_target()
    called = Event()
    runner.run = Mock(side_effect=lambda *_: called.set())
    service = CollectorScheduler(runner)
    service.start()
    try:
        assert called.wait(timeout=5)
    finally:
        service.stop()
    assert not service.scheduler.running


def test_schema_has_history_indexes(sessions):
    with sessions() as session:
        indexes = inspect(session.get_bind()).get_indexes("observations")
        assert {"ix_observations_target_time", "ix_observations_target_metric_time"} <= {i["name"] for i in indexes}


def test_oracle_connection_uses_thin_mode_and_bounded_timeout(settings):
    settings.tns_admin = r"C:\test\tns"
    connection = Mock()
    with patch("sentinel.adapters.oracle.connection.oracledb.connect", return_value=connection) as connect:
        manager = OracleConnectionManager(settings)
        result = asyncio.run(manager.connect("ALIAS", password="fixture-only"))
    assert result is connection
    assert connect.call_args.kwargs["dsn"] == "ALIAS"
    assert connect.call_args.kwargs["tcp_connect_timeout"] == 30
    assert connection.call_timeout == 60000
    asyncio.run(manager.close_connection(connection))
    connection.close.assert_called_once()


def test_capability_detection_sets_real_fields_and_preserves_oracle_sql():
    detector = OracleCapabilityDetector()
    cursor = Mock()
    connection = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchone.return_value = None
    caps = asyncio.run(detector.detect(connection))
    assert caps.has_select_v_instance is True
    assert caps.to_dict()["has_select_v$instance"] is True
    assert any("v$instance" in call.args[0] for call in cursor.execute.call_args_list)
