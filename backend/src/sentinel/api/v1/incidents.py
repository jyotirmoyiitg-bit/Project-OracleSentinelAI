"""Incident Center and notification log endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from sentinel.api.v1.schemas import (
    IncidentActionRequest,
    IncidentDetailResponse,
    IncidentListResponse,
    IncidentResponse,
    IncidentState,
    NotificationResponse,
    PortalNotificationListResponse,
    PortalNotificationResponse,
)
from sentinel.incidents.lifecycle import IncidentLifecycle, InvalidTransitionError
from sentinel.persistence.models import Incident, Notification
from sentinel.persistence.session import get_session

router = APIRouter(prefix="/api/v1", tags=["incidents"])

SessionDep = Annotated[Session, Depends(get_session)]


def find_incident(session: Session, incident_id: str) -> Incident:
    try:
        iid = uuid.UUID(incident_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from exc
    incident = session.get(Incident, iid)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@router.get("/incidents", response_model=IncidentListResponse)
def list_incidents(
    session: SessionDep,
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    state: IncidentState | None = None, severity: str | None = None, owner: str | None = None,
    target_id: str | None = None,
) -> IncidentListResponse:
    filters = []
    if state:
        filters.append(Incident.state == state)
    if severity:
        filters.append(Incident.severity == severity)
    if owner:
        filters.append(Incident.owner == owner)
    if target_id:
        try:
            filters.append(Incident.target_id == uuid.UUID(target_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid target_id") from exc
    total = session.scalar(select(func.count()).select_from(Incident).where(*filters)) or 0
    items = list(session.scalars(
        select(Incident).where(*filters).order_by(Incident.last_seen.desc())
        .offset((page - 1) * page_size).limit(page_size),
    ))
    return IncidentListResponse(
        items=[IncidentResponse.model_validate(item) for item in items], total=total, page=page, page_size=page_size,
    )


@router.get("/incidents/{incident_id}", response_model=IncidentDetailResponse)
def get_incident(incident_id: str, session: SessionDep) -> IncidentDetailResponse:
    incident = find_incident(session, incident_id)
    return IncidentDetailResponse.model_validate(incident)


@router.post("/incidents/{incident_id}/acknowledge", response_model=IncidentResponse)
def acknowledge_incident(incident_id: str, body: IncidentActionRequest, session: SessionDep) -> Incident:
    find_incident(session, incident_id)
    try:
        incident = IncidentLifecycle.acknowledge(session, uuid.UUID(incident_id), body.actor, body.reason)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return incident


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentResponse)
def resolve_incident(incident_id: str, body: IncidentActionRequest, session: SessionDep) -> Incident:
    find_incident(session, incident_id)
    try:
        incident = IncidentLifecycle.resolve(session, uuid.UUID(incident_id), body.actor, body.reason)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return incident


@router.get("/notifications", response_model=list[NotificationResponse])
def list_notifications(
    session: SessionDep, incident_id: str | None = None, status_filter: str | None = Query(None, alias="status"),
    channel: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
) -> list[Notification]:
    filters = []
    if incident_id:
        try:
            filters.append(Notification.incident_id == uuid.UUID(incident_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid incident_id") from exc
    if status_filter:
        filters.append(Notification.status == status_filter)
    if channel:
        filters.append(Notification.channel == channel)
    return list(session.scalars(
        select(Notification).where(*filters).order_by(Notification.created_at.desc()).limit(limit),
    ))


def portal_response(notification: Notification) -> PortalNotificationResponse:
    return PortalNotificationResponse.model_validate({
        "id": notification.id, "incident_id": notification.incident_id,
        "target_id": notification.incident.target_id, "target_name": notification.incident.target.display_name,
        "severity": notification.incident.severity, "message": notification.incident.message,
        "status": notification.status, "created_at": notification.created_at,
    })


@router.get("/portal-notifications", response_model=PortalNotificationListResponse)
def list_portal_notifications(
    session: SessionDep, page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100),
) -> PortalNotificationListResponse:
    total = session.scalar(select(func.count()).select_from(Notification).where(Notification.channel == "web")) or 0
    unread_count = session.scalar(select(func.count()).select_from(Notification).where(
        Notification.channel == "web", Notification.status == "unread",
    )) or 0
    items = session.scalars(
        select(Notification).options(joinedload(Notification.incident).joinedload(Incident.target))
        .where(Notification.channel == "web").order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset((page - 1) * page_size).limit(page_size),
    )
    return PortalNotificationListResponse(
        items=[portal_response(item) for item in items], total=total,
        unread_count=unread_count, page=page, page_size=page_size,
    )


@router.post("/portal-notifications/{notification_id}/read", response_model=PortalNotificationResponse)
def read_portal_notification(notification_id: str, session: SessionDep) -> PortalNotificationResponse:
    try:
        nid = uuid.UUID(notification_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from exc
    notification = session.scalar(
        select(Notification).options(joinedload(Notification.incident).joinedload(Incident.target))
        .where(Notification.id == nid, Notification.channel == "web"),
    )
    if notification is None:
        raise HTTPException(status_code=404, detail="Portal notification not found")
    notification.status = "read"
    session.commit()
    return portal_response(notification)
