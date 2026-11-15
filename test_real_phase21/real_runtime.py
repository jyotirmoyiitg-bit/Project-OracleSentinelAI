"""Private runtime helper for Verify_Phase21_Real.ps1. Never prints credentials."""

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
)
OPERATOR_USERNAME = "phase21-operator"
REQUIRED_AUDIT = ("operator.create", "chat.question")
FLEET_GROUPS = {"Health", "Incidents", "Programs", "Storage", "Policy", "Knowledge"}
ADMIN_ONLY_LABELS = {"Database users", "Control files", "Largest segments"}
FLEET_TOOLS = (
    "get_fleet_health", "get_fleet_incidents", "get_fleet_compliance", "get_fleet_tablespaces",
    "find_fleet_programs", "find_fleet_datafiles",
)
INCIDENT_QUESTION = "Which database in the fleet has reported an incident?"
POLICY_QUESTION = "Which database is running in violation of organizational policy?"
TABLESPACE_QUESTION = "Which databases have a tablespace above 85 percent used?"
DATAFILE_QUESTION = "Which database is having a tablespace with more than 10gb data file?"
PROGRAM_QUESTION = (
    "Which database in the fleet has an active running process with process or executable name "
    "'x_run.exe' or 'lcotp.exe' or java.exe"
)
CONNECTED_QUESTION = "Which programs are connected to each database in the fleet?"
NOMINATION_QUESTION = "Is anything from sqlplus hooked up anywhere?"


