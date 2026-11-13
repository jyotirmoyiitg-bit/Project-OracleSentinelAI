"""Operator sign-in. The password is checked and then discarded."""

from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from sentinel.auth.oidc import OidcError, begin, complete, enabled, state_cookie_name
from sentinel.auth.service import LoginDenied, authenticate
from sentinel.auth.tokens import issue_token, read_token
from sentinel.config.settings import Settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    local: bool = False


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


@router.get("/session")
def current_session(request: Request) -> dict[str, object]:
    settings = _settings(request)
    if not settings.auth_required:
        return {"required": False, "authenticated": True, "principal": "", "role": "operator", "method": "", "oidc_enabled": enabled(settings)}
    identity = read_token(settings, request.cookies.get("sentinel_session", ""))
    if identity is None:
        return {"required": True, "authenticated": False, "principal": "", "role": "", "method": "", "oidc_enabled": enabled(settings)}
    return {
        "required": True, "authenticated": True, "principal": identity.principal,
        "role": identity.role, "method": identity.method, "oidc_enabled": enabled(settings),
    }


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict[str, object]:
    settings = _settings(request)
    if not settings.auth_required:
        raise HTTPException(status_code=404, detail="Sign-in is not enabled")
    with _sessions(request).begin() as session:
        try:
            principal, role, method = authenticate(
                session, settings, body.username, body.password, use_local=body.local,
            )
        except LoginDenied as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    token = issue_token(settings, principal, role, method)
    response.set_cookie(
        "sentinel_session", token, httponly=True, samesite="lax",
        secure=settings.auth_cookie_secure, max_age=settings.auth_session_hours * 3600, path="/",
    )
    return {"required": True, "authenticated": True, "principal": principal, "role": role, "method": method, "oidc_enabled": enabled(settings)}


@router.get("/oidc/start")
def oidc_start(request: Request) -> RedirectResponse:
    settings = _settings(request)
    try:
        url, cookie = begin(settings)
    except OidcError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(state_cookie_name(), cookie, httponly=True, samesite="lax", max_age=600, path="/")
    return response


@router.get("/oidc/callback")
def oidc_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    settings = _settings(request)
    try:
        identity = complete(settings, code, state, request.cookies.get(state_cookie_name(), ""))
    except OidcError:
        target = settings.auth_oidc_success_url
        join = "&" if "?" in target else "?"
        response = RedirectResponse(f"{target}{join}sign_in_error=idp", status_code=302)
        response.delete_cookie(state_cookie_name(), path="/")
        return response
    token = issue_token(settings, identity.principal, identity.role, "oidc")
    response = RedirectResponse(settings.auth_oidc_success_url, status_code=302)
    response.set_cookie(
        "sentinel_session", token, httponly=True, samesite="lax",
        secure=settings.auth_cookie_secure, max_age=settings.auth_session_hours * 3600, path="/",
    )
    response.delete_cookie(state_cookie_name(), path="/")
    return response


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie("sentinel_session", path="/")
    return {"authenticated": False}
