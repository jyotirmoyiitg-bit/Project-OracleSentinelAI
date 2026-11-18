"""Private runtime helper for Verify_Phase22_Real.ps1. Never prints credentials."""

import argparse
import json
import logging
import os
import re
import secrets
import time
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
OPERATOR_USERNAME = "phase22-operator"
REQUIRED_AUDIT = ("snapshot.collect", "agent.run", "agent.finding.dismiss", "agent.settings")
RUN_STEPS = ("plan", "collect", "compare", "lookup", "verify", "finding")
GOAL = "ORA-00257: archiver error. Check the archive destination and the recovery area."


def load_settings() -> Settings:
    """Load root credentials plus Phase 22 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase22\\.env are required")
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if not settings.auth_required:
        raise ValueError("Live Phase 22 verification requires AUTH_REQUIRED=true")
    if settings.auth_oidc_issuer.strip() or settings.auth_oidc_client_id.strip():
        raise ValueError("Live Phase 22 verification leaves OIDC unset")
    if settings.ai_provider == "hosted" or settings.ai_fallback_provider != "disabled" or settings.ai_hosted_approved:
        raise ValueError("Live Phase 22 verification does not approve a hosted model or a fallback")
    if settings.operations_execution_enabled or settings.operations_execution_approved:
        raise ValueError("Live Phase 22 verification keeps privileged execution disabled")
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


def _setting(client: httpx.Client, target_id: str, key: str, value: object) -> None:
    response = client.put(f"/api/v1/databases/{target_id}/agent-settings", json={"key": key, "value": value})
    _expect(response, 200, f"Setting {key} for the database")


def _overrides(client: httpx.Client, target_id: str) -> dict[str, object]:
    response = client.get(f"/api/v1/databases/{target_id}/agent-settings")
    _expect(response, 200, "Database agent settings")
    items = response.json().get("items")
    if not isinstance(items, list):
        raise TypeError("Database agent settings returned an unexpected response")
    return {
        str(item["key"]): item.get("database_value") for item in items
        if isinstance(item, dict) and item.get("overridden")
    }


def _collect(client: httpx.Client, target_id: str, what: str) -> dict[str, object]:
    response = client.post(f"/api/v1/databases/{target_id}/snapshots", json={"packs": ["core"]})
    _expect(response, 200, what)
    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("readings"), list):
        raise TypeError(f"{what}: unexpected response")
    return body


def _no_sql(payload: object, what: str) -> None:
    folded = json.dumps(payload).lower()
    if "from v$" in folded or "from dba_" in folded or re.search(r"\bselect\s+[\w*,\s]+\s+from\b", folded):
        raise ValueError(f"{what} contained SQL")


def agent_checks(settings: Settings, cookie_file: Path, target_id: str, require_model: bool) -> None:
    """Confirm snapshots, the Investigator, findings, and database settings against the running API.

    Agents are turned on for the run when they are off, and every changed setting is put back.
    No reading, name, or path is printed.
    """
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie or not target_id.strip():
        raise ValueError("checks requires a session cookie and a target id")
    _remove_operator(settings)
    password = secrets.token_urlsafe(18) + "a1"
    result: dict[str, object] = {}
    with _client(cookie) as admin:
        status = admin.get("/api/v1/agents/status")
        _expect(status, 200, "Agent status")
        was_enabled = bool(status.json().get("enabled"))
        packs = [str(item.get("name")) for item in status.json().get("packs", []) if isinstance(item, dict)]
        if packs != ["core", "performance", "configuration"]:
            raise ValueError("The agent status did not list the three snapshot packs")
        before = _overrides(admin, target_id)
        try:
            if not was_enabled:
                _expect(admin.put("/api/v1/admin/config/agents_enabled", json={"value": True}), 200, "Turning agents on")
            result["agents_were"] = "on" if was_enabled else "off"
            _setting(admin, target_id, "agents_enabled", True)
            _setting(admin, target_id, "agent_snapshot_min_gap_seconds", 0)

            _expect(admin.post("/api/v1/admin/operators", json={
                "username": OPERATOR_USERNAME, "password": password, "role": "operator",
            }), 201, "Creating the temporary operator")
            signed = _sign_in(OPERATOR_USERNAME, password)
            _expect(signed, 200, "Operator sign-in")
            with _client(signed.cookies.get("sentinel_session", "")) as operator:
                first = _collect(operator, target_id, "The first snapshot")
                answered = int(first.get("answered") or 0)
                if answered < 1:
                    raise ValueError("The snapshot stored no reading from the selected database")
                result["readings_answered"] = answered
                result["readings_asked"] = len(first["readings"])
                _collect(operator, target_id, "The second snapshot")
                result["snapshot"] = "stored"

                seen = operator.get(f"/api/v1/databases/{target_id}/snapshots")
                _expect(seen, 200, "Stored snapshots")
                body = seen.json()
                readings = [item for item in body.get("readings", []) if isinstance(item, dict)]
                if int(body.get("snapshot_count") or 0) < 2 or not body.get("last_taken_at"):
                    raise ValueError("The two snapshots were not both stored")
                if not any(item.get("previous_at") for item in readings):
                    raise ValueError("No reading was compared with its previous snapshot")
                result["comparison"] = "made"
                restricted = [item for item in readings if item.get("restricted")]
                if any(not item.get("hidden") or item.get("changes") for item in restricted):
                    raise ValueError("An operator was shown a restricted reading")
                as_admin = admin.get(f"/api/v1/databases/{target_id}/snapshots").json()
                if any(item.get("hidden") for item in as_admin.get("readings", []) if isinstance(item, dict)):
                    raise ValueError("An administrator was refused a stored reading")
                _expect(operator.post(
                    f"/api/v1/databases/{target_id}/snapshots", json={"packs": ["configuration"]},
                ), 403, "An operator asking for the configuration pack")
                result["restricted_readings"] = "admin_only"

                _expect(operator.put(
                    f"/api/v1/databases/{target_id}/agent-settings", json={"key": "agent_snapshot_retention_days", "value": 7},
                ), 403, "An operator changing a database setting")
                _expect(admin.put(
                    f"/api/v1/databases/{target_id}/agent-settings", json={"key": "agent_snapshot_retention_days", "value": 90},
                ), 422, "A retention outside 1 to 30 days")
                _setting(admin, target_id, "agent_snapshot_min_gap_seconds", 600)
                _expect(operator.post(
                    f"/api/v1/databases/{target_id}/snapshots", json={"packs": ["core"]},
                ), 429, "A collection inside the minimum gap")
                _setting(admin, target_id, "agents_enabled", False)
                _expect(operator.post(
                    "/api/v1/agents/investigations", json={"target_id": target_id, "goal": GOAL},
                ), 409, "An investigation of a database that opted out")
                _setting(admin, target_id, "agents_enabled", True)
                result["database_settings"] = "applied"

                started = operator.post("/api/v1/agents/investigations", json={"target_id": target_id, "goal": GOAL})
                _expect(started, 202, "Starting the investigation")
                run_id = str(started.json()["id"])
                deadline = time.monotonic() + settings.agent_run_timeout_seconds + 60
                run: dict[str, object] = {}
                while time.monotonic() < deadline:
                    response = operator.get(f"/api/v1/agents/runs/{run_id}")
                    _expect(response, 200, "Reading the run")
                    run = response.json()
                    if run.get("status") not in {"queued", "running"}:
                        break
                    time.sleep(1.5)
                if run.get("status") != "completed":
                    raise ValueError(f"The investigation ended as {run.get('status')}")
                if run.get("playbook") != "archive_destination":
                    raise ValueError("The investigation did not choose the archive destination playbook")
                kinds = [str(step.get("kind")) for step in run.get("steps", []) if isinstance(step, dict)]
                if [kind for kind in kinds if kind in RUN_STEPS and kind != "collect"] != [
                    kind for kind in RUN_STEPS if kind != "collect"
                ] or "collect" not in kinds:
                    raise ValueError("The investigation did not save every step")
                collect = next(step for step in run["steps"] if isinstance(step, dict) and step.get("kind") == "collect")
                if collect.get("outcome") != "ok":
                    raise ValueError("The investigation read nothing from the selected database")
                _no_sql(run, "The investigation")
                result["investigation"] = "completed"

                finding = run.get("finding")
                if not isinstance(finding, dict) or finding.get("decision") != "open" or not finding.get("evidence"):
                    raise ValueError("The investigation saved no finding with evidence")
                written_by = str(finding.get("written_by"))
                if written_by == "model" and not finding.get("verified"):
                    raise ValueError("A model-written finding was shown without passing the verifier")
                if finding.get("restricted"):
                    raise ValueError("An operator's finding used restricted evidence")
                if run.get("llm_used") and status.json().get("model_writes_findings") is not True:
                    raise ValueError("A model that is not the local model wrote a finding")
                result["finding"] = "model" if written_by == "model" else (
                    "rejected" if not finding.get("verified") else "evidence_only"
                )
                if require_model and written_by != "model":
                    raise ValueError(
                        "The local model did not write the finding. Turn the local model on in Admin, "
                        "check that it is running, and run again."
                    )
                decided = operator.post(
                    f"/api/v1/agents/findings/{finding['id']}/decision",
                    json={"decision": "dismissed", "note": "Live check for Phase 22."},
                )
                _expect(decided, 200, "Dismissing the finding")
                if decided.json().get("decision") != "dismissed":
                    raise ValueError("The decision was not stored")
                result["decision"] = "recorded"

            trail = admin.get("/api/v1/audit/events")
            _expect(trail, 200, "Audit trail")
            actions = {str(item.get("action")) for item in trail.json().get("items", []) if isinstance(item, dict)}
            missing = [action for action in REQUIRED_AUDIT if action not in actions]
            if missing:
                raise ValueError("The audit trail is missing: " + ", ".join(missing))
            result["audit"] = "recorded"
        finally:
            for key in ("agents_enabled", "agent_snapshot_min_gap_seconds", "agent_snapshot_retention_days"):
                admin.put(f"/api/v1/databases/{target_id}/agent-settings", json={"key": key, "value": before.get(key)})
            if not was_enabled:
                admin.put("/api/v1/admin/config/agents_enabled", json={"value": False})
            _remove_operator(settings)
        if _overrides(admin, target_id) != before:
            raise ValueError("The database's agent settings were not put back")
        if bool(admin.get("/api/v1/agents/status").json().get("enabled")) != was_enabled:
            raise ValueError("The system agent switch was not put back")
        result["restored"] = "yes"
    print(json.dumps(result), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "schema", "session", "checks", "serve"))
    parser.add_argument("--cookie-file", help="File that receives or supplies the session cookie")
    parser.add_argument("--target-id", help="Database target id for the checks")
    parser.add_argument("--require-model", action="store_true", help="Fail unless the local model wrote the finding")
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
            "agent_run_timeout_seconds": settings.agent_run_timeout_seconds,
            "agent_max_steps": settings.agent_max_steps,
            "agent_max_model_calls": settings.agent_max_model_calls,
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
        agent_checks(settings, Path(args.cookie_file), args.target_id, args.require_model)
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
