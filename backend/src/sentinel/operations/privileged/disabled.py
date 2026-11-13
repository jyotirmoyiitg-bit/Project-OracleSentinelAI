"""Disabled privileged operations. This module does not connect to Oracle or a shell."""

import uuid

from sqlalchemy.orm import Session

from sentinel.auth.rbac import PermissionDenied, require
from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller
from sentinel.observability.audit import record

DISABLED_MESSAGE = "Privileged operations are disabled in Release 1."
NOT_IMPLEMENTED_MESSAGE = "Privileged execution is not implemented. No statement was sent to Oracle."


class PrivilegedDisabled(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = 501


def privileged_status(settings: Settings) -> dict[str, object]:
    """Report the flag. Execution stays unavailable even when the flag is acknowledged."""
    return {
        "enabled": settings.operations_execution_enabled,
        "approved": settings.operations_execution_approved,
        "require_dual_approval": settings.operations_require_dual_approval,
        "execution_available": False,
        "message": _message(settings),
    }


def reject_privileged(
    session: Session,
    settings: Settings,
    caller: Caller,
    target: str,
    *,
    permission: str,
) -> None:
    """Audit the attempt, then refuse it. The reserved table is not written."""
    try:
        require(caller, permission)
    except PermissionDenied:
        _audit(session, caller, target, "denied")
        raise
    _audit(session, caller, target, "disabled")
    raise PrivilegedDisabled(_message(settings))


def _message(settings: Settings) -> str:
    if settings.operations_execution_enabled and settings.operations_execution_approved:
        return NOT_IMPLEMENTED_MESSAGE
    return DISABLED_MESSAGE


def _audit(session: Session, caller: Caller, target: str, outcome: str) -> None:
    record(
        session,
        principal=caller.principal,
        action="privileged.access",
        target=target,
        outcome=outcome,
        correlation_id=str(uuid.uuid4()),
        detail={"execution_available": False, "statement_sent": False},
        service="operations",
    )
