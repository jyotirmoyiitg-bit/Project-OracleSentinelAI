"""Platform administration. Stored settings can be changed by an administrator. Secrets stay out."""

import uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.ai.gateway import hosted_is_configured
from sentinel.auth.passwords import hash_password, password_problem
from sentinel.auth.rbac import PermissionDenied, require
from sentinel.auth.sessions import forget
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.config.store import ConfigUpdateError, list_public_config, update_public_config
from sentinel.knowledge.acl import Caller, caller_from_headers
from sentinel.observability.audit import record
from sentinel.observability.retention import RETENTION_DAYS
from sentinel.persistence.models import AppConfig, DatabaseTarget, OperatorAccount, TargetCredential
from sentinel.rules.yaml_loader import load_rules

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class RoleChange(BaseModel):
    role: str = Field(min_length=1, max_length=32)


class OperatorCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$")
    password: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=32)


class OperatorEnabled(BaseModel):
    enabled: bool


class OperatorPassword(BaseModel):
    password: str = Field(min_length=1, max_length=200)


_OPERATOR_ROLES = frozenset({"viewer", "operator", "dba", "admin", "auditor", "platform_admin", "security_auditor"})


class CredentialRotation(BaseModel):
    secret_reference: str = Field(min_length=10, max_length=200)


class ConfigChange(BaseModel):
    value: Any


def _settings(request: Request) -> Settings:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise HTTPException(status_code=503, detail="Application settings are not initialized")
    return settings


def _factory(request: Request) -> sessionmaker[Session]:
    factory = getattr(request.app.state, "session_factory", None)
    if not callable(getattr(factory, "begin", None)):
        raise HTTPException(status_code=503, detail="Database is not initialized")
    return cast(sessionmaker[Session], factory)


