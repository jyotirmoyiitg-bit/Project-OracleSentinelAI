"""Incident Center and notification log endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from sentinel.api.v1.schemas import (
    IncidentActionRequest,
    IncidentClearResponse,
    IncidentDetailResponse,
    IncidentListResponse,
    IncidentResponse,
    IncidentState,
    NotificationResponse,
    PortalNotificationListResponse,
    PortalNotificationResponse,
)
from sentinel.auth.rbac import PermissionDenied, require
from sentinel.incidents.lifecycle import IncidentLifecycle, InvalidTransitionError
from sentinel.knowledge.acl import Caller, caller_from_headers
from sentinel.observability.audit import record_action
from sentinel.persistence.models import Incident, IncidentEvent, Notification
from sentinel.persistence.session import get_session

router = APIRouter(prefix="/api/v1", tags=["incidents"])

SessionDep = Annotated[Session, Depends(get_session)]


def _caller(request: Request) -> Caller:
    existing = getattr(request.state, "caller", None)
    if isinstance(existing, Caller):
        return existing
    try:
        return caller_from_headers(request.headers.get("X-Sentinel-Role"), request.headers.get("X-Sentinel-Principal"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _allow_admin(request: Request) -> Caller:
    caller = _caller(request)
    try:
        require(caller, "platform.read")
    except PermissionDenied as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return caller


def _allow_incident_action(request: Request) -> Caller:
    """Acknowledging or resolving needs incident.acknowledge. A viewer or auditor may only read."""
    caller = _caller(request)
    try:
        require(caller, "incident.acknowledge")
    except PermissionDenied as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return caller


def _actor(caller: Caller, supplied: str) -> str:
    """The signed-in operator is the actor. A name in the request body is used only when sign-in is off."""
    return caller.principal.strip() or supplied


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
        select(Incident).options(joinedload(Incident.target)).where(*filters)
        .order_by(Incident.last_seen.desc())
        .offset((page - 1) * page_size).limit(page_size),
    ))
    return IncidentListResponse(
        items=[_named(item) for item in items], total=total, page=page, page_size=page_size,
    )


def _named(incident: Incident) -> IncidentResponse:
    response = IncidentResponse.model_validate(incident)
    if not response.target_name and incident.target is not None:
        response.target_name = incident.target.display_name
    return response


@router.delete("/incidents", response_model=IncidentClearResponse)
def clear_incidents(request: Request, session: SessionDep) -> IncidentClearResponse:
    """Remove every incident record. Oracle is not changed."""
    caller = _allow_admin(request)
    deleted = session.scalar(select(func.count()).select_from(Incident)) or 0
    session.execute(delete(Notification))
    session.execute(delete(IncidentEvent))
    session.execute(delete(Incident))
    record_action(session, caller.principal, "incident.clear", "incidents", "cleared", {"deleted": deleted})
    session.commit()
    return IncidentClearResponse(deleted=deleted)


@router.get("/incidents/{incident_id}", response_model=IncidentDetailResponse)
def get_incident(incident_id: str, session: SessionDep) -> IncidentDetailResponse:
    incident = find_incident(session, incident_id)
    return IncidentDetailResponse.model_validate(incident)


@router.post("/incidents/{incident_id}/acknowledge", response_model=IncidentResponse)
def acknowledge_incident(
    incident_id: str, body: IncidentActionRequest, request: Request, session: SessionDep,
) -> Incident:
    caller = _allow_incident_action(request)
    find_incident(session, incident_id)
    actor = _actor(caller, body.actor)
    try:
        incident = IncidentLifecycle.acknowledge(session, uuid.UUID(incident_id), actor, body.reason)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_action(session, actor, "incident.acknowledge", str(incident.id), "acknowledged", {"reason": bool(body.reason)})
    session.commit()
    return incident


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentResponse)
def resolve_incident(
    incident_id: str, body: IncidentActionRequest, request: Request, session: SessionDep,
) -> Incident:
    caller = _allow_incident_action(request)
    find_incident(session, incident_id)
    actor = _actor(caller, body.actor)
    try:
        incident = IncidentLifecycle.resolve(session, uuid.UUID(incident_id), actor, body.reason)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_action(session, actor, "incident.resolve", str(incident.id), "resolved", {"reason": bool(body.reason)})
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
