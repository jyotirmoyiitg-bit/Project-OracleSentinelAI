"""Private runtime helper for Verify_Phase16_Real.ps1. Never prints credentials."""

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
OPERATOR_USERNAME = "phase16-operator"


def load_settings() -> Settings:
    """Load root credentials plus Phase 16 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase16\\.env are required")
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if not settings.auth_required:
        raise ValueError("Live Phase 16 verification requires AUTH_REQUIRED=true")
    if settings.auth_oidc_issuer.strip() or settings.auth_oidc_client_id.strip():
        raise ValueError("Live Phase 16 verification leaves OIDC unset")
    if settings.ai_enabled or settings.ai_provider != "disabled":
        raise ValueError("Live Phase 16 verification requires AI_ENABLED=false and AI_PROVIDER=disabled")
    if settings.ai_fallback_provider != "disabled" or settings.ai_hosted_approved:
        raise ValueError("Live Phase 16 verification does not approve a hosted model or a fallback")
    if settings.operations_execution_enabled or settings.operations_execution_approved:
        raise ValueError("Live Phase 16 verification keeps privileged execution disabled")
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
    print("Application schema upgraded to Alembic head (revision 0010).", flush=True)


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
        raise ValueError("Schema is not at revision 0010 with an empty privileged table")
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


def _operator_denied(settings: Settings, urls: tuple[str, ...]) -> None:
    """Sign in as a temporary operator and confirm each reading returns 403. The password is not printed."""
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
            cookie = response.cookies.get("sentinel_session", "")
            for url in urls:
                denied = httpx.get(url, cookies={"sentinel_session": cookie}, timeout=30.0, trust_env=False)
                if denied.status_code != 403:
                    raise ValueError("An operator was not denied a GraphDB reading")
        finally:
            with Session(engine) as session:
                account = session.scalar(select(OperatorAccount).where(OperatorAccount.username == OPERATOR_USERNAME))
                if account is not None:
                    session.delete(account)
                    session.commit()
    finally:
        engine.dispose()


def _service_value(lines: object, label: str) -> str:
    if not isinstance(lines, list):
        raise TypeError("Activity service was not a list")
    for line in lines:
        if isinstance(line, dict) and line.get("label") == label:
            return str(line.get("value") or "")
    raise ValueError(f"Activity did not return {label}")


def _reading_state(payload: dict[str, object], key: str, rows_key: str) -> str:
    section = payload.get(key)
    if not isinstance(section, dict):
        raise TypeError(f"{key} was not returned")
    rows = section.get(rows_key)
    limitation = section.get("limitation")
    if isinstance(rows, list) and rows:
        return "returned"
    if isinstance(limitation, str) and limitation.strip():
        return "limited"
    raise ValueError(f"{key} returned neither rows nor a named limitation")


def readings_checks(settings: Settings, cookie_file: Path, target_id: str) -> None:
    """Confirm memory, storage, and activity. Names, paths, and SQL are not printed."""
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie or not target_id.strip():
        raise ValueError("readings requires a session cookie and a target id")
    base = f"/api/v1/databases/{target_id}/graph"
    memory_path = f"{base}/memory"
    storage_path = f"{base}/storage"
    activity_path = f"{base}/activity"
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
        memory_state = _reading_state(body, "sga", "items")
        storage = admin.get(storage_path)
        if storage.status_code != 200:
            raise ValueError("Administrator storage request was not accepted")
        stored = storage.json()
        if not isinstance(stored, dict) or "from v$" in storage.text.lower():
            raise ValueError("The storage response included SQL")
        storage_state = _reading_state(stored, "data_files", "files")
        activity = admin.get(activity_path)
        if activity.status_code != 200:
            raise ValueError("Administrator activity request was not accepted")
        active = activity.json()
        if not isinstance(active, dict) or "from v$" in activity.text.lower():
            raise ValueError("The activity response included SQL")
        response = _service_value(active.get("service"), "Response")
        average = _service_value(active.get("service"), "Average active")
        if response != "Not collected." or average != "Not collected.":
            raise ValueError("Response or average active was given a value")
        counters = active.get("counters")
        expected = ("physical reads", "redo entries", "parse count (total)", "execute count")
        if not isinstance(counters, list) or tuple(item.get("name") for item in counters if isinstance(item, dict)) != expected:
            raise ValueError("Activity did not return the four rate counters")
        if not body.get("collected_at") or not isinstance(body.get("limitations"), list):
            raise ValueError("Memory did not report a collection time")
        missing = admin.get(f"/api/v1/databases/{uuid.uuid4()}/graph/memory")
        if missing.status_code != 404:
            raise ValueError("A missing database did not return not-found")
    _operator_denied(settings, (
        f"http://127.0.0.1:8000{memory_path}",
        f"http://127.0.0.1:8000{storage_path}",
        f"http://127.0.0.1:8000{activity_path}",
    ))
    print(json.dumps({
        "readings": "snapshot",
        "memory": memory_state,
        "storage": storage_state,
        "activity": "service",
        "response": "not_collected",
        "average_active": "not_collected",
        "counters": len(expected),
        "operator": "denied",
        "anonymous": "signed_out",
        "missing_database": "not_found",
    }), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "schema", "session", "readings", "serve"))
    parser.add_argument("--cookie-file", help="File that receives or supplies the session cookie")
    parser.add_argument("--target-id", help="Database target id for the reading checks")
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
    elif args.action == "readings":
        if not args.cookie_file or not args.cookie_file.strip() or not args.target_id or not args.target_id.strip():
            raise ValueError("readings requires --cookie-file and --target-id")
        readings_checks(settings, Path(args.cookie_file), args.target_id)
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