def load_settings() -> Settings:
    """Load root credentials plus Phase 21 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase21\\.env are required")
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if not settings.auth_required:
        raise ValueError("Live Phase 21 verification requires AUTH_REQUIRED=true")
    if settings.auth_oidc_issuer.strip() or settings.auth_oidc_client_id.strip():
        raise ValueError("Live Phase 21 verification leaves OIDC unset")
    if settings.ai_provider == "hosted" or settings.ai_fallback_provider != "disabled" or settings.ai_hosted_approved:
        raise ValueError("Live Phase 21 verification does not approve a hosted model or a fallback")
    if settings.operations_execution_enabled or settings.operations_execution_approved:
        raise ValueError("Live Phase 21 verification keeps privileged execution disabled")
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


def _open(client: httpx.Client, scope_type: str, scope_id: str | None, what: str) -> dict[str, object]:
    """Open a chat thread and return its payload."""
    body: dict[str, object] = {"scope_type": scope_type}
    if scope_id:
        body["scope_id"] = scope_id
    response = client.post("/api/v1/chat/threads", json=body)
    _expect(response, 200, what)
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("id"):
        raise TypeError(f"{what}: unexpected response")
    return payload


def _commands(thread: dict[str, object]) -> list[dict[str, str]]:
    items = thread.get("guided_commands")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _ask(client: httpx.Client, thread: dict[str, object], question: str) -> dict[str, object]:
    """Post one question and return the message event. The answer text is not printed."""
    response = client.post(f"/api/v1/chat/threads/{thread['id']}/messages", json={"content": question})
    if response.status_code != 200:
        raise ValueError("The copilot did not accept the question")
    event = ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: ") and event == "message":
            payload = json.loads(line[6:])
            if isinstance(payload, dict):
                folded = json.dumps(payload).lower()
                if "from v$" in folded or "from dba_" in folded:
                    raise ValueError("A copilot answer included SQL")
                return payload
    raise ValueError("The copilot returned no message")


def _tool(message: dict[str, object], name: str) -> dict[str, object] | None:
    tools = message.get("tools")
    if not isinstance(tools, list):
        return None
    return next((item for item in tools if isinstance(item, dict) and item.get("tool") == name), None)


def _fleet_tools(message: dict[str, object]) -> list[str]:
    tools = message.get("tools")
    if not isinstance(tools, list):
        return []
    return [str(item.get("tool")) for item in tools if isinstance(item, dict) and item.get("tool") in FLEET_TOOLS]


def _answer(message: dict[str, object]) -> dict[str, object]:
    answer = message.get("answer")
    return answer if isinstance(answer, dict) else {}


def _fleet_answer(message: dict[str, object], tool: str, what: str) -> dict[str, object]:
    """The question must have run the fleet tool and returned a fleet answer."""
    outcome = _tool(message, tool)
    if outcome is None or outcome.get("decision") != "allowed":
        raise ValueError(f"{what} did not run {tool}")
    if _answer(message).get("kind") != "fleet":
        raise ValueError(f"{what} did not return a fleet answer")
    if message.get("llm_used") and message.get("provider") != "local":
        raise ValueError("A fleet answer was written by a model that is not the local model")
    return outcome


def _read_live(message: dict[str, object], outcome: dict[str, object], name: str, what: str) -> int:
    """The selected database must be among the databases read. Returns how many were read."""
    reading = _answer(message).get("reading")
    lines = [str(line) for line in reading] if isinstance(reading, list) else []
    if any(line.startswith("Could not be read") and name in line for line in lines):
        raise ValueError(f"{what}: the selected database could not be read")
    found = re.search(r"of (\d+) databases read", str(outcome.get("summary") or ""))
    count = int(found.group(1)) if found else 0
    if count < 1:
        raise ValueError(f"{what}: no database was read")
    return count


def fleet_checks(settings: Settings, cookie_file: Path, target_id: str, require_model: bool) -> None:
    """Confirm the fleet copilot against the running API. No answer text, name, or path is printed."""
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie or not target_id.strip():
        raise ValueError("checks requires a session cookie and a target id")
    _remove_operator(settings)
    password = secrets.token_urlsafe(18) + "a1"
    result: dict[str, object] = {}
    try:
        with _client(cookie) as admin:
            detail = admin.get(f"/api/v1/databases/{target_id}")
            _expect(detail, 200, "Selected database")
            name = str(detail.json().get("display_name") or "")
            if not name:
                raise ValueError("The selected database has no display name")

            fleet_admin = _open(admin, "fleet", None, "Administrator fleet chat")
            database_admin = _open(admin, "database", target_id, "Administrator database chat")
            fleet_list, database_list = _commands(fleet_admin), _commands(database_admin)
            if {item.get("group") for item in fleet_list} != FLEET_GROUPS:
                raise ValueError("The fleet chat did not return the fleet question groups")
            # Knowledge searches apply to both chats. Every other fleet question is for the fleet only.
            fleet_prompts = {item.get("prompt") for item in fleet_list if item.get("group") != "Knowledge"}
            database_labels = {item.get("label") for item in database_list}
            if fleet_prompts & {item.get("prompt") for item in database_list}:
                raise ValueError("A fleet question is also offered in a database chat")
            if not ADMIN_ONLY_LABELS <= database_labels or "Connected programs" not in database_labels:
                raise ValueError("The administrator database chat is missing its questions")
            result["scope_lists"] = "separate"

            _expect(admin.post("/api/v1/admin/operators", json={
                "username": OPERATOR_USERNAME, "password": password, "role": "operator",
            }), 201, "Creating the temporary operator")
            signed = _sign_in(OPERATOR_USERNAME, password)
            _expect(signed, 200, "Operator sign-in")
            with _client(signed.cookies.get("sentinel_session", "")) as operator:
                database_operator = _open(operator, "database", target_id, "Operator database chat")
                if {item.get("label") for item in _commands(database_operator)} & ADMIN_ONLY_LABELS:
                    raise ValueError("An operator was offered an administrator-only question")
                result["admin_only_buttons"] = "hidden"

                fleet = _open(operator, "fleet", None, "Operator fleet chat")
                if {item.get("group") for item in _commands(fleet)} != FLEET_GROUPS:
                    raise ValueError("The operator fleet chat did not return the fleet question groups")

                _fleet_answer(_ask(operator, fleet, INCIDENT_QUESTION), "get_fleet_incidents", "The incident question")
                result["incidents"] = "answered"
                _fleet_answer(_ask(operator, fleet, POLICY_QUESTION), "get_fleet_compliance", "The policy question")
                result["policy"] = "answered"
                _fleet_answer(_ask(operator, fleet, TABLESPACE_QUESTION), "get_fleet_tablespaces", "The tablespace question")
                result["tablespaces"] = "answered"

                message = _ask(operator, fleet, DATAFILE_QUESTION)
                outcome = _fleet_answer(message, "find_fleet_datafiles", "The datafile question")
                if "larger than 10 GB" not in str(outcome.get("summary") or ""):
                    raise ValueError("The datafile question did not apply the size from the question")
                result["databases_read"] = _read_live(message, outcome, name, "The datafile question")
                result["datafiles"] = "read_live"

                message = _ask(operator, fleet, PROGRAM_QUESTION)
                outcome = _fleet_answer(message, "find_fleet_programs", "The program question")
                if not str(outcome.get("summary") or "").startswith("x_run.exe, lcotp.exe, java.exe: found on"):
                    raise ValueError("The program question did not keep the three names from the question")
                _read_live(message, outcome, name, "The program question")
                message = _ask(operator, fleet, CONNECTED_QUESTION)
                outcome = _fleet_answer(message, "find_fleet_programs", "The connected programs question")
                _read_live(message, outcome, name, "The connected programs question")
                result["programs"] = "read_live"

                wrote = bool(message.get("llm_used"))
                model_on = bool(fleet.get("llm_enabled")) and str(fleet.get("provider") or "disabled") != "disabled"

                explained = _ask(operator, fleet, "Explain what a checkpoint is")
                if _fleet_tools(explained):
                    raise ValueError("An explanation read the fleet")
                refused = _tool(_ask(operator, fleet, "select username from dba_users where rownum < 5"), "execute_sql")
                if refused is None or refused.get("decision") != "denied":
                    raise ValueError("A typed statement was not refused in fleet chat")
                result["no_scan"] = "held"

                nomination = "skipped"
                if model_on:
                    nominated = _ask(operator, fleet, NOMINATION_QUESTION)
                    routed = _tool(nominated, "route_question")
                    picked = _fleet_tools(nominated)
                    nomination = "nominated" if routed is not None and picked else "not_nominated"
                    wrote = wrote or bool(nominated.get("llm_used"))
                result["model"] = "wrote_answer" if wrote else ("on_but_unused" if model_on else "off")
                result["nomination"] = nomination
                if require_model and not wrote:
                    raise ValueError(
                        "No fleet answer was written by the local model. Turn the local model on in Admin, "
                        "check that it is running, and run again."
                    )

            connected = _ask(admin, database_admin, "Which programs are connected to this database?")
            sessions = _tool(connected, "get_sessions")
            if sessions is None or sessions.get("decision") != "allowed" or _fleet_tools(connected):
                raise ValueError("The database chat did not answer the connected programs question from this database")
            if _fleet_tools(_ask(admin, database_admin, INCIDENT_QUESTION)):
                raise ValueError("A database chat ran a fleet tool")
            result["database_chat"] = "answered"

            trail = admin.get("/api/v1/audit/events")
            _expect(trail, 200, "Audit trail")
            actions = {str(item.get("action")) for item in trail.json().get("items", []) if isinstance(item, dict)}
            missing = [action for action in REQUIRED_AUDIT if action not in actions]
            if missing:
                raise ValueError("The audit trail is missing: " + ", ".join(missing))
            result["audit"] = "recorded"
    finally:
        _remove_operator(settings)
    print(json.dumps(result), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "schema", "session", "checks", "serve"))
    parser.add_argument("--cookie-file", help="File that receives or supplies the session cookie")
    parser.add_argument("--target-id", help="Database target id for the checks")
    parser.add_argument("--require-model", action="store_true", help="Fail unless the local model wrote a fleet answer")
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
            "fleet_max_databases": settings.ai_fleet_max_databases,
            "fleet_concurrency": settings.ai_fleet_concurrency,
            "fleet_database_timeout_seconds": settings.ai_fleet_database_timeout_seconds,
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
        fleet_checks(settings, Path(args.cookie_file), args.target_id, args.require_model)
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
