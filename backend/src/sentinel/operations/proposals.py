"""Create and list proposal-only changes. Nothing in this module connects to Oracle."""

import uuid
from datetime import timedelta

from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.auth.rbac import PermissionDenied, require
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller
from sentinel.observability.audit import record
from sentinel.observability.metrics import record_proposal
from sentinel.operations.validation import PreparedChange, ProposalRejected, parse_request, preflight
from sentinel.persistence.models import DatabaseTarget, OperationProposal

_tracer = trace.get_tracer("sentinel.operations")


def execution_enabled() -> bool:
    """Release 1 has no execution path."""
    return False


def refuse_execution() -> None:
    raise PermissionDenied("Release 1 does not execute proposals.")


def create_proposal(
    session: Session,
    settings: Settings,
    caller: Caller,
    target_id: uuid.UUID,
    request_text: str,
    idempotency_key: str | None,
) -> OperationProposal:
    require(caller, "proposal.create")
    with _tracer.start_as_current_span("proposal.create"):
        key = (idempotency_key or "").strip()[:80] or None
        if key:
            existing = session.scalar(
                select(OperationProposal).where(
                    OperationProposal.requester == caller.principal,
                    OperationProposal.idempotency_key == key,
                ),
            )
            if existing is not None:
                return existing
        change = parse_request(request_text)
        target = session.get(DatabaseTarget, target_id)
        if target is None:
            raise ProposalRejected("That database was not found.", 404)
        risk = preflight(session, caller, target, change)
        correlation_id = str(uuid.uuid4())
        proposal = OperationProposal(
            requester=caller.principal[:200],
            requester_role=caller.role[:32],
            action="tablespace.add_omf_datafile",
            target_id=target.id,
            parameters={"tablespace": change.tablespace, "size_mb": change.size_mb, "request_text": request_text[:200]},
            preview_sql=change.preview_sql,
            impact=change.impact,
            risk=risk,
            status="pending",
            expires_at=utcnow() + timedelta(hours=settings.proposal_expiry_hours),
            correlation_id=correlation_id,
            idempotency_key=key,
            execution_log=[],
            created_at=utcnow(),
        )
        session.add(proposal)
        session.flush()
        record(
            session,
            principal=caller.principal,
            action="proposal.create",
            target=str(proposal.id),
            outcome="pending",
            correlation_id=correlation_id,
            detail=_detail(proposal, change),
        )
        record_proposal("pending")
        return proposal


def list_proposals(
    session: Session,
    caller: Caller,
    status: str | None,
    target_id: uuid.UUID | None,
) -> list[OperationProposal]:
    require(caller, "proposal.read")
    statement = select(OperationProposal).order_by(OperationProposal.created_at.desc())
    if status:
        statement = statement.where(OperationProposal.status == status)
    if target_id is not None:
        statement = statement.where(OperationProposal.target_id == target_id)
    rows = list(session.scalars(statement.limit(100)).all())
    for row in rows:
        expire_if_needed(session, row)
    return rows


def get_proposal(session: Session, caller: Caller, proposal_id: uuid.UUID) -> OperationProposal:
    require(caller, "proposal.read")
    proposal = session.get(OperationProposal, proposal_id)
    if proposal is None:
        raise ProposalRejected("That proposal was not found.", 404)
    expire_if_needed(session, proposal)
    return proposal


def proposal_payload(proposal: OperationProposal) -> dict[str, object]:
    return {
        "id": str(proposal.id),
        "requester": proposal.requester,
        "requester_role": proposal.requester_role,
        "action": proposal.action,
        "target_id": str(proposal.target_id),
        "parameters": proposal.parameters,
        "preview_sql": proposal.preview_sql,
        "impact": proposal.impact,
        "risk": proposal.risk,
        "status": proposal.status,
        "expires_at": proposal.expires_at.isoformat(),
        "approver": proposal.approver,
        "decided_at": proposal.decided_at.isoformat() if proposal.decided_at else None,
        "decision_reason": proposal.decision_reason,
        "correlation_id": proposal.correlation_id,
        "execution_allowed": False,
        "execution_log": [],
        "created_at": proposal.created_at.isoformat(),
    }


def expire_if_needed(session: Session, proposal: OperationProposal) -> None:
    if proposal.status == "pending" and proposal.expires_at <= utcnow():
        proposal.status = "expired"
        session.flush()
        record(
            session,
            principal="system",
            action="proposal.expire",
            target=str(proposal.id),
            outcome="expired",
            correlation_id=proposal.correlation_id,
            detail={"status": "expired"},
        )


def _detail(proposal: OperationProposal, change: PreparedChange) -> dict[str, object]:
    return {
        "action": proposal.action,
        "target_id": str(proposal.target_id),
        "tablespace": change.tablespace,
        "size_mb": change.size_mb,
        "preview_sql": change.preview_sql,
        "execution_allowed": False,
    }
