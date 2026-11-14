"""Private runtime helper for Verify_Phase15_Real.ps1. Never prints credentials."""

import argparse
import json
import logging
import os
import secrets
import uuid
from pathlib import Path

import httpx
import oracledb
from sentinel.auth.passwords import hash_password
from sentinel.config.settings import Settings
from sentinel.persistence.models import OperatorAccount
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)
PHASE_TABLES = (
    "operation_proposals",
    "database_entitlements",
    "audit_events",
    "operator_accounts",
    "privileged_operations",
    "app_config",
)
OPERATOR_USERNAME = "phase15-operator"


def load_settings() -> Settings:
    """Load root credentials plus Phase 15 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase15\\.env are required")
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if not settings.auth_required:
        raise ValueError("Live Phase 15 verification requires AUTH_REQUIRED=true")
    if settings.auth_oidc_issuer.strip() or settings.auth_oidc_client_id.strip():
        raise ValueError("Live Phase 15 verification leaves OIDC unset")
    if settings.ai_enabled or settings.ai_provider != "disabled":
        raise ValueError("Live Phase 15 verification requires AI_ENABLED=false and AI_PROVIDER=disabled")
    if settings.ai_fallback_provider != "disabled" or settings.ai_hosted_approved:
        raise ValueError("Live Phase 15 verification does not approve a hosted model or a fallback")
    if settings.operations_execution_enabled or settings.operations_execution_approved:
        raise ValueError("Live Phase 15 verification keeps privileged execution disabled")
    if make_url(settings.database_url).get_backend_name() != "postgresql":
        raise ValueError("Live verification requires a PostgreSQL DATABASE_URL")
    tns = Path(settings.tns_admin or "tns")
    if not tns.is_absolute():
        tns = ROOT / tns
    if not (tns / "tnsnames.ora").is_file():
        raise ValueError("TNS_ADMIN must contain tnsnames.ora")
    settings.tns_admin = str(tns.resolve())
    if not settings.oracle_password and not settings.oracle_password_secret_ref:
        raise ValueError("Configure ORACLE_PASSWORD or ORACLE_PASSWORD_SECRET_REF in the root .env")
    if not settings.auth_bootstrap_username.strip() or len(settings.auth_bootstrap_password) < 8:
        raise ValueError("Configure the local bootstrap account in the root .env")
    os.environ["TNS_ADMIN"] = settings.tns_admin
    return settings


def migrate(settings: Settings) -> None:
    from alembic import command
    from alembic.config import Config

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            if "database_targets" in tables and "alembic_version" not in tables:
                raise ValueError(
                    "Unversioned existing schema detected. Back up and compare the Phase 1 baseline "
                    "before manually stamping 0001; this launcher never stamps automatically."
                )
    finally:
        engine.dispose()
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    command.upgrade(config, "head")
    print("Application schema upgraded to Alembic head (through Phase 15, revision 0010).", flush=True)


def schema_status(settings: Settings) -> None:
    """Report the revision and required tables. No row contents."""
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            tables = set(inspect(connection).get_table_names())
            stored = 0
            if "privileged_operations" in tables:
                stored = int(connection.execute(text("SELECT COUNT(*) FROM privileged_operations")).scalar() or 0)
    finally:
        engine.dispose()
    missing = [name for name in PHASE_TABLES if name not in tables]
    if str(version) != "0010" or missing or stored != 0:
        raise ValueError("Phase 15 schema is not at revision 0010 with an empty privileged table")
    print(json.dumps({"alembic_head": "0010", "privileged_rows": stored}), flush=True)


def write_session(settings: Settings, cookie_file: Path) -> None:
    """Sign in locally and store only the session cookie. The password is not printed."""
    response = httpx.post(
        "http://127.0.0.1:8000/api/v1/auth/login",
        json={
            "username": settings.auth_bootstrap_username,
            "password": settings.auth_bootstrap_password,
            "local": True,
        },
        timeout=30.0,
        trust_env=False,
        follow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError("Local sign-in failed")
    cookie = response.cookies.get("sentinel_session", "")
    if not cookie:
        raise ValueError("Local sign-in did not set a session cookie")
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    cookie_file.write_text(cookie, encoding="utf-8")
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError("Local sign-in returned an unexpected response")
    role = str(body.get("role") or "")
    if role not in {"admin", "platform_admin"}:
        raise ValueError("The bootstrap account is not an administrator")
    print(json.dumps({
        "principal": str(body.get("principal") or ""),
        "role": role,
        "method": str(body.get("method") or ""),
    }), flush=True)


def _operator_denied(settings: Settings, storage_url: str) -> None:
    """Sign in as a temporary operator and confirm the management reading returns 403. The password is not printed."""
    password = secrets.token_urlsafe(24)
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            account = session.scalar(select(OperatorAccount).where(OperatorAccount.username == OPERATOR_USERNAME))
            digest = hash_password(password, settings.auth_password_iterations)
            if account is None:
                session.add(OperatorAccount(
                    username=OPERATOR_USERNAME, password_hash=digest, role="operator", enabled=True,
                ))
            else:
                account.password_hash = digest
                account.role = "operator"
                account.enabled = True
            session.commit()
        try:
            response = httpx.post(
                "http://127.0.0.1:8000/api/v1/auth/login",
                json={"username": OPERATOR_USERNAME, "password": password, "local": True},
                timeout=30.0,
                trust_env=False,
                follow_redirects=False,
            )
            if response.status_code != 200:
                raise ValueError("The temporary operator could not sign in")
            denied = httpx.get(
                storage_url,
                cookies={"sentinel_session": response.cookies.get("sentinel_session", "")},
                timeout=30.0,
                trust_env=False,
            )
            if denied.status_code != 403:
                raise ValueError("An operator was not denied the management reading")
        finally:
            with Session(engine) as session:
                account = session.scalar(select(OperatorAccount).where(OperatorAccount.username == OPERATOR_USERNAME))
                if account is not None:
                    session.delete(account)
                    session.commit()
    finally:
        engine.dispose()


def _percent(value: object, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= float(value) <= 100:
        raise ValueError(f"{label} percent was outside 0 to 100")
    return float(value)


def management_checks(settings: Settings, cookie_file: Path, target_id: str) -> None:
    """Confirm SGA meters, recovery, archive, and backup. Names and paths are not printed."""
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie or not target_id.strip():
        raise ValueError("management requires a session cookie and a target id")
    memory_path = f"/api/v1/databases/{target_id}/graph/memory"
    anonymous = httpx.get(f"http://127.0.0.1:8000{memory_path}", timeout=30.0, trust_env=False)
    if anonymous.status_code != 401:
        raise ValueError("Memory was readable before sign-in")
    with httpx.Client(
        base_url="http://127.0.0.1:8000",
        cookies={"sentinel_session": cookie},
        timeout=180.0,
        trust_env=False,
    ) as admin:
        reading = admin.get(memory_path)
        if reading.status_code != 200:
            raise ValueError("Administrator memory request was not accepted")
        body = reading.json()
        if not isinstance(body, dict):
            raise TypeError("Memory did not return an object")
        if "sql" in body or "from v$" in reading.text.lower():
            raise ValueError("The memory response included SQL")
        if not isinstance(body.get("sga", {}).get("items"), list):
            raise TypeError("SGA pools were not a list")
        hit = body.get("hit_ratio")
        shared = body.get("shared_pool_used")
        if not isinstance(hit, dict) or not isinstance(shared, dict):
            raise TypeError("SGA meters were not returned")
        hit_percent = _percent(hit.get("percent"), "Hit ratio")
        if hit_percent is None:
            if hit.get("caption") != "Not collected.":
                raise ValueError("A missing hit ratio was given a caption")
            hit_state = "limited"
        elif hit.get("caption") != "Since instance startup.":
            raise ValueError("Hit ratio was not labeled since instance startup")
        else:
            hit_state = "measured"
        shared_percent = _percent(shared.get("percent"), "Shared pool")
        shared_caption = str(shared.get("caption") or "")
        if shared_percent is None:
            if "V$SGASTAT" not in shared_caption and "V_$SGASTAT" not in shared_caption and shared_caption != "Not collected.":
                raise ValueError("Shared pool used was neither a percent nor a named limitation")
            shared_state = "limited"
        elif shared_caption != "Shared pool used.":
            raise ValueError("Shared pool used was not captioned")
        else:
            shared_state = "measured"
        if body.get("result_cache") != "Not collected.":
            raise ValueError("Result cache was given a value")
        guard = body.get("data_guard")
        if not isinstance(guard, dict) or not isinstance(guard.get("stats"), list):
            raise TypeError("Data Guard was not returned")
        if not guard["stats"] and not guard.get("limitation"):
            raise ValueError("Data Guard returned neither stats nor a limitation")
        if not guard["stats"] and guard.get("limitation") == "Not a Data Guard database.":
            guard_state = "not_guard"
        elif guard.get("limitation"):
            guard_state = "limited"
        else:
            guard_state = "stats"
        storage = admin.get(f"/api/v1/databases/{target_id}/graph/storage")
        if storage.status_code != 200:
            raise ValueError("Administrator storage request was not accepted")
        stored = storage.json()
        if not isinstance(stored, dict) or "from v$" in storage.text.lower():
            raise ValueError("The storage response included SQL")
        recovery = stored.get("recovery_area")
        archive = stored.get("archive")
        backup = stored.get("backup")
        if not all(isinstance(item, dict) for item in (recovery, archive, backup)):
            raise TypeError("Recovery, archive, and backup were not returned")
        if not isinstance(recovery.get("present"), bool):
            raise TypeError("Recovery did not say whether a destination was returned")
        if recovery["present"]:
            _percent(recovery.get("used_percent"), "Recovery")
            recovery_state = "present"
        else:
            if recovery.get("used_percent") is not None or not recovery.get("limitation"):
                raise ValueError("An empty recovery area was drawn as used")
            recovery_state = "limited"
        if archive.get("time_to_failure") != "Not collected." or archive.get("free_minutes") != "Not collected.":
            raise ValueError("Archive time was given a value")
        if backup.get("limitation"):
            if backup.get("failed") is not False:
                raise ValueError("A missing backup was marked failed")
            backup_state = "limited"
        elif not backup.get("status") or not isinstance(backup.get("failed"), bool):
            raise ValueError("Backup returned neither a status nor a limitation")
        else:
            backup_state = "failed" if backup["failed"] else "returned"
        if not body.get("collected_at") or not isinstance(body.get("limitations"), list):
            raise ValueError("Memory did not report a collection time")
        missing = admin.get(f"/api/v1/databases/{uuid.uuid4()}/graph/memory")
        if missing.status_code != 404:
            raise ValueError("A missing database did not return not-found")
    _operator_denied(settings, f"http://127.0.0.1:8000{memory_path}")
    print(json.dumps({
        "management": "snapshot",
        "hit_ratio": hit_state,
        "shared_pool": shared_state,
        "result_cache": "not_collected",
        "data_guard": guard_state,
        "recovery": recovery_state,
        "backup": backup_state,
        "operator": "denied",
        "anonymous": "signed_out",
        "missing_database": "not_found",
    }), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "schema", "session", "management", "serve"))
    parser.add_argument("--cookie-file", help="File that receives or supplies the session cookie")
    parser.add_argument("--target-id", help="Database target id for the management checks")
    args = parser.parse_args()
    settings = load_settings()
    if args.action == "preflight":
        aliases = oracledb.ConnectParams(config_dir=settings.tns_admin).get_network_service_names()
        if not aliases:
            raise ValueError("No TNS aliases found in tnsnames.ora")
        print(json.dumps({
            "aliases": sorted(aliases),
            "auth_required": settings.auth_required,
            "oidc_configured": bool(settings.auth_oidc_issuer.strip()),
            "ai_enabled": settings.ai_enabled,
            "ai_provider": settings.ai_provider,
            "ai_fallback_provider": settings.ai_fallback_provider,
            "ai_hosted_approved": settings.ai_hosted_approved,
            "scheduler_enabled": settings.scheduler_enabled,
        }), flush=True)
    elif args.action == "migrate":
        migrate(settings)
    elif args.action == "schema":
        schema_status(settings)
    elif args.action == "session":
        if not args.cookie_file or not args.cookie_file.strip():
            raise ValueError("session requires --cookie-file")
        write_session(settings, Path(args.cookie_file))
    elif args.action == "management":
        if not args.cookie_file or not args.cookie_file.strip() or not args.target_id or not args.target_id.strip():
            raise ValueError("management requires --cookie-file and --target-id")
        management_checks(settings, Path(args.cookie_file), args.target_id)
    else:
        import uvicorn
        from sentinel.main import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    try:
        main()
    except (ValueError, TypeError, SQLAlchemyError, OSError, oracledb.Error, httpx.HTTPError) as exc:
        if type(exc) in {ValueError, TypeError}:
            logger.error("%s", exc)
        else:
            logger.error(
                "Live preparation failed (%s). Check database access and local configuration.",
                type(exc).__name__,
            )
        raise SystemExit(1) from None
