"""Signed session cookies. The cookie value is not a password."""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from sentinel.config.settings import Settings


@dataclass(frozen=True)
class SessionIdentity:
    principal: str
    role: str
    method: str


def issue_token(settings: Settings, principal: str, role: str, method: str) -> str:
    payload = {
        "principal": principal,
        "role": role,
        "method": method,
        "exp": int(time.time()) + settings.auth_session_hours * 3600,
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    ).decode("ascii").rstrip("=")
    signature = hmac.new(_secret(settings), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def read_token(settings: Settings, token: str) -> SessionIdentity | None:
    body, separator, signature = token.partition(".")
    if not separator or not body or not signature:
        return None
    expected = hmac.new(_secret(settings), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        principal = str(payload["principal"])
        role = str(payload["role"])
        method = str(payload["method"])
        expires = int(payload["exp"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if expires < int(time.time()) or not principal or role not in {"operator", "dba", "admin", "auditor"}:
        return None
    if method not in {"ad", "local"}:
        return None
    return SessionIdentity(principal[:200], role, method)


def _secret(settings: Settings) -> bytes:
    return settings.secret_key.encode("utf-8")
