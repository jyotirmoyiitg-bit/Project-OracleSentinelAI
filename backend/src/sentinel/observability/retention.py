"""Retention windows. Audit rows are not deleted by the application."""

from datetime import datetime, timedelta

RETENTION_DAYS = {
    "observations": 30,
    "diagnostic_events": 90,
    "chat_messages": 90,
    "incidents": 365,
    "documents": None,
    "audit_events": 2555,
}


class RetentionDenied(RuntimeError):
    pass


def cutoff(kind: str, now: datetime) -> datetime | None:
    days = RETENTION_DAYS[kind]
    if days is None:
        return None
    return now - timedelta(days=days)


def purge_audit(_now: datetime) -> None:
    """Release 1 keeps every audit event. A later review has to change this on purpose."""
    raise RetentionDenied("Audit events are immutable and are not purged")
