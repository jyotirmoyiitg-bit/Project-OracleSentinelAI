"""Tie a local session to the account as it is now.

A session cookie is signed and carries no server-side row. For a local account the cookie also
carries a fingerprint of the role, the enabled flag, and the password hash. When an administrator
disables the account, changes its role, or resets its password, the fingerprint no longer matches
and the next request is signed out. Directory and OIDC sessions are bounded by their expiry.
"""

import hashlib
import hmac
import threading
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.auth.tokens import SessionIdentity
from sentinel.config.settings import Settings
from sentinel.persistence.models import OperatorAccount

_CACHE_SECONDS = 10.0
_cache: dict[tuple[str, str], tuple[float, bool]] = {}
_guard = threading.Lock()


def account_fingerprint(settings: Settings, account: OperatorAccount) -> str:
    material = f"{account.username}|{account.role}|{int(bool(account.enabled))}|{account.password_hash}"
    return hmac.new(settings.secret_key.encode("utf-8"), material.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def forget(principal: str | None = None) -> None:
    """Drop cached verdicts after an account changed, so the change applies on the next request."""
    with _guard:
        if principal is None:
            _cache.clear()
            return
        name = principal.strip().lower()
        for key in [key for key in _cache if key[0] == name]:
            _cache.pop(key, None)


def session_is_current(settings: Settings, factory: Any, identity: SessionIdentity) -> bool:
    """A directory or OIDC session is current until it expires. A local one must match its account."""
    if identity.method != "local":
        return True
    if not identity.fingerprint or not callable(getattr(factory, "begin", None)):
        return False
    key = (identity.principal.strip().lower(), identity.fingerprint)
    moment = time.monotonic()
    with _guard:
        cached = _cache.get(key)
        if cached is not None and moment - cached[0] < _CACHE_SECONDS:
            return cached[1]
    verdict = _matches(settings, factory, identity)
    with _guard:
        if len(_cache) > 5000:
            _cache.clear()
        _cache[key] = (moment, verdict)
    return verdict


def _matches(settings: Settings, factory: sessionmaker[Session], identity: SessionIdentity) -> bool:
    try:
        with factory() as session:
            account = session.scalar(
                select(OperatorAccount).where(OperatorAccount.username == identity.principal.strip().lower()),
            )
            if account is None or not account.enabled or account.role != identity.role:
                return False
            return hmac.compare_digest(account_fingerprint(settings, account), identity.fingerprint)
    except Exception:  # noqa: BLE001 -- an unreadable account store must not keep a session alive
        return False
