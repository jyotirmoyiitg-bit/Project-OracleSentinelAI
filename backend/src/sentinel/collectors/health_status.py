"""Health derived from fresh evidence, never from connection success alone."""

from datetime import datetime

from sentinel.collectors.normalization import PROBES
from sentinel.collectors.profiles import stale_after
from sentinel.config.settings import Settings
from sentinel.persistence.models import CollectorCheckpoint, CurrentObservation, DatabaseTarget


def is_fresh(
    checkpoint: CollectorCheckpoint | None, target: DatabaseTarget, settings: Settings, now: datetime,
) -> bool:
    if checkpoint is None or checkpoint.last_success_at is None or checkpoint.status == "failed":
        return False
    return (now - checkpoint.last_success_at).total_seconds() <= stale_after(
        settings, target.poll_profile, target.environment, checkpoint.collector_type,
    )


def compute_health(
    target: DatabaseTarget, checkpoints: list[CollectorCheckpoint],
    observations: list[CurrentObservation], settings: Settings, now: datetime,
) -> str:
    if not target.enabled:
        return "unknown"
    by_probe = {checkpoint.collector_type: checkpoint for checkpoint in checkpoints}
    connection = by_probe.get("connectivity")
    if connection and connection.status == "failed" and connection.error_class in {
        "ConnectionError", "DatabaseError", "OperationalError", "InterfaceError", "TimeoutError",
    } and connection.last_attempt_at and (
        now - connection.last_attempt_at
    ).total_seconds() <= stale_after(settings, target.poll_profile, target.environment, "connectivity"):
        return "offline"
    if any(not is_fresh(by_probe.get(probe), target, settings, now) for probe in PROBES):
        return "unknown"
    if any(obs.quality == "stale" or (now - obs.timestamp).total_seconds() > stale_after(
        settings, target.poll_profile, target.environment, obs.probe_type,
    ) for obs in observations):
        return "unknown"
    severity = 0
    for obs in observations:
        if obs.quality == "stale" or obs.value is None and not obs.value_str:
            continue
        if obs.metric_name in {"storage.tablespace.used_pct", "storage.fra.used_pct", "resources.used_pct"} and (
            obs.value is not None
        ):
            severity = max(severity, 2 if obs.value > 95 else 1 if obs.value > 85 else 0)
        if obs.metric_name == "instance.status" and obs.value_str not in {"OPEN", "MOUNTED"}:
            severity = max(severity, 2)
        if obs.metric_name == "storage.archive.dest_status" and obs.value_str in {"ERROR", "FULL", "DISABLED"}:
            severity = max(severity, 2)
        if obs.metric_name == "storage.archive.error" and obs.value_str:
            severity = max(severity, 2)
    return ("healthy", "warning", "critical")[severity]
