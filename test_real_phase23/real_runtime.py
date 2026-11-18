"""Private runtime helper for Verify_Phase23_Real.ps1. Never prints credentials."""

import argparse
import json
import logging
import os
import re
import secrets
import time
import uuid
from pathlib import Path

import httpx
import oracledb
from sentinel.config.settings import Settings
from sentinel.persistence.models import CurrentObservation, Observation, OperatorAccount
from sqlalchemy import create_engine, func, inspect, select, text
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
OPERATOR_USERNAME = "phase23-operator"
SAMPLE_METRIC = "activity.sample"
RATE_COUNTERS = ("physical reads", "redo entries", "parse count (total)", "execute count")
HISTORY_KEYS = ("graph_history_enabled", "graph_history_interval_seconds", "graph_history_retention_hours")
RUN_INTERVAL_SECONDS = 60
WAIT_SECONDS = 240


def load_settings() -> Settings:
    """Load root credentials plus Phase 23 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase23\\.env are required")
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if not settings.auth_required:
        raise ValueError("Live Phase 23 verification requires AUTH_REQUIRED=true")
    if settings.auth_oidc_issuer.strip() or settings.auth_oidc_client_id.strip():
        raise ValueError("Live Phase 23 verification leaves OIDC unset")
    if settings.ai_provider == "hosted" or settings.ai_fallback_provider != "disabled" or settings.ai_hosted_approved:
        raise ValueError("Live Phase 23 verification does not approve a hosted model or a fallback")
    if settings.operations_execution_enabled or settings.operations_execution_approved:
        raise ValueError("Live Phase 23 verification keeps privileged execution disabled")
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
    print("Application schema upgraded to Alembic head (revision 0011 or later).", flush=True)


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
    if str(version) < "0011" or missing or stored != 0:
        raise ValueError("Schema is not at revision 0011 or later with an empty privileged table")
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


def _config(client: httpx.Client) -> dict[str, object]:
    response = client.get("/api/v1/admin/config")
    _expect(response, 200, "Reading the stored settings")
    items = response.json().get("items")
    if not isinstance(items, list):
        raise TypeError("The stored settings returned an unexpected response")
    section = {
        str(item["key"]): item.get("value") for item in items
        if isinstance(item, dict) and item.get("section") == "Graph history"
    }
    if set(section) != set(HISTORY_KEYS):
        raise ValueError("Admin does not list the three Graph history settings")
    return section


def _set(client: httpx.Client, key: str, value: object) -> None:
    _expect(client.put(f"/api/v1/admin/config/{key}", json={"value": value}), 200, f"Storing {key}")


def _history(client: httpx.Client, target_id: str, minutes: int = 60) -> dict[str, object]:
    response = client.get(f"/api/v1/databases/{target_id}/graph/history", params={"minutes": minutes})
    _expect(response, 200, "Stored history")
    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("samples"), list):
        raise TypeError("Stored history returned an unexpected response")
    return body


def _named(items: object) -> dict[str, int]:
    if not isinstance(items, list):
        return {}
    return {
        str(item.get("name")): int(item["value"]) for item in items
        if isinstance(item, dict) and isinstance(item.get("value"), int)
    }


def _stored_rows(settings: Settings, target_id: str) -> tuple[int, int]:
    """Count the stored samples of one database, and any that leaked into the current-value table."""
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            stored = session.scalar(select(func.count()).select_from(Observation).where(
                Observation.target_id == uuid.UUID(target_id), Observation.metric_name == SAMPLE_METRIC,
            ))
            current = session.scalar(select(func.count()).select_from(CurrentObservation).where(
                CurrentObservation.metric_name == SAMPLE_METRIC,
            ))
    finally:
        engine.dispose()
    return int(stored or 0), int(current or 0)


def _no_sql(payload: object, what: str) -> None:
    folded = json.dumps(payload).lower()
    if "from v$" in folded or "from dba_" in folded or re.search(r"\bselect\s+[\w*,\s]+\s+from\b", folded):
        raise ValueError(f"{what} contained SQL")


def history_checks(settings: Settings, cookie_file: Path, target_id: str) -> None:
    """Confirm stored counter samples, the history route, and the settings against the running API.

    The interval is set to 60 seconds for the run and every changed setting is put back.
    No counter value, event name, or path is printed.
    """
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie or not target_id.strip():
        raise ValueError("checks requires a session cookie and a target id")
    _remove_operator(settings)
    password = secrets.token_urlsafe(18) + "a1"
    result: dict[str, object] = {}
    with _client(cookie) as admin:
        before = _config(admin)
        try:
            _set(admin, "graph_history_enabled", True)
            _set(admin, "graph_history_interval_seconds", RUN_INTERVAL_SECONDS)
            result["history_was"] = "on" if before["graph_history_enabled"] else "off"
            _expect(admin.put(
                "/api/v1/admin/config/graph_history_interval_seconds", json={"value": 5},
            ), 400, "An interval under 60 seconds")
            _expect(admin.put(
                "/api/v1/admin/config/graph_history_retention_hours", json={"value": 500},
            ), 400, "A retention over 168 hours")
            result["settings"] = "checked"

            deadline = time.monotonic() + WAIT_SECONDS
            body: dict[str, object] = {}
            while time.monotonic() < deadline:
                body = _history(admin, target_id)
                if len(body["samples"]) >= 2:
                    break
                time.sleep(5)
            samples = [item for item in body.get("samples", []) if isinstance(item, dict)]
            if len(samples) < 2:
                raise ValueError(
                    f"Fewer than two samples were stored in {WAIT_SECONDS} seconds. "
                    "Check that the scheduler is running and that GSYSTEM can read V$SYSSTAT."
                )
            if body.get("enabled") is not True or int(body.get("interval_seconds") or 0) != RUN_INTERVAL_SECONDS:
                raise ValueError("Stored history did not report that it is on with the 60 second interval")
            ages = [float(item.get("age_seconds") or 0) for item in samples]
            if ages != sorted(ages, reverse=True):
                raise ValueError("Stored samples were not returned oldest first")
            earlier, latest = samples[-2], samples[-1]
            counters = _named(latest.get("counters"))
            missing = [name for name in RATE_COUNTERS if name not in counters]
            if missing:
                raise ValueError(
                    f"{len(missing)} of the four rate counters were not stored. Check SELECT on V_$SYSSTAT for GSYSTEM."
                )
            previous = _named(earlier.get("counters"))
            if any(counters[name] < previous.get(name, 0) for name in RATE_COUNTERS):
                result["counters"] = "restarted"
            else:
                result["counters"] = "increasing"
            result["samples_stored"] = len(samples)
            result["wait_events_stored"] = len(latest.get("events") or [])
            result["io_counters_stored"] = len(_named(latest.get("io")))
            result["file_io_stored"] = "yes" if (latest.get("files") or {}).get("reads") is not None else "no"
            result["cpu_stored"] = "yes" if latest.get("cpu_micro") is not None else "no"
            _no_sql(body, "Stored history")
            result["history"] = "stored"

            live = admin.get(f"/api/v1/databases/{target_id}/graph/activity")
            _expect(live, 200, "The live activity picture")
            now = _named(live.json().get("counters"))
            gap = float(latest.get("age_seconds") or 0)
            if all(name in now for name in RATE_COUNTERS) and all(now[name] >= counters[name] for name in RATE_COUNTERS):
                result["first_rate"] = "from_stored_sample" if gap >= 10 else "too_recent"
            else:
                result["first_rate"] = "restarted"

            rows, leaked = _stored_rows(settings, target_id)
            if rows < 2 or leaked:
                raise ValueError("Samples were not stored as history rows only")
            listed = admin.get(f"/api/v1/databases/{target_id}/observations", params={"limit": 1000})
            _expect(listed, 200, "The observation list")
            if any(isinstance(item, dict) and item.get("metric_name") == SAMPLE_METRIC for item in listed.json()):
                raise ValueError("A stored sample appeared in the general observation list")
            named = admin.get(
                f"/api/v1/databases/{target_id}/observations", params={"limit": 10, "metric_name": SAMPLE_METRIC},
            )
            _expect(named, 200, "Stored samples by name")
            if not named.json():
                raise ValueError("Stored samples could not be read by name")
            result["storage"] = "history_only"

            _expect(admin.post("/api/v1/admin/operators", json={
                "username": OPERATOR_USERNAME, "password": password, "role": "operator",
            }), 201, "Creating the temporary operator")
            signed = _sign_in(OPERATOR_USERNAME, password)
            _expect(signed, 200, "Operator sign-in")
            with _client(signed.cookies.get("sentinel_session", "")) as operator:
                if len(_history(operator, target_id)["samples"]) < 2:
                    raise ValueError("An operator was not shown the stored samples")
                _expect(operator.get(
                    f"/api/v1/databases/{target_id}/graph/history", params={"minutes": 1},
                ), 422, "A window under five minutes")
                _expect(operator.get(
                    f"/api/v1/databases/{uuid.uuid4()}/graph/history",
                ), 404, "History of an unknown database")
                _expect(operator.put(
                    "/api/v1/admin/config/graph_history_interval_seconds", json={"value": 120},
                ), 403, "An operator changing the interval")
            result["access"] = "every_role_reads_admin_sets"

            _set(admin, "graph_history_enabled", False)
            off = _history(admin, target_id)
            if off.get("enabled") is not False or not str(off.get("caption") or "").startswith("Stored history is off"):
                raise ValueError("Turning the setting off was not reported by the history route")
            result["switch"] = "applied"
        finally:
            for key in HISTORY_KEYS:
                admin.put(f"/api/v1/admin/config/{key}", json={"value": before[key]})
            _remove_operator(settings)
        if _config(admin) != before:
            raise ValueError("The Graph history settings were not put back")
        result["restored"] = "yes"
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
            "graph_history_enabled": settings.graph_history_enabled,
            "graph_history_interval_seconds": settings.graph_history_interval_seconds,
            "graph_history_retention_hours": settings.graph_history_retention_hours,
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
        history_checks(settings, Path(args.cookie_file), args.target_id)
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
