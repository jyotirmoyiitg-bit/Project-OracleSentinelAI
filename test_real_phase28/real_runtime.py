"""Private runtime helper for Verify_Phase28_Real.ps1. Never prints credentials, user names, or SQL text."""

import argparse
import json
import logging
import os
import re
import secrets
from pathlib import Path

import httpx
import oracledb
from sentinel.config.settings import Settings
from sentinel.persistence.models import OperatorAccount
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"
logger = logging.getLogger(__name__)
PHASE_TABLES = (
    "operation_proposals",
    "database_entitlements",
    "audit_events",
    "operator_accounts",
    "privileged_operations",
    "app_config",
    "agent_runs",
    "agent_steps",
    "evidence_snapshots",
    "agent_findings",
    "agent_database_settings",
)
OPERATOR_USERNAME = "phase28-operator"
# The check opens its own Oracle session, tags it, and runs one marked statement, so SessionDB has a known row.
CHECK_MODULE = "Sentinel Phase 28 check"
CHECK_MARKER = "sentinel-phase28-check"
CHECK_SQL = f"SELECT /* {CHECK_MARKER} */ COUNT(*) FROM dual"
STATEMENT_NOTE = "The statement of a session and its plan are shown to an administrator."
PLAN_GRANT = "SYS.V_$SQL_PLAN"


def load_settings() -> Settings:
    """Load root credentials plus Phase 28 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase28\\.env are required")
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if not settings.auth_required:
        raise ValueError("Live Phase 28 verification requires AUTH_REQUIRED=true")
    if settings.auth_oidc_issuer.strip() or settings.auth_oidc_client_id.strip():
        raise ValueError("Live Phase 28 verification leaves OIDC unset")
    if settings.ai_provider == "hosted" or settings.ai_fallback_provider != "disabled" or settings.ai_hosted_approved:
        raise ValueError("Live Phase 28 verification does not approve a hosted model or a fallback")
    if settings.operations_execution_enabled or settings.operations_execution_approved:
        raise ValueError("Live Phase 28 verification keeps privileged execution disabled")
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
    print("Application schema upgraded to Alembic head (revision 0012 or later).", flush=True)


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
    if str(version) < "0012" or missing or stored != 0:
        raise ValueError("Schema is not at revision 0012 or later with an empty privileged table")
    print(json.dumps({"alembic_head": str(version), "privileged_rows": stored}), flush=True)


def write_session(settings: Settings, cookie_file: Path) -> None:
    """Sign in locally and store only the session cookie. The password is not printed."""
    response = httpx.post(
        f"{BASE}/api/v1/auth/login",
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
        "directory_enabled": bool(body.get("directory_enabled")),
    }), flush=True)


def _remove_operator(settings: Settings) -> None:
    """Remove the temporary operator account. Audit rows are kept."""
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            account = session.scalar(select(OperatorAccount).where(OperatorAccount.username == OPERATOR_USERNAME))
            if account is not None:
                session.delete(account)
                session.commit()
    finally:
        engine.dispose()


def _client(cookie: str) -> httpx.Client:
    # A fleet question reads every enabled database, so one answer can take several minutes.
    return httpx.Client(base_url=BASE, cookies={"sentinel_session": cookie}, timeout=900.0, trust_env=False)


def _sign_in(username: str, password: str) -> httpx.Response:
    return httpx.post(
        f"{BASE}/api/v1/auth/login",
        json={"username": username, "password": password, "local": True},
        timeout=30.0, trust_env=False, follow_redirects=False,
    )


def _expect(response: httpx.Response, status: int, what: str) -> None:
    if response.status_code != status:
        raise ValueError(f"{what}: expected HTTP {status}, received {response.status_code}")




def _open_check_session(settings: Settings, alias: str) -> tuple[oracledb.Connection, int, int]:
    """Open one tagged Oracle session and run the marked statement last, so it is that session's last statement."""
    import asyncio

    from sentinel.adapters.oracle.connection import OracleConnectionManager
    from sentinel.config.secrets import init_resolver

    init_resolver(settings.secret_key)
    connection = asyncio.run(OracleConnectionManager(settings).connect(alias))
    connection.module = CHECK_MODULE
    connection.action = "probe"
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT TO_NUMBER(SYS_CONTEXT('USERENV', 'SID')) FROM dual")
        sid = int(cursor.fetchone()[0])
        cursor.execute("SELECT serial# FROM v$session WHERE sid = TO_NUMBER(SYS_CONTEXT('USERENV', 'SID'))")
        serial = int(cursor.fetchone()[0])
        cursor.execute(CHECK_SQL)
        cursor.fetchone()
    finally:
        cursor.close()
    return connection, sid, serial


def _find(listing: dict[str, object], sid: int, serial: int) -> tuple[str, dict[str, object]] | None:
    for group in listing.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for row in group.get("sessions") or []:
            if isinstance(row, dict) and row.get("sid") == sid and row.get("serial") == serial:
                return str(group.get("program") or ""), row
    return None


def _names_shown(listing: dict[str, object]) -> bool:
    return any(
        row.get("username") or row.get("os_user") or row.get("terminal")
        for group in listing.get("groups") or [] if isinstance(group, dict)
        for row in group.get("sessions") or [] if isinstance(row, dict)
    )


