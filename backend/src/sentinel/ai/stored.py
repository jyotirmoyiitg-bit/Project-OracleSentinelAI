"""Answer a database question from a recent agent snapshot before reading Oracle again.

A snapshot row is the same reading the live path returns, stored by the Collector. It is used only
when it is recent, when the operator did not ask for a live value, and when the caller may read it.
Otherwise the caller reads the database live as before.
"""

import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.agents.packs import is_restricted
from sentinel.ai.activity import TOOL_NAMES, ActivityReading
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller, is_admin_operator
from sentinel.persistence.models import EvidenceSnapshot

# Words that ask for the value as it is this moment.
_LIVE = re.compile(r"(?i)\b(?:right now|now|live|currently|at the moment|this moment|refresh|re-?read|latest|just)\b")


def wants_live(question: str) -> bool:
    return bool(_LIVE.search(question))


def age_text(seconds: float) -> str:
    if seconds < 90:
        return "about a minute ago" if seconds >= 45 else "under a minute ago"
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes} minutes ago"
    return f"{round(minutes / 60)} hours ago"


def stored_reading(
    session: Session,
    settings: Settings,
    target_id: uuid.UUID,
    kind: str,
    caller: Caller,
    question: str,
    now: datetime | None = None,
) -> ActivityReading | None:
    """The newest usable snapshot of one reading, or None when the database should be read live."""
    limit = settings.ai_snapshot_max_age_seconds
    if limit <= 0 or kind not in TOOL_NAMES or wants_live(question):
        return None
    if is_restricted(kind) and not is_admin_operator(caller.role):
        return None
    moment = now or utcnow()
    row = session.scalars(
        select(EvidenceSnapshot).where(
            EvidenceSnapshot.target_id == target_id, EvidenceSnapshot.kind == kind,
            EvidenceSnapshot.taken_at >= moment - timedelta(seconds=limit),
        ).order_by(EvidenceSnapshot.taken_at.desc()).limit(1),
    ).first()
    if row is None or row.status != "ok" or not isinstance(row.rows, list):
        return None
    age = max(0.0, (moment - row.taken_at).total_seconds())
    note = f"From the snapshot stored {age_text(age)}. Ask for the value now to read the database again."
    rows = [dict(item) for item in row.rows if isinstance(item, dict)]
    return ActivityReading(kind, TOOL_NAMES[kind], "ok", rows, f"{row.note} {note}".strip())
