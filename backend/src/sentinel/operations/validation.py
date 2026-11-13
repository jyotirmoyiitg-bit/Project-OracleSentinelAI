"""Fixed tablespace-datafile proposal checks. The request text is never copied into SQL."""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.auth.rbac import bypasses_entitlement
from sentinel.knowledge.acl import Caller
from sentinel.persistence.models import CurrentObservation, DatabaseEntitlement, DatabaseTarget

_EXTEND = re.compile(
    r"^extend\s+([A-Za-z][A-Za-z0-9_$#]{0,29})\s+by\s+(\d+(?:\.\d+)?)\s*(gb|mb)\s*$",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_$#]{0,29}$")
DATAFILE_SQL = "ALTER TABLESPACE {tablespace} ADD DATAFILE SIZE {size_mb}M"
MIN_SIZE_MB = 1
MAX_SIZE_MB = 32768
ACTION = "tablespace.add_omf_datafile"


class ProposalRejected(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class PreparedChange:
    tablespace: str
    size_mb: int
    preview_sql: str
    impact: str


def parse_request(text: str) -> PreparedChange:
    """Accept only `Extend <tablespace> by <size> GB|MB` and build the fixed statement."""
    match = _EXTEND.fullmatch(text.strip())
    if match is None:
        raise ProposalRejected(
            "Only a tablespace datafile proposal is accepted, such as Extend USERS by 10 GB.",
        )
    tablespace = match.group(1).upper()
    if _IDENTIFIER.fullmatch(tablespace) is None:
        raise ProposalRejected("The tablespace name is not an Oracle identifier.")
    amount = float(match.group(2))
    size_mb = amount * 1024 if match.group(3).lower() == "gb" else amount
    if size_mb != int(size_mb):
        raise ProposalRejected("The requested size must be a whole number of megabytes.")
    size = int(size_mb)
    if size < MIN_SIZE_MB or size > MAX_SIZE_MB:
        raise ProposalRejected(f"The requested size must be from {MIN_SIZE_MB} MB to {MAX_SIZE_MB} MB.")
    preview = DATAFILE_SQL.format(tablespace=tablespace, size_mb=size)
    impact = (
        f"Adds one Oracle-managed datafile of {size} MB to {tablespace}. "
        "Release 1 does not run this statement."
    )
    return PreparedChange(tablespace, size, preview, impact)


def preflight(session: Session, caller: Caller, target: DatabaseTarget, change: PreparedChange) -> str:
    """Check the target, entitlement, maintenance window, OMF, and measured free space."""
    if not target.enabled:
        raise ProposalRejected("That database is disabled.", 404)
    if target.maintenance_restricted:
        raise ProposalRejected("This database is in a maintenance restriction.")
    _require_entitlement(session, caller, target)
    if not _omf_configured(session, target):
        raise ProposalRejected("OMF is not confirmed for this database. db_create_file_dest is empty.")
    available = _available_bytes(session, target)
    if available is None:
        raise ProposalRejected("Storage availability has not been measured for this database.")
    requested = change.size_mb * 1024 * 1024
    if requested > available:
        raise ProposalRejected("The requested size exceeds the measured free recovery area space.")
    return "medium"


def _require_entitlement(session: Session, caller: Caller, target: DatabaseTarget) -> None:
    if bypasses_entitlement(caller.role):
        return
    grants = session.scalars(
        select(DatabaseEntitlement).where(DatabaseEntitlement.principal == caller.principal),
    ).all()
    environment = (target.environment or "").strip().lower()
    allowed = any(
        grant.target_id == target.id or (
            not grant.target_id and grant.environment.strip().lower() == environment and environment
        )
        for grant in grants
    )
    if not allowed:
        raise ProposalRejected("Authorization failed. This database is not entitled to the caller.", 403)


def _omf_configured(session: Session, target: DatabaseTarget) -> bool:
    rows = session.scalars(
        select(CurrentObservation).where(
            CurrentObservation.target_id == target.id,
            CurrentObservation.metric_name == "config.parameter",
        ),
    ).all()
    for row in rows:
        labels = row.labels if isinstance(row.labels, dict) else {}
        if str(labels.get("parameter") or "") != "db_create_file_dest":
            continue
        if str(row.value_str or "").strip():
            return True
    return False


def _available_bytes(session: Session, target: DatabaseTarget) -> int | None:
    rows = session.scalars(
        select(CurrentObservation).where(
            CurrentObservation.target_id == target.id,
            CurrentObservation.metric_name.in_(("storage.fra.space_limit", "storage.fra.space_used")),
        ),
    ).all()
    values = {row.metric_name: row.value for row in rows}
    limit = values.get("storage.fra.space_limit")
    used = values.get("storage.fra.space_used")
    if limit is None or used is None:
        return None
    return max(int(limit) - int(used), 0)