def session_checks(settings: Settings, cookie_file: Path, target_id: str, alias: str, require_plan: bool) -> None:
    """Confirm SessionDB against the running API and the real database. Prints counts and states only."""
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie or not target_id.strip() or not alias.strip():
        raise ValueError("checks requires a session cookie, a target id, and a TNS alias")
    _remove_operator(settings)
    password = secrets.token_urlsafe(18) + "a1"
    result: dict[str, object] = {}
    connection, sid, serial = _open_check_session(settings, alias)
    try:
        with _client(cookie) as admin:
            listed = admin.get(f"/api/v1/databases/{target_id}/sessions")
            _expect(listed, 200, "Reading SessionDB")
            listing = listed.json()
            if listing.get("limitation"):
                raise ValueError(f"SessionDB could not read the sessions: {listing['limitation']}")
            if listing.get("detail") is not True:
                raise ValueError("An administrator was not shown session details")
            found = _find(listing, sid, serial)
            if found is None:
                raise ValueError("SessionDB did not list the session the check opened")
            program, row = found
            if row.get("module") != CHECK_MODULE:
                raise ValueError("The check session was listed without its module")
            if str(row.get("username") or "").upper() != settings.oracle_username.upper():
                raise ValueError("The check session was listed under another user")
            if not row.get("logon_time") or not row.get("last_call") or not program:
                raise ValueError("The check session was listed without its program, logon time, or last call")
            if row.get("status") not in {"ACTIVE", "INACTIVE"}:
                raise ValueError("The check session has no status")
            result["listed"] = "yes"
            result["sessions"] = int(listing.get("total") or 0)
            result["programs"] = int(listing.get("programs") or 0)
            result["active"] = int(listing.get("active") or 0)

            asked = admin.get(f"/api/v1/databases/{target_id}/sessions/{sid}/statement", params={"serial": serial})
            _expect(asked, 200, "Reading the statement of the check session")
            statement = asked.json()
            if statement.get("limitation"):
                raise ValueError(f"The statement could not be read: {statement['limitation']}")
            if not statement.get("found") or statement.get("kind") != "previous":
                raise ValueError("The idle check session did not show its last statement")
            if CHECK_MARKER not in str(statement.get("text") or ""):
                raise ValueError("The statement shown was not the one the check session ran last")
            if not re.fullmatch(r"[0-9a-z]{13}", str(statement.get("sql_id") or "")):
                raise ValueError("The statement was shown without its SQL_ID")
            result["statement"] = "read"
            steps = statement.get("plan") or []
            note = str(statement.get("plan_note") or "")
            if steps:
                first = steps[0] if isinstance(steps[0], dict) else {}
                if not str(first.get("operation") or "").startswith("SELECT STATEMENT"):
                    raise ValueError("The cached plan does not start with the statement")
                result["plan"] = "read"
                result["plan_steps"] = len(steps)
            elif PLAN_GRANT in note:
                result["plan"] = "not_granted"
            else:
                result["plan"] = "none_cached"
            if require_plan and result["plan"] != "read":
                raise ValueError(
                    f"The cached plan was not read ({result['plan']}). Run scripts\\oracle\\02_grant_readonly_monitoring.sql "
                    "as SYSDBA in this database, then run again."
                )

            stale = admin.get(f"/api/v1/databases/{target_id}/sessions/{sid}/statement", params={"serial": serial + 1})
            _expect(stale, 200, "Reading a session that does not exist")
            if stale.json().get("found"):
                raise ValueError("A SID with the wrong serial number showed a statement")
            _expect(admin.get(f"/api/v1/databases/{target_id}/sessions/{sid}/statement"), 422,
                    "A statement without a serial number")

            try:
                _expect(admin.post("/api/v1/admin/operators", json={
                    "username": OPERATOR_USERNAME, "password": password, "role": "operator",
                }), 201, "Creating the temporary operator")
                signed = _sign_in(OPERATOR_USERNAME, password)
                _expect(signed, 200, "Operator sign-in")
                with _client(signed.cookies.get("sentinel_session", "")) as operator:
                    seen = operator.get(f"/api/v1/databases/{target_id}/sessions")
                    _expect(seen, 200, "An operator reading SessionDB")
                    trimmed = seen.json()
                    if trimmed.get("detail") is not False or _names_shown(trimmed):
                        raise ValueError("An operator was shown user names, OS users, or terminals")
                    if _find(trimmed, sid, serial) is None:
                        raise ValueError("An operator was not shown the check session")
                    refused = operator.get(f"/api/v1/databases/{target_id}/sessions/{sid}/statement",
                                           params={"serial": serial})
                    _expect(refused, 403, "An operator asking for a statement")
                    if refused.json().get("detail") != STATEMENT_NOTE:
                        raise ValueError("An operator was refused a statement without the reason")
                result["operator"] = "trimmed_and_refused"
            finally:
                _remove_operator(settings)
        result["restored"] = "yes"
    finally:
        connection.close()
    print(json.dumps(result), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "schema", "session", "checks", "serve"))
    parser.add_argument("--require-plan", action="store_true", help="Fail unless the cached plan was read")
    parser.add_argument("--cookie-file", help="File that receives or supplies the session cookie")
    parser.add_argument("--target-id", help="Database target id for the checks")
    parser.add_argument("--tns-alias", help="TNS alias of the database the checks open a session on")
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
    elif args.action == "checks":
        if not (args.cookie_file and args.target_id and args.tns_alias):
            raise ValueError("checks requires --cookie-file, --target-id, and --tns-alias")
        session_checks(settings, Path(args.cookie_file), args.target_id, args.tns_alias, args.require_plan)
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
