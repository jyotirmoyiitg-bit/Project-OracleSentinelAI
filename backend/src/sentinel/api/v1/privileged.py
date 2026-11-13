"""Privileged operation endpoints. Release 1 returns 501 and does not execute."""

import uuid
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session, sessionmaker

from sentinel.auth.rbac import PermissionDenied
from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller, caller_from_headers
from sentinel.operations.privileged.disabled import PrivilegedDisabled, reject_privileged

router = APIRouter(prefix="/api/v1/operations/privileged", tags=["privileged-operations"])


def _settings(request: Request) -> Settings:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise HTTPException(status_code=503, detail="Application settings are not initialized")
    return settings


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


def _refuse(request: Request, target: str, permission: str) -> None:
    settings = _settings(request)
    caller = _caller(request)
    failure: PermissionDenied | PrivilegedDisabled | None = None
    with _factory(request).begin() as session:
        try:
            reject_privileged(session, settings, caller, target, permission=permission)
        except (PermissionDenied, PrivilegedDisabled) as exc:
            failure = exc
    if isinstance(failure, PermissionDenied):
        raise HTTPException(status_code=failure.status_code, detail=failure.message)
    if isinstance(failure, PrivilegedDisabled):
        raise HTTPException(status_code=failure.status_code, detail=failure.message)


@router.get("")
def list_privileged(request: Request) -> None:
    """The list is refused. A query parameter cannot turn execution on."""
    _refuse(request, "list", "privileged.read")


@router.post("/{proposal_id}/execute")
def execute_privileged(proposal_id: uuid.UUID, request: Request) -> None:
    """Refuse execution. No statement is sent to Oracle."""
    _refuse(request, str(proposal_id), "privileged.execute")
