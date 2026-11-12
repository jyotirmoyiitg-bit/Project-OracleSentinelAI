"""Document ACL checks. An empty grant denies access."""

from dataclasses import dataclass

from sentinel.knowledge.constants import ADMIN_OPERATOR_ROLE, ALLOWED_ROLES, DEFAULT_ROLES


@dataclass(frozen=True)
class Caller:
    role: str
    principal: str


def is_admin_operator(role: str) -> bool:
    """An admin operator may list database users. operator, dba, and auditor may not."""
    return role == ADMIN_OPERATOR_ROLE


def caller_from_headers(role: str | None, principal: str | None) -> Caller:
    resolved = (role or "operator").strip().lower()
    if resolved not in ALLOWED_ROLES:
        raise ValueError(f"Unknown role: {resolved}")
    identity = (principal or "").strip()
    if len(identity) > 200:
        raise ValueError("Principal must be at most 200 characters")
    return Caller(resolved, identity)


def default_acl() -> dict[str, list[str]]:
    return {"roles": list(DEFAULT_ROLES), "principals": []}


def copy_acl(acl: object) -> dict[str, list[str]]:
    if not isinstance(acl, dict):
        return default_acl()
    roles = acl.get("roles")
    principals = acl.get("principals")
    return {
        "roles": [str(item) for item in roles] if isinstance(roles, list) else [],
        "principals": [str(item) for item in principals] if isinstance(principals, list) else [],
    }


def normalize_acl(roles: list[str], principals: list[str]) -> dict[str, list[str]]:
    clean_roles: list[str] = []
    for role in roles:
        value = role.strip().lower()
        if value not in ALLOWED_ROLES:
            raise ValueError(f"Unknown role: {role}")
        if value not in clean_roles:
            clean_roles.append(value)
    clean_principals: list[str] = []
    for principal in principals:
        value = principal.strip()
        if not value or len(value) > 200:
            raise ValueError("Principals must be 1-200 characters")
        if value not in clean_principals:
            clean_principals.append(value)
    if len(clean_roles) > 20 or len(clean_principals) > 20:
        raise ValueError("ACL lists are limited to 20 entries")
    return {"roles": clean_roles, "principals": clean_principals}


def allows(acl: object, caller: Caller) -> bool:
    """Grant access when the caller is an administrator or is listed on the document."""
    if caller.role == "admin":
        return True
    if not isinstance(acl, dict):
        return False
    roles = acl.get("roles")
    principals = acl.get("principals")
    role_ok = isinstance(roles, list) and caller.role in roles
    principal_ok = bool(caller.principal) and isinstance(principals, list) and caller.principal in principals
    return role_ok or principal_ok
