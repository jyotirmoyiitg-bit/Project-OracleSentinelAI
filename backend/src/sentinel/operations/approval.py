"""Approve or reject a pending proposal. Approval does not run the preview SQL."""

import uuid

from sqlalchemy.orm import Session

from sentinel.auth.rbac import require
from sentinel.collectors.normalization import utcnow
from sentinel.knowledge.acl import Caller
from sentinel.observability.audit import record
from sentinel.observability.metrics import record_proposal
from sentinel.operations.proposals import expire_if_needed
from sentinel.operations.validation import ProposalRejected
from sentinel.persistence.models import OperationProposal


def decide(
    session: Session,
    caller: Caller,
    proposal_id: uuid.UUID,
    *,
    approved: bool,
    reason: str,
) -> OperationProposal:
    require(caller, "proposal.decide")
    proposal = session.get(OperationProposal, proposal_id)
    if proposal is None:
        raise ProposalRejected("That proposal was not found.", 404)
    expire_if_needed(session, proposal)
    if proposal.status != "pending":
        raise ProposalRejected(f"This proposal is {proposal.status} and can no longer be decided.", 409)
    if proposal.requester == caller.principal:
        raise ProposalRejected("Authorization failed. The requester cannot approve their own proposal.", 403)
    note = reason.strip()
    if not note or len(note) > 500:
        raise ProposalRejected("A decision reason of 1 to 500 characters is required.")
    proposal.status = "approved" if approved else "rejected"
    proposal.approver = caller.principal[:200]
    proposal.decided_at = utcnow()
    proposal.decision_reason = note
    proposal.execution_log = []
    session.flush()
    record(
        session,
        principal=caller.principal,
        action="proposal.approve" if approved else "proposal.reject",
        target=str(proposal.id),
        outcome=proposal.status,
        correlation_id=proposal.correlation_id,
        detail={
            "status": proposal.status,
            "preview_sql": proposal.preview_sql,
            "execution_allowed": False,
            "reason": note,
        },
    )
    record_proposal(proposal.status)
    return proposal
