"""Append-only audit records. The database rejects updates and deletes."""

import hashlib
import json
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from sentinel.collectors.normalization import utcnow
from sentinel.persistence.models import AuditEvent


class AuditImmutable(RuntimeError):
    """Raised when application code tries to change an audit row."""


def evidence_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def record(
    session: Session,
    *,
    principal: str,
    action: str,
    target: str,
    outcome: str,
    correlation_id: str,
    detail: dict[str, Any],
    service: str = "api",
) -> AuditEvent:
    digest = evidence_digest({"action": action, "target": target, "outcome": outcome, "detail": detail})
    event_row = AuditEvent(
        occurred_at=utcnow(),
        principal=principal[:200],
        service=service[:40],
        action=action[:80],
        target=target[:80],
        correlation_id=correlation_id[:36],
        outcome=outcome[:20],
        evidence_digest=digest,
        detail=detail,
    )
    session.add(event_row)
    session.flush()
    return event_row


@event.listens_for(AuditEvent, "before_update")
def _reject_update(_mapper: object, _connection: object, _target: object) -> None:
    raise AuditImmutable("Audit events cannot be changed")


@event.listens_for(AuditEvent, "before_delete")
def _reject_delete(_mapper: object, _connection: object, _target: object) -> None:
    raise AuditImmutable("Audit events cannot be deleted")
