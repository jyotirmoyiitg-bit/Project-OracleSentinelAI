"""Private runtime helper for Verify_Phase25_Real.ps1. Never prints credentials."""

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
OPERATOR_USERNAME = "phase25-operator"
SECTIONS = ("memory/sga", "storage/tablespaces", "parameters", "alert-log")
ADMIN_ONLY = "Shown to an administrator."
PATH_MASK = "(path shown to an administrator)"
# A real instance lists several hundred parameters.
MIN_PARAMETERS = 100


def load_settings() -> Settings:
    """Load root credentials plus Phase 25 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase25\\.env are required")
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if not settings.auth_required:
        raise ValueError("Live Phase 25 verification requires AUTH_REQUIRED=true")
    if settings.auth_oidc_issuer.strip() or settings.auth_oidc_client_id.strip():
        raise ValueError("Live Phase 25 verification leaves OIDC unset")
    if settings.ai_provider == "hosted" or settings.ai_fallback_provider != "disabled" or settings.ai_hosted_approved:
        raise ValueError("Live Phase 25 verification does not approve a hosted model or a fallback")
    if settings.operations_execution_enabled or settings.operations_execution_approved:
        raise ValueError("Live Phase 25 verification keeps privileged execution disabled")
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


def _section(client: httpx.Client, target_id: str, path: str) -> dict[str, object]:
    response = client.get(f"/api/v1/databases/{target_id}/{path}")
    _expect(response, 200, f"Reading {path}")
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError(f"{path} returned an unexpected response")
    _no_sql(body, path)
    return body


def _rows(body: dict[str, object], key: str) -> list[dict[str, object]]:
    items = body.get(key)
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _looks_like_path(value: object) -> bool:
    return isinstance(value, str) and bool(re.search(r"(?:^|[\s=(])(?:/|[A-Za-z]:\\|\+[A-Za-z0-9_]+/)\S", value))


def _no_sql(payload: object, what: str) -> None:
    folded = json.dumps(payload).lower()
    if "from v$" in folded or "from dba_" in folded or re.search(r"\bselect\s+[\w*,\s]+\s+from\b", folded):
        raise ValueError(f"{what} contained SQL")


def section_checks(settings: Settings, cookie_file: Path, target_id: str) -> None:
    """Confirm MemoryDB, StorageDB, ParameterDB, and AlertDB against the running API and the real database.

    Nothing is changed. No value, name, path, or log line is printed; only counts.
    """
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie or not target_id.strip():
        raise ValueError("checks requires a session cookie and a target id")
    _remove_operator(settings)
    password = secrets.token_urlsafe(18) + "a1"
    result: dict[str, object] = {}
    with _client(cookie) as admin:
        try:
            sga = _section(admin, target_id, "memory/sga")
            pools = _rows(sga, "pools")
            if sga.get("limitation"):
                raise ValueError("MemoryDB could not read V$SGASTAT. Check SELECT on V_$SGASTAT for the monitoring account.")
            if not pools or int(sga.get("total_bytes") or 0) <= 0 or not _rows(sga, "components"):
                raise ValueError("MemoryDB returned no SGA pool")
            if any(int(pool.get("used_bytes") or 0) + int(pool.get("free_bytes") or 0) != int(pool.get("bytes") or 0) for pool in pools):
                raise ValueError("An SGA pool's used and free memory do not add up to its size")
            if sum(int(pool.get("bytes") or 0) for pool in pools) != int(sga.get("total_bytes") or 0):
                raise ValueError("The SGA total is not the sum of its pools")
            result["sga_pools"] = len(pools)
            result["shared_pool_areas"] = len(_rows(sga, "shared_pool"))
            result["memory"] = "read"

            spaces = _section(admin, target_id, "storage/tablespaces")
            rows = _rows(spaces, "tablespaces")
            if spaces.get("limitation"):
                raise ValueError("StorageDB could not read the tablespaces. Check SELECT on DBA_TABLESPACES and DBA_FREE_SPACE.")
            if not rows:
                raise ValueError("StorageDB returned no tablespace")
            for row in rows:
                total, free, used = row.get("total_bytes"), row.get("free_bytes"), row.get("used_bytes")
                if isinstance(total, int) and isinstance(free, int) and isinstance(used, int) and (used + free != total or used < 0):
                    raise ValueError("A tablespace's used and free space do not add up to its size")
            if not any(row.get("contents") == "PERMANENT" for row in rows):
                raise ValueError("StorageDB returned no permanent tablespace")
            result["tablespaces"] = len(rows)
            result["temporary_tablespaces"] = sum(1 for row in rows if row.get("contents") == "TEMPORARY")
            result["storage"] = "read"

            params = _section(admin, target_id, "parameters")
            items = _rows(params, "parameters")
            if params.get("limitation"):
                raise ValueError("ParameterDB could not read V$PARAMETER.")
            if len(items) < MIN_PARAMETERS or int(params.get("total") or 0) != len(items):
                raise ValueError(f"ParameterDB returned fewer than {MIN_PARAMETERS} parameters")
            names = [str(item.get("name")) for item in items]
            if names != sorted(names) or "processes" not in names:
                raise ValueError("ParameterDB did not list the parameters by name")
            if int(params.get("changed") or 0) != sum(1 for item in items if item.get("is_default") is False):
                raise ValueError("The count of changed parameters is wrong")
            result["parameters"] = len(items)
            result["parameters_changed"] = int(params.get("changed") or 0)
            result["version_known"] = "yes" if params.get("version") else "no"
            result["parameter_list"] = "read"

            alert = _section(admin, target_id, "alert-log")
            lines = _rows(alert, "lines")
            if alert.get("limitation"):
                raise ValueError("AlertDB could not read the alert log. Check SELECT on V_$DIAG_ALERT_EXT.")
            times = [str(line.get("at")) for line in lines]
            if times != sorted(times) or len(lines) > 300:
                raise ValueError("AlertDB did not return at most 300 lines, oldest first")
            if int(alert.get("errors") or 0) != sum(1 for line in lines if line.get("severity") == "error"):
                raise ValueError("The count of alert errors is wrong")
            result["alert_lines"] = len(lines)
            result["alert_errors"] = int(alert.get("errors") or 0)
            result["alert_warnings"] = int(alert.get("warnings") or 0)
            result["alert_log"] = "read"

            _expect(admin.post("/api/v1/admin/operators", json={
                "username": OPERATOR_USERNAME, "password": password, "role": "operator",
            }), 201, "Creating the temporary operator")
            signed = _sign_in(OPERATOR_USERNAME, password)
            _expect(signed, 200, "Operator sign-in")
            with _client(signed.cookies.get("sentinel_session", "")) as operator:
                for path in SECTIONS:
                    _section(operator, target_id, path)
                    _expect(operator.get(f"/api/v1/databases/{uuid.uuid4()}/{path}"), 404, f"{path} of an unknown database")
                seen = _section(operator, target_id, "parameters")
                held = [item for item in _rows(seen, "parameters") if item.get("restricted")]
                if seen.get("detail") is not False or any(item.get("value") != ADMIN_ONLY for item in held):
                    raise ValueError("An operator was not shown the parameter rule")
                if any(_looks_like_path(item.get("value")) for item in _rows(seen, "parameters")):
                    raise ValueError("An operator was shown a parameter value that holds a path")
                admin_paths = sum(1 for item in items if _looks_like_path(item.get("value")))
                if admin_paths and len(held) < admin_paths:
                    raise ValueError("A path the administrator sees was not held back from the operator")
                result["parameters_held_back"] = len(held)
                log = _section(operator, target_id, "alert-log")
                operator_lines = _rows(log, "lines")
                if len(operator_lines) != len(lines):
                    raise ValueError("An operator was shown a different number of alert lines")
                if any(_looks_like_path(str(line.get("text")).replace(PATH_MASK, "")) for line in operator_lines):
                    raise ValueError("An operator was shown a file path in the alert log")
                result["alert_paths_masked"] = sum(1 for line in operator_lines if PATH_MASK in str(line.get("text")))
            result["access"] = "every_role_reads_paths_admin_only"
        finally:
            _remove_operator(settings)
    print(json.dumps(result), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "schema", "session", "checks", "serve"))
    parser.add_argument("--cookie-file", help="File that receives or supplies the session cookie")
    parser.add_argument("--target-id", help="Database target id for the checks")
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
        if not args.cookie_file or not args.cookie_file.strip() or not args.target_id or not args.target_id.strip():
            raise ValueError("checks requires --cookie-file and --target-id")
        section_checks(settings, Path(args.cookie_file), args.target_id)
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