def _caller(request: Request) -> Caller:
    existing = getattr(request.state, "caller", None)
    if isinstance(existing, Caller):
        return existing
    try:
        return caller_from_headers(request.headers.get("X-Sentinel-Role"), request.headers.get("X-Sentinel-Principal"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _deny(exc: PermissionDenied) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/platform")
def platform(
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    try:
        require(caller, "platform.read")
    except PermissionDenied as exc:
        raise _deny(exc) from exc
    rules: list[dict[str, object]] = []
    try:
        rules = [
            {"id": rule.id, "expression": rule.expression, "severity": rule.severity, "enabled": rule.enabled}
            for rule in load_rules(settings.rules_path)
        ]
    except (OSError, ValueError):
        rules = []
    return {
        "scheduler_enabled": settings.scheduler_enabled,
        "knowledge_enabled": settings.knowledge_enabled,
        "llm": {
            "provider": settings.ai_provider,
            "model": settings.ai_hosted_model if settings.ai_provider == "hosted" else settings.ai_model_name,
            "hosted_vendor": settings.ai_hosted_vendor,
            "secret_configured": hosted_is_configured(settings),
        },
        "notifications": {
            "smtp_enabled": settings.smtp_enabled,
            "smtp_host_configured": bool(settings.smtp_host),
            "smtp_from": settings.smtp_from_address or "",
            "smtp_secret_configured": bool(settings.smtp_password_secret_ref),
        },
        "roles": ["viewer", "operator", "dba", "platform_admin", "security_auditor"],
        "legacy_roles": {"admin": "platform_admin", "auditor": "security_auditor"},
        "retention_days": RETENTION_DAYS,
        "proposal_expiry_hours": settings.proposal_expiry_hours,
        "rules": rules,
        "execution_allowed": False,
        "privileged_execution_enabled": False,
    }


def _manage_operators(caller: Caller) -> None:
    try:
        require(caller, "role.manage")
    except PermissionDenied as exc:
        raise _deny(exc) from exc


def _account(session: Session, username: str) -> OperatorAccount:
    account = session.scalar(select(OperatorAccount).where(OperatorAccount.username == username.strip().lower()))
    if account is None:
        raise HTTPException(status_code=404, detail="That operator account was not found.")
    return account


def _not_self(caller: Caller, username: str, action: str) -> None:
    if username.strip().lower() == caller.principal.strip().lower():
        raise HTTPException(
            status_code=403,
            detail=f"Authorization failed. An operator cannot {action} their own account here.",
        )


def _operator_row(account: OperatorAccount) -> dict[str, object]:
    return {
        "username": account.username,
        "role": account.role,
        "enabled": bool(account.enabled),
        "created_at": account.created_at.isoformat() if account.created_at else "",
    }


@router.get("/operators")
def list_operators(request: Request, caller: Annotated[Caller, Depends(_caller)]) -> dict[str, object]:
    """Local operator accounts. Directory operators are managed in the directory and are not listed."""
    _manage_operators(caller)
    with _factory(request)() as session:
        rows = session.scalars(select(OperatorAccount).order_by(OperatorAccount.username)).all()
        return {"items": [_operator_row(row) for row in rows]}


@router.post("/operators", status_code=201)
def create_operator(
    body: OperatorCreate,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    _manage_operators(caller)
    username = body.username.strip().lower()
    role = body.role.strip().lower()
    if role not in _OPERATOR_ROLES:
        raise HTTPException(status_code=400, detail="Unknown role")
    problem = password_problem(body.password, username)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    with _factory(request).begin() as session:
        if session.scalar(select(OperatorAccount.id).where(OperatorAccount.username == username)) is not None:
            raise HTTPException(status_code=409, detail="That username already exists.")
        account = OperatorAccount(
            username=username,
            password_hash=hash_password(body.password, settings.auth_password_iterations),
            role=role,
            enabled=True,
        )
        session.add(account)
        session.flush()
        record(
            session, principal=caller.principal, action="operator.create", target=username,
            outcome="created", correlation_id=str(uuid.uuid4()), detail={"role": role},
        )
        return _operator_row(account)


@router.post("/operators/{username}/role")
def change_role(
    username: str,
    body: RoleChange,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
) -> dict[str, str]:
    _manage_operators(caller)
    role = body.role.strip().lower()
    if role not in _OPERATOR_ROLES:
        raise HTTPException(status_code=400, detail="Unknown role")
    if username.strip().lower() == caller.principal.strip().lower():
        raise HTTPException(status_code=403, detail="Authorization failed. An operator cannot change their own role.")
    with _factory(request).begin() as session:
        account = _account(session, username)
        account.role = role
        record(
            session,
            principal=caller.principal,
            action="operator.role",
            target=account.username,
            outcome="updated",
            correlation_id=str(uuid.uuid4()),
            detail={"role": role},
        )
        result = {"username": account.username, "role": account.role}
    forget(username)
    return result


@router.post("/operators/{username}/enabled")
def set_operator_enabled(
    username: str,
    body: OperatorEnabled,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
) -> dict[str, object]:
    """Disable or re-enable a local account. A disabled account is signed out on its next request."""
    _manage_operators(caller)
    _not_self(caller, username, "disable")
    with _factory(request).begin() as session:
        account = _account(session, username)
        account.enabled = body.enabled
        record(
            session, principal=caller.principal, action="operator.enabled", target=account.username,
            outcome="enabled" if body.enabled else "disabled", correlation_id=str(uuid.uuid4()),
            detail={"enabled": body.enabled},
        )
        result = _operator_row(account)
    forget(username)
    return result


@router.post("/operators/{username}/password")
def reset_operator_password(
    username: str,
    body: OperatorPassword,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    """Set a new password for another local account. Its open sessions are signed out."""
    _manage_operators(caller)
    _not_self(caller, username, "reset the password of")
    problem = password_problem(body.password, username)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    with _factory(request).begin() as session:
        account = _account(session, username)
        account.password_hash = hash_password(body.password, settings.auth_password_iterations)
        record(
            session, principal=caller.principal, action="operator.password", target=account.username,
            outcome="reset", correlation_id=str(uuid.uuid4()), detail={"self": False},
        )
        result: dict[str, object] = {"username": account.username, "updated": True}
    forget(username)
    return result


@router.post("/targets/{target_id}/credential-rotation")
def rotate_credential(
    target_id: uuid.UUID,
    body: CredentialRotation,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
) -> dict[str, str]:
    try:
        require(caller, "credential.rotate")
    except PermissionDenied as exc:
        raise _deny(exc) from exc
    reference = body.secret_reference.strip()
    if not reference.startswith("secret://") or not reference.removeprefix("secret://").split("/")[-1].isidentifier():
        raise HTTPException(status_code=400, detail="The credential must be a secret:// reference.")
    with _factory(request).begin() as session:
        target = session.get(DatabaseTarget, target_id)
        if target is None:
            raise HTTPException(status_code=404, detail="That database was not found.")
        credential = session.scalar(select(TargetCredential).where(TargetCredential.target_id == target.id))
        if credential is None:
            credential = TargetCredential(target_id=target.id, secret_reference=reference, username="GSYSTEM")
            session.add(credential)
        credential.secret_reference = reference
        credential.rotated_at = utcnow()
        session.flush()
        record(
            session,
            principal=caller.principal,
            action="credential.rotate",
            target=str(target.id),
            outcome="rotated",
            correlation_id=str(uuid.uuid4()),
            detail={"secret_reference": reference, "username": credential.username or "GSYSTEM"},
        )
        return {"target_id": str(target.id), "secret_reference": reference}


@router.get("/config")
def read_config(
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
) -> dict[str, object]:
    try:
        require(caller, "platform.read")
    except PermissionDenied as exc:
        raise _deny(exc) from exc
    with _factory(request)() as session:
        return {"items": list_public_config(session)}


@router.put("/config/{key}")
def write_config(
    key: str,
    body: ConfigChange,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    try:
        require(caller, "config.write")
    except PermissionDenied as exc:
        _audit_config(request, caller, key, "denied")
        raise _deny(exc) from exc
    with _factory(request).begin() as session:
        try:
            stored = update_public_config(session, settings, key, body.value)
        except ConfigUpdateError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        record(
            session,
            principal=caller.principal,
            action="config.update",
            target=key,
            outcome="updated",
            correlation_id=str(uuid.uuid4()),
            detail={"key": key},
        )
        changed = session.get(AppConfig, key)
        return {"key": key, "value": stored, "updated_at": changed.updated_at.isoformat() if changed else ""}


def _audit_config(request: Request, caller: Caller, key: str, outcome: str) -> None:
    try:
        with _factory(request).begin() as session:
            record(
                session,
                principal=caller.principal,
                action="config.update",
                target=key,
                outcome=outcome,
                correlation_id=str(uuid.uuid4()),
                detail={"key": key},
            )
    except HTTPException:
        return
