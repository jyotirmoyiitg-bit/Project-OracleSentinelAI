"""Sign-in: directory first, then a local account when the directory cannot be used."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.auth.directory import authenticate_directory
from sentinel.auth.passwords import hash_password, verify_password
from sentinel.config.settings import Settings
from sentinel.knowledge.constants import ALLOWED_ROLES
from sentinel.persistence.models import OperatorAccount


class LoginDenied(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def ensure_bootstrap_operator(session: Session, settings: Settings) -> None:
    username = settings.auth_bootstrap_username.strip().lower()
    password = settings.auth_bootstrap_password
    if not username or not password:
        return
    if settings.auth_bootstrap_role not in ALLOWED_ROLES:
        return
    existing = session.scalar(select(OperatorAccount).where(OperatorAccount.username == username))
    if existing is not None:
        return
    session.add(OperatorAccount(
        username=username,
        password_hash=hash_password(password, settings.auth_password_iterations),
        role=settings.auth_bootstrap_role,
        enabled=True,
    ))


def authenticate(
    session: Session, settings: Settings, username: str, password: str, *, use_local: bool,
) -> tuple[str, str, str]:
    account_name = username.strip()
    if not account_name or not password or len(account_name) > 80:
        raise LoginDenied("Sign-in failed.", 401)
    if use_local:
        local = _local_account(session, account_name, password)
        if local is None:
            raise LoginDenied("Sign-in failed.", 401)
        return local.username, local.role, "local"
    directory = authenticate_directory(settings, account_name, password)
    if directory.status == "authenticated":
        return directory.principal, directory.role, "ad"
    if directory.status == "not_operator":
        raise LoginDenied("This directory account is not an operator.", 403)
    if directory.status == "unavailable":
        raise LoginDenied("The directory is unavailable. Check Local to sign in with a local account.", 401)
    if directory.status == "not_configured":
        raise LoginDenied("Active Directory is not configured. Check Local to sign in with a local account.", 401)
    raise LoginDenied("Sign-in failed.", 401)


def _local_account(session: Session, username: str, password: str) -> OperatorAccount | None:
    account = session.scalar(select(OperatorAccount).where(OperatorAccount.username == username.strip().lower()))
    if account is None or not account.enabled:
        return None
    if not verify_password(password, account.password_hash):
        return None
    return account
