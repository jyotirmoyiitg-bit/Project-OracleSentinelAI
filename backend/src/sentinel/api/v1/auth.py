"""Operator sign-in. The password is checked and then discarded. Every attempt is audited."""

import uuid
from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.auth.oidc import OidcError, begin, complete, enabled, state_cookie_name
from sentinel.auth.passwords import hash_password, password_problem, verify_password
from sentinel.auth.service import LoginDenied, authenticate
from sentinel.auth.sessions import account_fingerprint, forget, session_is_current
from sentinel.auth.throttle import LoginThrottle
from sentinel.auth.tokens import SessionIdentity, issue_token, read_token
from sentinel.config.settings import Settings
from sentinel.observability.audit import record
from sentinel.persistence.models import OperatorAccount

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    local: bool = False


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


def _settings(request: Request) -> Settings:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise HTTPException(status_code=503, detail="Application settings are not initialized")
    return settings


def _sessions(request: Request) -> sessionmaker[Session]:
    factory = getattr(request.app.state, "session_factory", None)
    if not callable(getattr(factory, "begin", None)):
        raise HTTPException(status_code=503, detail="Database is not initialized")
    return cast(sessionmaker[Session], factory)


def _throttle(request: Request, settings: Settings) -> LoginThrottle:
    existing = getattr(request.app.state, "login_throttle", None)
    seconds = settings.auth_lockout_minutes * 60
    if (
        isinstance(existing, LoginThrottle)
        and existing.max_failures == settings.auth_max_failed_logins
        and existing.lock_seconds == seconds
    ):
        return existing
    throttle = LoginThrottle(settings.auth_max_failed_logins, seconds, seconds)
    request.app.state.login_throttle = throttle
    return throttle


def _address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _audit(request: Request, principal: str, action: str, outcome: str, detail: dict[str, object]) -> None:
    """Sign-in audit rows are written in their own transaction. A failed write does not block sign-in."""
    try:
        with _sessions(request).begin() as session:
            record(
                session, principal=principal or "unknown", action=action, target=principal[:80] or "unknown",
                outcome=outcome, correlation_id=str(uuid.uuid4()), detail=detail, service="auth",
            )
    except Exception:  # noqa: BLE001 -- the audit store must not turn a sign-in into a server error
        return


def _identity(request: Request, settings: Settings) -> SessionIdentity | None:
    identity = read_token(settings, request.cookies.get("sentinel_session", ""))
    if identity is None:
        return None
    factory = getattr(request.app.state, "session_factory", None)
    return identity if session_is_current(settings, factory, identity) else None


def _session_payload(settings: Settings, identity: SessionIdentity | None) -> dict[str, object]:
    common: dict[str, object] = {
        "required": True,
        "oidc_enabled": enabled(settings),
        "directory_enabled": bool(settings.auth_ad_url.strip()),
    }
    if identity is None:
        return {**common, "authenticated": False, "principal": "", "role": "", "method": ""}
    return {
        **common, "authenticated": True, "principal": identity.principal,
        "role": identity.role, "method": identity.method,
    }


def _set_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        "sentinel_session", token, httponly=True, samesite="lax",
        secure=settings.auth_cookie_secure, max_age=settings.auth_session_hours * 3600, path="/",
    )


