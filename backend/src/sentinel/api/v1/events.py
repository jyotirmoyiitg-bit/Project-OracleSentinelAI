"""Oracle alert-log timeline endpoint, with linked incidents per event."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.api.v1.databases import find_target, runtime_settings, time_window
from sentinel.api.v1.schemas import DiagnosticEventResponse, IncidentResponse
from sentinel.config.settings import Settings
from sentinel.persistence.models import DiagnosticEvent, Incident
from sentinel.persistence.session import get_session

router = APIRouter(prefix="/api/v1/databases", tags=["events"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(runtime_settings)]


class EventWithIncidents(DiagnosticEventResponse):
    incidents: list[IncidentResponse] = []


@router.get("/{target_id}/events", response_model=list[EventWithIncidents])
def get_alert_log_events(
    target_id: str, session: SessionDep, settings: SettingsDep,
    start: datetime | None = None, end: datetime | None = None,
    code: str | None = Query(None, max_length=50), severity: str | None = None,
    search: str | None = Query(None, max_length=255, description="Full-text search within the message"),
    limit: int = Query(100, ge=1, le=1000),
) -> list[EventWithIncidents]:
    target = find_target(session, target_id)
    begin, stop = time_window(start, end, settings)
    stmt = select(DiagnosticEvent).where(
        DiagnosticEvent.target_id == target.id,
        DiagnosticEvent.occurred_at >= begin, DiagnosticEvent.occurred_at <= stop,
    )
    if code:
        stmt = stmt.where(DiagnosticEvent.code == code)
    if severity:
        stmt = stmt.where(DiagnosticEvent.severity == severity)
    if search:
        stmt = stmt.where(DiagnosticEvent.message.ilike(f"%{search}%"))
    events = list(session.scalars(stmt.order_by(DiagnosticEvent.occurred_at.desc()).limit(limit)))
    incidents_by_rule = {
        incident.rule_id: incident for incident in session.scalars(
            select(Incident).where(Incident.target_id == target.id),
        )
    }
    results = []
    for event in events:
        linked = [incidents_by_rule[event.code]] if event.code and event.code in incidents_by_rule else []
        payload = EventWithIncidents.model_validate(event)
        payload.incidents = [IncidentResponse.model_validate(incident) for incident in linked]
        results.append(payload)
    return results
