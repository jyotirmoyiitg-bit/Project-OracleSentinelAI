"""Incident state machine: new -> acknowledged -> resolved, with an auditable, immutable history."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.collectors.normalization import utcnow
from sentinel.persistence.models import Incident, IncidentEvent

VALID_TRANSITIONS: dict[str, set[str]] = {
    "new": {"acknowledged", "resolved"},
    "acknowledged": {"resolved"},
    "resolved": set(),
}


class InvalidTransitionError(ValueError):
    """Raised when an incident state transition is not allowed."""


class IncidentLifecycle:
    """Stateless helpers operating on an active SQLAlchemy session; callers own the transaction."""

    @staticmethod
    def open_or_update(
        session: Session, *, target_id: uuid.UUID, rule_id: str, fingerprint: str, severity: str,
        message: str, evidence: dict[str, Any] | None = None, now: datetime | None = None,
    ) -> tuple[Incident, bool]:
        """Open a new incident, or extend the still-open incident with the same fingerprint.

        Returns `(incident, created)`. A resolved incident never matches — a recurring
        problem after resolution opens a fresh incident instead of reopening a closed one.
        """
        now = now or utcnow()
        existing = session.scalar(
            select(Incident).where(Incident.fingerprint == fingerprint, Incident.state != "resolved")
            .order_by(Incident.created_at.desc()),
        )
        if existing is not None:
            before = {"state": existing.state, "severity": existing.severity, "last_seen": existing.last_seen.isoformat()}
            existing.last_seen = now
            existing.message = message
            if _severity_rank(severity) > _severity_rank(existing.severity):
                existing.severity = severity
            session.add(IncidentEvent(
                incident_id=existing.id, type="recurrence", timestamp=now, actor="system",
                before=before, after={"state": existing.state, "severity": existing.severity, "last_seen": now.isoformat()},
            ))
            return existing, False
        incident = Incident(
            id=uuid.uuid4(), target_id=target_id, rule_id=rule_id, fingerprint=fingerprint,
            state="new", severity=severity, message=message, first_seen=now, last_seen=now,
            evidence=evidence or {},
        )
        session.add(incident)
        session.flush()
        session.add(IncidentEvent(
            incident_id=incident.id, type="opened", timestamp=now, actor="system",
            before={}, after={"state": "new", "severity": severity},
        ))
        return incident, True

    @staticmethod
    def acknowledge(
        session: Session, incident_id: uuid.UUID, actor: str, reason: str | None = None, now: datetime | None = None,
    ) -> Incident:
        return IncidentLifecycle._transition(session, incident_id, "acknowledged", actor, reason, now)

    @staticmethod
    def resolve(
        session: Session, incident_id: uuid.UUID, actor: str, reason: str | None = None, now: datetime | None = None,
    ) -> Incident:
        return IncidentLifecycle._transition(session, incident_id, "resolved", actor, reason, now)

    @staticmethod
    def _transition(
        session: Session, incident_id: uuid.UUID, target_state: str, actor: str,
        reason: str | None, now: datetime | None,
    ) -> Incident:
        incident = session.get(Incident, incident_id)
        if incident is None:
            raise InvalidTransitionError(f"Incident not found: {incident_id}")
        if target_state not in VALID_TRANSITIONS.get(incident.state, set()):
            raise InvalidTransitionError(f"Cannot transition incident from {incident.state!r} to {target_state!r}")
        now = now or utcnow()
        before = {"state": incident.state}
        incident.state = target_state
        incident.last_seen = now
        session.add(IncidentEvent(
            incident_id=incident.id, type=target_state, timestamp=now, actor=actor, reason=reason,
            before=before, after={"state": target_state},
        ))
        return incident


def _severity_rank(severity: str) -> int:
    return {"info": 0, "warning": 1, "critical": 2}.get(severity, 0)