@router.get("/session")
def current_session(request: Request) -> dict[str, object]:
    settings = _settings(request)
    if not settings.auth_required:
        return {
            "required": False, "authenticated": True, "principal": "", "role": "operator", "method": "",
            "oidc_enabled": enabled(settings), "directory_enabled": bool(settings.auth_ad_url.strip()),
        }
    return _session_payload(settings, _identity(request, settings))


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict[str, object]:
    settings = _settings(request)
    if not settings.auth_required:
        raise HTTPException(status_code=404, detail="Sign-in is not enabled")
    throttle = _throttle(request, settings)
    address = _address(request)
    name = body.username.strip().lower()
    method = "local" if body.local else "directory"
    wait = throttle.retry_after(name, address)
    if wait:
        _audit(request, name, "auth.login", "locked", {"method": method, "retry_after_seconds": wait})
        raise HTTPException(
            status_code=429,
            detail="Too many failed sign-ins. Try again later.",
            headers={"Retry-After": str(wait)},
        )
    fingerprint = ""
    with _sessions(request).begin() as session:
        try:
            principal, role, signed_method = authenticate(
                session, settings, body.username, body.password, use_local=body.local,
            )
        except LoginDenied as exc:
            locked = exc.status_code == 401 and throttle.failed(name, address)
            _audit(request, name, "auth.login", "denied", {"method": method, "status": exc.status_code, "locked": locked})
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        if signed_method == "local":
            account = session.scalar(select(OperatorAccount).where(OperatorAccount.username == principal))
            if account is not None:
                fingerprint = account_fingerprint(settings, account)
    throttle.succeeded(name, address)
    _audit(request, principal, "auth.login", "success", {"method": signed_method, "role": role})
    _set_cookie(response, settings, issue_token(settings, principal, role, signed_method, fingerprint))
    return _session_payload(settings, SessionIdentity(principal, role, signed_method, fingerprint))


@router.post("/password")
def change_password(body: PasswordChange, request: Request, response: Response) -> dict[str, object]:
    """A local operator changes their own password. Other sessions for the account are signed out."""
    settings = _settings(request)
    identity = _identity(request, settings) if settings.auth_required else None
    if identity is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    if identity.method != "local":
        raise HTTPException(status_code=400, detail="This account's password is managed by the directory.")
    problem = password_problem(body.new_password, identity.principal)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    if body.new_password == body.current_password:
        raise HTTPException(status_code=422, detail="The new password must differ from the current one.")
    with _sessions(request).begin() as session:
        account = session.scalar(select(OperatorAccount).where(OperatorAccount.username == identity.principal))
        if account is None or not account.enabled or not verify_password(body.current_password, account.password_hash):
            _audit(request, identity.principal, "auth.password", "denied", {"reason": "current_password"})
            raise HTTPException(status_code=403, detail="The current password is not correct.")
        account.password_hash = hash_password(body.new_password, settings.auth_password_iterations)
        session.flush()
        fingerprint = account_fingerprint(settings, account)
        record(
            session, principal=identity.principal, action="auth.password", target=identity.principal[:80],
            outcome="updated", correlation_id=str(uuid.uuid4()), detail={"self": True}, service="auth",
        )
    forget(identity.principal)
    _set_cookie(response, settings, issue_token(settings, identity.principal, identity.role, "local", fingerprint))
    return {"updated": True}


@router.get("/oidc/start")
def oidc_start(request: Request) -> RedirectResponse:
    settings = _settings(request)
    try:
        url, cookie = begin(settings)
    except OidcError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        state_cookie_name(), cookie, httponly=True, samesite="lax",
        secure=settings.auth_cookie_secure, max_age=600, path="/",
    )
    return response


@router.get("/oidc/callback")
def oidc_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    settings = _settings(request)
    try:
        identity = complete(settings, code, state, request.cookies.get(state_cookie_name(), ""))
    except OidcError:
        _audit(request, "unknown", "auth.login", "denied", {"method": "oidc"})
        target = settings.auth_oidc_success_url
        join = "&" if "?" in target else "?"
        response = RedirectResponse(f"{target}{join}sign_in_error=idp", status_code=302)
        response.delete_cookie(state_cookie_name(), path="/")
        return response
    _audit(request, identity.principal, "auth.login", "success", {"method": "oidc", "role": identity.role})
    token = issue_token(settings, identity.principal, identity.role, "oidc")
    response = RedirectResponse(settings.auth_oidc_success_url, status_code=302)
    _set_cookie(response, settings, token)
    response.delete_cookie(state_cookie_name(), path="/")
    return response


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    settings = _settings(request)
    identity = read_token(settings, request.cookies.get("sentinel_session", "")) if settings.auth_required else None
    if identity is not None:
        _audit(request, identity.principal, "auth.logout", "success", {"method": identity.method})
    response.delete_cookie("sentinel_session", path="/")
    return {"authenticated": False}
