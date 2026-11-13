"""Read the append-only audit trail. There is no update or delete route."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.auth.rbac import PermissionDenied, require
from sentinel.knowledge.acl import Caller, caller_from_headers
from sentinel.persistence.models import AuditEvent

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


def _factory(request: Request) -> sessionmaker[Session]:
    factory = getattr(request.app.state, "session_factory", None)
    if not callable(getattr(factory, "begin", None)):
        raise HTTPException(status_code=503, detail="Database is not initialized")
    return cast(sessionmaker[Session], factory)


def _caller(request: Request) -> Caller:
    existing = getattr(request.state, "caller", None)
    if isinstance(existing, Caller):
        return existing
    try:
        return caller_from_headers(request.headers.get("X-Sentinel-Role"), request.headers.get("X-Sentinel-Principal"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/events")
def list_events(request: Request, caller: Annotated[Caller, Depends(_caller)]) -> dict[str, object]:
    try:
        require(caller, "audit.read")
    except PermissionDenied as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    with _factory(request).begin() as session:
        rows = session.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(200)).all()
        return {
            "items": [
                {
                    "id": str(row.id),
                    "occurred_at": row.occurred_at.isoformat(),
                    "principal": row.principal,
                    "service": row.service,
                    "action": row.action,
                    "target": row.target,
                    "correlation_id": row.correlation_id,
                    "outcome": row.outcome,
                    "evidence_digest": row.evidence_digest,
                }
                for row in rows
            ],
        }
