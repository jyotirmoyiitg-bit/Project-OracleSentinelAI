"""Private runtime helper for Verify_Phase14_Real.ps1. Never prints credentials."""

import argparse
import json
import logging
import os
import re
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
OPERATOR_USERNAME = "phase14-operator"
_LABELED_SIZE = re.compile(r"^\d+(?:\.\d+)? (?:MB|GB)$")


def load_settings() -> Settings:
    """Load root credentials plus Phase 14 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase14\\.env are required")
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if not settings.auth_required:
        raise ValueError("Live Phase 14 verification requires AUTH_REQUIRED=true")
    if settings.auth_oidc_issuer.strip() or settings.auth_oidc_client_id.strip():
        raise ValueError("Live Phase 14 verification leaves OIDC unset")
    if settings.ai_enabled or settings.ai_provider != "disabled":
        raise ValueError("Live Phase 14 verification requires AI_ENABLED=false and AI_PROVIDER=disabled")
    if settings.ai_fallback_provider != "disabled" or settings.ai_hosted_approved:
        raise ValueError("Live Phase 14 verification does not approve a hosted model or a fallback")
    if settings.operations_execution_enabled or settings.operations_execution_approved:
        raise ValueError("Live Phase 14 verification keeps privileged execution disabled")
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
    print("Application schema upgraded to Alembic head (through Phase 14, revision 0010).", flush=True)


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
        raise ValueError("Phase 14 schema is not at revision 0010 with an empty privileged table")
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
    """Sign in as a temporary operator and confirm the activity reading returns 403. The password is not printed."""
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
                raise ValueError("An operator was not denied the activity reading")
        finally:
            with Session(engine) as session:
                account = session.scalar(select(OperatorAccount).where(OperatorAccount.username == OPERATOR_USERNAME))
                if account is not None:
                    session.delete(account)
                    session.commit()
    finally:
        engine.dispose()


def _present_or_limited(rows: object, limitation: object, label: str) -> int:
    if not isinstance(rows, list):
        raise TypeError(f"{label} was not a list")
    if len(rows) == 0 and not limitation:
        raise ValueError(f"{label} returned neither rows nor a limitation")
    return len(rows)


_UPTIME = re.compile(r"^\d+d \d+h \d+m$")
_OVALS = ("Dedicated", "Shared", "Dispatchers", "Parallel query", "Job queue", "Total")


def activity_checks(settings: Settings, cookie_file: Path, target_id: str) -> None:
    """Confirm service, host, process counts, and the segment list. Names and paths are not printed."""
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie or not target_id.strip():
        raise ValueError("activity requires a session cookie and a target id")
    activity_path = f"/api/v1/databases/{target_id}/graph/activity"
    anonymous = httpx.get(f"http://127.0.0.1:8000{activity_path}", timeout=30.0, trust_env=False)
    if anonymous.status_code != 401:
        raise ValueError("Activity was readable before sign-in")
    with httpx.Client(
        base_url="http://127.0.0.1:8000",
        cookies={"sentinel_session": cookie},
        timeout=180.0,
        trust_env=False,
    ) as admin:
        reading = admin.get(activity_path)
        if reading.status_code != 200:
            raise ValueError("Administrator activity request was not accepted")
        body = reading.json()
        if not isinstance(body, dict):
            raise TypeError("Activity did not return an object")
        if "sql" in body or "from v$" in reading.text.lower():
            raise ValueError("The activity response included SQL")
        service = {line.get("label"): line.get("value") for line in body.get("service") or [] if isinstance(line, dict)}
        if service.get("Response") != "Not collected." or service.get("Average active") != "Not collected.":
            raise ValueError("Response or average active was given a value")
        uptime = str(service.get("Uptime") or "")
        if not uptime:
            raise ValueError("Uptime was not returned")
        host = body.get("host")
        if not isinstance(host, dict):
            raise TypeError("Host was not returned")
        cpu = host.get("cpu_percent")
        if cpu is None:
            if host.get("cpu_caption") != "The host did not return that statistic.":
                raise ValueError("A missing CPU statistic was not named")
        elif isinstance(cpu, bool) or not isinstance(cpu, int | float) or not 0 <= cpu <= 100:
            raise ValueError("CPU percent was outside 0 to 100")
        elif host.get("cpu_caption") != "Since instance startup.":
            raise ValueError("CPU was not labeled since instance startup")
        processes = body.get("server_processes")
        if not isinstance(processes, list):
            raise TypeError("Server processes were not a list")
        names = [item.get("name") for item in processes if isinstance(item, dict)]
        if names != list(_OVALS) or "EM Agent" in names:
            raise ValueError("Server process ovals did not match the fixed counts")
        for item in processes:
            if not isinstance(item, dict):
                raise TypeError("A process oval was not an object")
            count = item.get("count")
            if count is None and not item.get("limitation"):
                raise ValueError("A process oval was neither a count nor a limitation")
            if count is not None and (isinstance(count, bool) or not isinstance(count, int)):
                raise TypeError("A process oval count was not a whole number")
        storage = admin.get(f"/api/v1/databases/{target_id}/graph/storage")
        if storage.status_code != 200:
            raise ValueError("Administrator storage request was not accepted")
        stored = storage.json()
        segments = stored.get("segments") if isinstance(stored, dict) else None
        if not isinstance(segments, dict):
            raise TypeError("Segments were not returned")
        rows = segments.get("rows")
        segment_count = _present_or_limited(rows, segments.get("limitation"), "Segments")
        if segment_count > 20:
            raise ValueError("The segment list has more than 20 rows")
        for item in rows or []:
            display = str(item.get("display") or "") if isinstance(item, dict) else ""
            if display and not _LABELED_SIZE.match(display):
                raise ValueError("A segment size was not labeled in MB or GB")
        if "from v$" in storage.text.lower():
            raise ValueError("The storage response included SQL")
        if not body.get("collected_at") or not isinstance(body.get("limitations"), list):
            raise ValueError("Activity did not report a collection time")
        missing = admin.get(f"/api/v1/databases/{uuid.uuid4()}/graph/activity")
        if missing.status_code != 404:
            raise ValueError("A missing database did not return not-found")
    _operator_denied(settings, f"http://127.0.0.1:8000{activity_path}")
    print(json.dumps({
        "activity": "snapshot",
        "uptime": uptime if _UPTIME.match(uptime) else "limited",
        "response": "not_collected",
        "average_active": "not_collected",
        "process_ovals": len(processes),
        "segments": segment_count,
        "limitations": len(body.get("limitations") or []),
        "operator": "denied",
        "anonymous": "signed_out",
        "missing_database": "not_found",
    }), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "schema", "session", "activity", "serve"))
    parser.add_argument("--cookie-file", help="File that receives or supplies the session cookie")
    parser.add_argument("--target-id", help="Database target id for the activity checks")
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
    elif args.action == "activity":
        if not args.cookie_file or not args.cookie_file.strip() or not args.target_id or not args.target_id.strip():
            raise ValueError("activity requires --cookie-file and --target-id")
        activity_checks(settings, Path(args.cookie_file), args.target_id)
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
