"""Dashboard projections from latest values, not historical table scans."""

from datetime import datetime

from sqlalchemy import case
from sqlalchemy.sql.elements import Case

from sentinel.api.v1.schemas import (
    ArchiveSummary,
    ComplianceSummary,
    DatabaseDetailResponse,
    DatabaseTargetResponse,
    FraSummary,
    ObservationResponse,
    PollStatus,
    TablespaceSummary,
)
from sentinel.collectors.health_status import compute_health, is_fresh
from sentinel.collectors.normalization import PROBES
from sentinel.config.settings import Settings
from sentinel.persistence.models import CollectorCheckpoint, CurrentObservation, DatabaseTarget


def effective_status(now: datetime) -> Case[str]:
    return case(
        (DatabaseTarget.enabled.is_(False), "unknown"),
        (DatabaseTarget.health_valid_until.is_(None), "unknown"),
        (DatabaseTarget.health_valid_until < now, "unknown"),
        else_=DatabaseTarget.last_health_status,
    )


def observation_response(
    obs: CurrentObservation, points: list[CollectorCheckpoint], target: DatabaseTarget,
    settings: Settings, now: datetime,
) -> ObservationResponse:
    checkpoint = next((point for point in points if point.collector_type == obs.probe_type), None)
    return ObservationResponse(
        id=obs.observation_id, target_id=obs.target_id, metric_name=obs.metric_name,
        timestamp=obs.timestamp, value=obs.value, value_str=obs.value_str, unit=obs.unit,
        labels=obs.labels, quality=obs.quality if is_fresh(checkpoint, target, settings, now) else "stale",
        source=obs.source,
    )


def target_response(
    target: DatabaseTarget, points: list[CollectorCheckpoint], current: list[CurrentObservation],
    settings: Settings, now: datetime,
) -> DatabaseTargetResponse:
    result = DatabaseTargetResponse.model_validate(target)
    result.last_health_status = compute_health(target, points, current, settings, now)
    if target.health_valid_until is None or target.health_valid_until < now:
        result.last_health_status = "unknown"
    result.monitoring_status = "disabled" if not target.enabled else result.last_health_status
    result.trend_snapshots = [
        observation_response(obs, points, target, settings, now) for obs in current
        if obs.metric_name in {"storage.tablespace.used_pct", "storage.fra.used_pct", "resources.used_pct"}
    ]
    return result


def detail_response(
    target: DatabaseTarget, points: list[CollectorCheckpoint], current: list[CurrentObservation],
    settings: Settings, now: datetime,
) -> DatabaseDetailResponse:
    latest = [observation_response(obs, points, target, settings, now) for obs in current]
    spaces: dict[str, TablespaceSummary] = {}
    fra = FraSummary()
    archive = ArchiveSummary()
    for obs in latest:
        if obs.metric_name.startswith("storage.tablespace."):
            name = obs.labels.get("tablespace_name", "unknown")
            space = spaces.setdefault(name, TablespaceSummary(name=name, timestamp=obs.timestamp))
            if obs.metric_name.endswith("used_pct"):
                space.used_pct = obs.value
            elif obs.metric_name.endswith("bytes_total"):
                space.size_bytes = obs.value
            elif obs.metric_name.endswith("autoextend"):
                space.autoextend = bool(obs.value) if obs.value is not None else None
        if obs.metric_name == "storage.fra.space_limit":
            fra.size_bytes = obs.value
        elif obs.metric_name == "storage.fra.space_used":
            fra.used_bytes = obs.value
        elif obs.metric_name == "storage.fra.used_pct":
            fra.used_pct = obs.value
        elif obs.metric_name == "storage.fra.usage_by_type":
            fra.breakdown.append(obs)
        elif obs.metric_name == "database.log_mode":
            archive.log_mode = obs.value_str
        elif obs.metric_name.startswith("storage.archive."):
            archive.destinations.append(obs)
    statuses = []
    for probe in PROBES:
        point = next((point for point in points if point.collector_type == probe), None)
        statuses.append(PollStatus(
            probe_type=probe,
            status=(point.status if point and (is_fresh(point, target, settings, now) or point.status == "failed")
                    else "stale" if point and point.last_success_at else "pending"),
            last_success_at=point.last_success_at if point else None,
            last_attempt_at=point.last_attempt_at if point else None,
            error_class=point.error_class if point else None,
        ))
    return DatabaseDetailResponse(
        **target_response(target, points, current, settings, now).model_dump(),
        latest_observations=latest, poll_status=statuses, compliance=ComplianceSummary(),
        tablespaces=sorted(spaces.values(), key=lambda space: space.name), fra=fra, archive=archive,
    )
