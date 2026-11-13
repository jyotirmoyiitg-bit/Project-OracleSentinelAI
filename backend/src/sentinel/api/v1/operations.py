"""Proposal endpoints. Creating or approving a proposal never connects to Oracle."""

import uuid
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from sentinel.auth.rbac import PermissionDenied
from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller, caller_from_headers
from sentinel.operations.approval import decide
from sentinel.operations.proposals import (
    create_proposal,
    get_proposal,
    list_proposals,
    proposal_payload,
    refuse_execution,
)
from sentinel.operations.validation import ProposalRejected

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


class ProposalCreate(BaseModel):
    target_id: uuid.UUID
    request_text: str = Field(min_length=1, max_length=200)


class Decision(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


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


def _guard(exc: ProposalRejected | PermissionDenied) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/proposals", status_code=201)
def submit_proposal(
    body: ProposalCreate,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    with _factory(request).begin() as session:
        try:
            proposal = create_proposal(
                session, settings, caller, body.target_id, body.request_text, idempotency_key,
            )
        except (ProposalRejected, PermissionDenied) as exc:
            raise _guard(exc) from exc
        return proposal_payload(proposal)


@router.get("/proposals")
def proposals(
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    status: str | None = None,
    target_id: uuid.UUID | None = None,
) -> dict[str, object]:
    with _factory(request).begin() as session:
        try:
            rows = list_proposals(session, caller, status, target_id)
        except (ProposalRejected, PermissionDenied) as exc:
            raise _guard(exc) from exc
        return {"items": [proposal_payload(row) for row in rows]}


@router.get("/proposals/{proposal_id}")
def proposal_detail(
    proposal_id: uuid.UUID,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
) -> dict[str, object]:
    with _factory(request).begin() as session:
        try:
            proposal = get_proposal(session, caller, proposal_id)
        except (ProposalRejected, PermissionDenied) as exc:
            raise _guard(exc) from exc
        return proposal_payload(proposal)


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(
    proposal_id: uuid.UUID,
    body: Decision,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
) -> dict[str, object]:
    return _decision(proposal_id, body, request, caller, approved=True)


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(
    proposal_id: uuid.UUID,
    body: Decision,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
) -> dict[str, object]:
    return _decision(proposal_id, body, request, caller, approved=False)


@router.post("/proposals/{proposal_id}/execute")
def execute_proposal(proposal_id: uuid.UUID) -> dict[str, object]:
    del proposal_id
    try:
        refuse_execution()
    except PermissionDenied as exc:
        raise _guard(exc) from exc
    return {}


def _decision(
    proposal_id: uuid.UUID,
    body: Decision,
    request: Request,
    caller: Caller,
    *,
    approved: bool,
) -> dict[str, object]:
    with _factory(request).begin() as session:
        try:
            proposal = decide(session, caller, proposal_id, approved=approved, reason=body.reason)
        except (ProposalRejected, PermissionDenied) as exc:
            raise _guard(exc) from exc
        return proposal_payload(proposal)
