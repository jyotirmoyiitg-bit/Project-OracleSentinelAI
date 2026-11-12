"""Database health and recent diagnostic events from collected telemetry."""

import uuid

from sqlalchemy import select

from sentinel.ai.safety import event_code, event_severity, sanitize_untrusted
from sentinel.ai.tools.context import ToolContext
from sentinel.ai.types import EvidenceCitation, ToolOutcome
from sentinel.persistence.models import CurrentObservation, DatabaseTarget, DiagnosticEvent, Incident


def database_health(context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    del arguments
    target = _target(context)
    rows = context.session.scalars(
        select(CurrentObservation).where(CurrentObservation.target_id == target.id).limit(20),
    ).all()
    metrics = [_metric(row) for row in rows]
    open_incidents = context.session.scalars(
        select(Incident).where(Incident.target_id == target.id, Incident.state != "resolved").limit(10),
    ).all()
    summary = f"{target.display_name} health is {target.last_health_status or 'unknown'}."
    citations = [
        EvidenceCitation("metric", target.display_name, "database_targets.last_health_status", summary, str(target.id)),
    ]
    citations.extend(
        EvidenceCitation("metric", item["metric_name"], item["metric_name"], item["detail"], str(target.id))
        for item in metrics[:8]
    )
    return ToolOutcome(
        "get_database_health", "allowed", summary, citations,
        {"health": target.last_health_status, "metrics": metrics, "open_incidents": len(open_incidents)},
    )


def recent_events(context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    target = _target(context)
    code = event_code(_text(arguments.get("code")))
    severity = event_severity(_text(arguments.get("severity")))
    statement = select(DiagnosticEvent).where(DiagnosticEvent.target_id == target.id)
    if code:
        statement = statement.where(DiagnosticEvent.code == code)
    if severity:
        statement = statement.where(DiagnosticEvent.severity == severity)
    rows = context.session.scalars(statement.order_by(DiagnosticEvent.occurred_at.desc()).limit(20)).all()
    events: list[dict[str, str]] = [
        {
            "id": str(row.id),
            "code": row.code or "",
            "severity": row.severity or "",
            "message": sanitize_untrusted(row.message or "", 300),
            "occurred_at": row.occurred_at.isoformat() if row.occurred_at else "",
        }
        for row in rows
    ]
    summary = f"{len(events)} recent diagnostic events for {target.display_name}."
    citations = [
        EvidenceCitation("event", item["code"] or "event", item["id"], item["message"], str(target.id))
        for item in events[:8]
    ]
    return ToolOutcome("get_events", "allowed", summary, citations, {"events": events})


def _target(context: ToolContext) -> DatabaseTarget:
    if context.target_id is None:
        raise ValueError("database scope is required")
    target = context.session.get(DatabaseTarget, context.target_id)
    if target is None or not target.enabled:
        raise ValueError("database is not available")
    return target


def _metric(row: CurrentObservation) -> dict[str, str]:
    value = row.value_str if row.value is None else str(row.value)
    label = ""
    if isinstance(row.labels, dict):
        label = str(row.labels.get("tablespace_name") or row.labels.get("destination") or "")
    detail = sanitize_untrusted(f"{row.metric_name} {label} {value or ''} {row.unit or ''}".strip(), 240)
    return {"metric_name": row.metric_name, "value": value or "", "detail": detail}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def parse_uuid(value: object) -> uuid.UUID | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
