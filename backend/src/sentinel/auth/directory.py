"""Active Directory sign-in through LDAP bind. A failed directory does not reveal the password."""

import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

from sentinel.config.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DirectoryResult:
    status: str
    role: str = ""
    principal: str = ""


def authenticate_directory(settings: Settings, username: str, password: str) -> DirectoryResult:
    if not settings.auth_ad_url.strip() or not settings.auth_ad_domain.strip():
        return DirectoryResult("not_configured")
    groups_configured = (
        settings.auth_ad_operator_group.strip()
        or settings.auth_ad_dba_group.strip()
        or settings.auth_ad_admin_group.strip()
    )
    if not groups_configured:
        return DirectoryResult("not_configured")
    try:
        import ldap3
        from ldap3.core.exceptions import LDAPBindError, LDAPException
        from ldap3.utils.conv import escape_filter_chars
    except ImportError:
        logger.warning("directory sign-in unavailable because ldap3 is not installed")
        return DirectoryResult("unavailable")
    account = username.strip()
    bind_name = account if "@" in account else f"{account}@{settings.auth_ad_domain.strip()}"
    parsed = urlsplit(settings.auth_ad_url.strip())
    host = parsed.hostname or ""
    use_ssl = parsed.scheme == "ldaps"
    port = parsed.port or (636 if use_ssl else 389)
    if not host:
        return DirectoryResult("unavailable")
    try:
        server = ldap3.Server(host, port=port, use_ssl=use_ssl, connect_timeout=5, get_info=ldap3.NONE)
        connection = ldap3.Connection(
            server, user=bind_name, password=password, auto_bind=True, receive_timeout=5, read_only=True,
        )
    except LDAPBindError:
        return DirectoryResult("invalid")
    except LDAPException:
        logger.warning("directory sign-in could not reach the domain controller")
        return DirectoryResult("unavailable")
    try:
        sam = account.split("@", 1)[0]
        base = settings.auth_ad_base_dn.strip() or _domain_to_dn(settings.auth_ad_domain)
        found = connection.search(
            search_base=base,
            search_filter=f"(sAMAccountName={escape_filter_chars(sam)})",
            search_scope=ldap3.SUBTREE,
            attributes=["memberOf", "sAMAccountName"],
            size_limit=1,
        )
        groups: list[str] = []
        principal = sam
        if found and connection.entries:
            entry = connection.entries[0]
            principal = str(entry.sAMAccountName.value or sam)
            raw_groups = entry.memberOf.values if "memberOf" in entry else []
            groups = [str(item) for item in raw_groups]
        role = _role(settings, groups)
        if role is None:
            return DirectoryResult("not_operator")
        return DirectoryResult("authenticated", role, principal[:200])
    except LDAPException:
        logger.warning("directory group lookup failed")
        return DirectoryResult("unavailable")
    finally:
        connection.unbind()


def _role(settings: Settings, groups: list[str]) -> str | None:
    folded = {item.casefold() for item in groups}
    names: set[str] = set()
    for item in groups:
        for part in item.split(","):
            if part.upper().startswith("CN="):
                names.add(part[3:].casefold())
    wanted = (
        ("admin", settings.auth_ad_admin_group),
        ("dba", settings.auth_ad_dba_group),
        ("operator", settings.auth_ad_operator_group),
    )
    for role, group in wanted:
        token = group.strip()
        if token and (token.casefold() in folded or token.casefold() in names):
            return role
    return None


def _domain_to_dn(domain: str) -> str:
    return ",".join(f"DC={part.strip()}" for part in domain.split(".") if part.strip())
