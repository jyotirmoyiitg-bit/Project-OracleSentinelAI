"""Role checks for proposals, audit, and administration.

`admin` keeps the platform-administrator permissions. `auditor` keeps the security-auditor permissions.
"""

from sentinel.knowledge.acl import Caller

CANONICAL_ROLES = frozenset({"viewer", "operator", "dba", "platform_admin", "security_auditor"})
_ALIASES = {"admin": "platform_admin", "auditor": "security_auditor"}
_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"dashboard.read"}),
    "operator": frozenset({"dashboard.read", "incident.acknowledge", "proposal.read", "privileged.read"}),
    "dba": frozenset({
        "dashboard.read", "incident.acknowledge", "proposal.read", "proposal.create", "proposal.decide",
        "database.manage", "privileged.read",
    }),
    "platform_admin": frozenset({
        "dashboard.read", "incident.acknowledge", "proposal.read", "proposal.create", "proposal.decide",
        "database.manage", "audit.read", "provider.read", "role.manage", "credential.rotate", "platform.read",
        "config.write", "privileged.read", "privileged.execute",
    }),
    "security_auditor": frozenset({"dashboard.read", "audit.read", "proposal.read", "privileged.read"}),
}


class PermissionDenied(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = 403


def canonical_role(role: str) -> str:
    mapped = _ALIASES.get(role, role)
    if mapped not in CANONICAL_ROLES:
        raise PermissionDenied("Unknown role")
    return mapped


def has_permission(role: str, permission: str) -> bool:
    try:
        return permission in _PERMISSIONS[canonical_role(role)]
    except PermissionDenied:
        return False


def require(caller: Caller, permission: str) -> None:
    if not has_permission(caller.role, permission):
        raise PermissionDenied("Authorization failed.")


def bypasses_entitlement(role: str) -> bool:
    """Platform administrators keep access when no per-database grant has been stored."""
    return has_permission(role, "platform.read")
