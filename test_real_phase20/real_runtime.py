"""Private runtime helper for Verify_Phase20_Real.ps1. Never prints credentials."""

import argparse
import json
import logging
import os
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
)
OPERATOR_USERNAME = "phase20-operator"
REQUIRED_AUDIT = ("auth.login", "operator.create", "operator.role", "operator.enabled", "chat.question")


def load_settings() -> Settings:
    """Load root credentials plus Phase 20 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase20\\.env are required")
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if not settings.auth_required:
        raise ValueError("Live Phase 20 verification requires AUTH_REQUIRED=true")
    if settings.auth_oidc_issuer.strip() or settings.auth_oidc_client_id.strip():
        raise ValueError("Live Phase 20 verification leaves OIDC unset")
    if settings.ai_enabled or settings.ai_provider != "disabled":
        raise ValueError("Live Phase 20 verification requires AI_ENABLED=false and AI_PROVIDER=disabled")
    if settings.ai_fallback_provider != "disabled" or settings.ai_hosted_approved:
        raise ValueError("Live Phase 20 verification does not approve a hosted model or a fallback")
    if settings.operations_execution_enabled or settings.operations_execution_approved:
        raise ValueError("Live Phase 20 verification keeps privileged execution disabled")
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
    print("Application schema upgraded to Alembic head (revision 0010 or later).", flush=True)


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
    if str(version) < "0010" or missing or stored != 0:
        raise ValueError("Schema is not at revision 0010 or later with an empty privileged table")
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
    return httpx.Client(base_url=BASE, cookies={"sentinel_session": cookie}, timeout=240.0, trust_env=False)


def _sign_in(username: str, password: str) -> httpx.Response:
    return httpx.post(
        f"{BASE}/api/v1/auth/login",
        json={"username": username, "password": password, "local": True},
        timeout=30.0, trust_env=False, follow_redirects=False,
    )


def _ask(client: httpx.Client, thread_id: str, question: str) -> dict[str, object]:
    """Post one question and return the message event. The answer text is not printed."""
    response = client.post(f"/api/v1/chat/threads/{thread_id}/messages", json={"content": question})
    if response.status_code != 200:
        raise ValueError("The copilot did not accept the question")
    event = ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: ") and event == "message":
            payload = json.loads(line[6:])
            if isinstance(payload, dict):
                if "from v$" in json.dumps(payload).lower():
                    raise ValueError("A copilot answer included SQL")
                return payload
    raise ValueError("The copilot returned no message")


def _tool(message: dict[str, object], name: str) -> dict[str, object] | None:
    tools = message.get("tools")
    if not isinstance(tools, list):
        return None
    return next((item for item in tools if isinstance(item, dict) and item.get("tool") == name), None)


def _expect(response: httpx.Response, status: int, what: str) -> None:
    if response.status_code != status:
        raise ValueError(f"{what}: expected HTTP {status}, received {response.status_code}")


def security_checks(settings: Settings, cookie_file: Path, target_id: str) -> None:
    """Confirm permissions, revocation, audit, and the new copilot routes. No name or path is printed."""
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie or not target_id.strip():
        raise ValueError("checks requires a session cookie and a target id")
    _remove_operator(settings)
    password = secrets.token_urlsafe(18) + "a1"
    result: dict[str, object] = {}
    try:
        with _client(cookie) as admin:
            listing = admin.get("/api/v1/databases")
            _expect(listing, 200, "Administrator fleet list")
            headers = listing.headers
            if (
                headers.get("x-content-type-options") != "nosniff" or headers.get("x-frame-options") != "DENY"
                or headers.get("cache-control") != "no-store" or "content-security-policy" not in headers
            ):
                raise ValueError("The API response did not carry the security headers")
            result["headers"] = "present"
            current = admin.get("/api/v1/auth/session").json()
            if not current.get("authenticated") or "directory_enabled" not in current:
                raise ValueError("The session response did not describe the sign-in")
            _expect(_sign_in("phase20-nobody", "wrong-password-1"), 401, "A wrong password")
            result["wrong_password"] = "denied"

            _expect(admin.post("/api/v1/admin/operators", json={
                "username": OPERATOR_USERNAME, "password": "short", "role": "operator",
            }), 422, "A weak operator password")
            result["weak_password"] = "refused"
            _expect(admin.post("/api/v1/admin/operators", json={
                "username": OPERATOR_USERNAME, "password": password, "role": "operator",
            }), 201, "Creating the temporary operator")
            result["operator_created"] = "created"

            signed = _sign_in(OPERATOR_USERNAME, password)
            _expect(signed, 200, "Operator sign-in")
            with _client(signed.cookies.get("sentinel_session", "")) as operator:
                _expect(operator.get("/api/v1/databases"), 200, "Operator fleet list")
                _expect(operator.put(f"/api/v1/databases/{target_id}", json={"owner": "phase20"}), 403, "Operator database change")
                _expect(operator.post("/api/v1/databases/test-tns-connection", json={
                    "tns_alias": "PHASE20", "display_name": "PHASE20",
                }), 403, "Operator connection test")
                _expect(operator.post(f"/api/v1/databases/{target_id}/test-connection"), 403, "Operator target connection test")
                _expect(operator.get("/api/v1/admin/operators"), 403, "Operator account list")
                _expect(operator.post("/api/v1/knowledge/reindex", json={}), 403, "Operator reindex")
                result["operator_registry"] = "denied"

                opened = admin.post("/api/v1/chat/threads", json={"scope_type": "database", "scope_id": target_id})
                _expect(opened, 200, "Administrator chat thread")
                admin_thread = str(opened.json()["id"])
                _expect(operator.get(f"/api/v1/chat/threads/{admin_thread}/messages"), 404, "Another operator's chat thread")
                _expect(operator.post(
                    f"/api/v1/chat/threads/{admin_thread}/messages", json={"content": "Summarize open incidents"},
                ), 404, "Posting into another operator's chat thread")
                result["chat_thread"] = "private"

                redo = _tool(_ask(admin, admin_thread, "Where are the redo log files?"), "get_redo_logs")
                if redo is None or redo.get("decision") != "allowed":
                    raise ValueError("The redo log question did not run the redo log reading")
                result["redo_reading"] = "answered"
                processes = _tool(_ask(admin, admin_thread, "List the background processes"), "get_background_processes")
                if processes is None or processes.get("decision") != "allowed":
                    raise ValueError("The background process question did not run its reading")
                result["background_reading"] = "answered"

                own = operator.post("/api/v1/chat/threads", json={"scope_type": "database", "scope_id": target_id})
                _expect(own, 200, "Operator chat thread")
                control = _tool(_ask(operator, str(own.json()["id"]), "List the control files"), "list_control_files")
                if control is None or control.get("decision") != "denied":
                    raise ValueError("An operator was not refused the control file listing")
                result["control_files"] = "admin_only"
                fleet_thread = operator.post("/api/v1/chat/threads", json={"scope_type": "fleet"})
                _expect(fleet_thread, 200, "Operator fleet chat thread")
                fleet = _tool(_ask(operator, str(fleet_thread.json()["id"]), "Which databases are down?"), "get_fleet_health")
                if fleet is None or fleet.get("decision") != "allowed":
                    raise ValueError("The fleet question did not use the stored fleet status")
                result["fleet_health"] = "answered"

                _expect(admin.post(
                    f"/api/v1/admin/operators/{OPERATOR_USERNAME}/role", json={"role": "viewer"},
                ), 200, "Changing the operator role")
                _expect(operator.get("/api/v1/databases"), 401, "The old session after a role change")
                result["role_change"] = "signed_out"

            again = _sign_in(OPERATOR_USERNAME, password)
            _expect(again, 200, "Sign-in after the role change")
            if again.json().get("role") != "viewer":
                raise ValueError("The new session did not carry the new role")
            with _client(again.cookies.get("sentinel_session", "")) as viewer:
                _expect(viewer.get("/api/v1/databases"), 200, "Viewer fleet list")
                _expect(admin.post(
                    f"/api/v1/admin/operators/{OPERATOR_USERNAME}/enabled", json={"enabled": False},
                ), 200, "Disabling the operator")
                _expect(viewer.get("/api/v1/databases"), 401, "The session of a disabled account")
            _expect(_sign_in(OPERATOR_USERNAME, password), 401, "Sign-in to a disabled account")
            result["disabled"] = "signed_out"

            trail = admin.get("/api/v1/audit/events")
            _expect(trail, 200, "Audit trail")
            actions = {str(item.get("action")) for item in trail.json().get("items", []) if isinstance(item, dict)}
            missing = [name for name in REQUIRED_AUDIT if name not in actions]
            if missing:
                raise ValueError("The audit trail is missing: " + ", ".join(missing))
            result["audit"] = "recorded"
            result["audit_actions"] = len(REQUIRED_AUDIT)
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
    elif args.action == "checks":
        if not args.cookie_file or not args.cookie_file.strip() or not args.target_id or not args.target_id.strip():
            raise ValueError("checks requires --cookie-file and --target-id")
        security_checks(settings, Path(args.cookie_file), args.target_id)
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
