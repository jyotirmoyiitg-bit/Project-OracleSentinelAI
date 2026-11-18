"""Private runtime helper for Verify_Phase26_Real.ps1. Never prints credentials."""

import argparse
import json
import logging
import os
import re
import secrets
from pathlib import Path

import httpx
import oracledb
from sentinel.ai.activity import ACTIVITY_TOOLS
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
OPERATOR_USERNAME = "phase26-operator"
SIZE_QUESTION = "What is the combined size of the redo log files?"
SCHEMA_QUESTION = "Which schema in the database is not a system schema?"
LIST_QUESTION = "List the schemas in this database"
EXPLAIN_QUESTION = "What is a schema?"
USER_LIST_DENIAL = "Authorization failed. Listing database users is limited to an admin operator."
OWN_HEADER = "Application schemas, not maintained by Oracle: "
SYSTEM_HEADER = "Schemas maintained by Oracle (system schemas): "
MONITORING_NOTE = "(the Sentinel monitoring account)"
UNCITED_NOTE = "No manual passage was found for this question"
CITED_NOTE = "with the manual passages it used listed as sources"
# How much of two answers is compared to tell a copy.
COPY_LENGTH = 60


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


def _config(client: httpx.Client) -> dict[str, object]:
    response = client.get("/api/v1/admin/config")
    _expect(response, 200, "Reading the stored settings")
    items = response.json().get("items")
    if not isinstance(items, list):
        raise TypeError("The stored settings returned an unexpected response")
    return {str(item["key"]): item.get("value") for item in items if isinstance(item, dict)}


def _thread(client: httpx.Client, target_id: str) -> dict[str, object]:
    response = client.post("/api/v1/chat/threads", json={"scope_type": "database", "scope_id": target_id})
    _expect(response, 200, "Opening a database chat")
    body = response.json()
    if not isinstance(body, dict) or not body.get("id"):
        raise TypeError("Opening a database chat returned an unexpected response")
    return body


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
                _no_sql(payload.get("tools"), "A copilot answer")
                return payload
    raise ValueError("The copilot returned no message")


def _tools(message: dict[str, object]) -> dict[str, str]:
    tools = message.get("tools")
    if not isinstance(tools, list):
        return {}
    return {str(item.get("tool")): str(item.get("decision")) for item in tools if isinstance(item, dict)}


def _answer(message: dict[str, object]) -> dict[str, object]:
    answer = message.get("answer")
    return answer if isinstance(answer, dict) else {}


def _summary(message: dict[str, object]) -> str:
    """The start of an answer, folded, for comparing two answers. It is never printed."""
    text = str(_answer(message).get("summary") or message.get("content") or "")
    return " ".join(text.lower().split())[:COPY_LENGTH]


def _count(reading: list[str], header: str) -> int:
    for line in reading:
        if line.startswith(header):
            return int(line[len(header):].strip() or 0)
    return -1


def _no_sql(payload: object, what: str) -> None:
    folded = json.dumps(payload).lower()
    if "from v$" in folded or "from dba_" in folded or re.search(r"\bselect\s+[\w*,\s]+\s+from\b", folded):
        raise ValueError(f"{what} contained SQL")


def copilot_fix_checks(settings: Settings, cookie_file: Path, target_id: str, require_model: bool) -> None:
    """Confirm the Phase 26 fixes against the running API and the real database.

    The conversation that went wrong is asked again: a redo log question, then which schema is not a system
    schema. The supervisor is turned on for the checks and put back. No answer text, name, or reading is
    printed; only counts.
    """
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie or not target_id.strip():
        raise ValueError("checks requires a session cookie and a target id")
    _remove_operator(settings)
    password = secrets.token_urlsafe(18) + "a1"
    result: dict[str, object] = {}
    with _client(cookie) as admin:
        before = _config(admin)
        if "ai_supervisor_enabled" not in before:
            raise ValueError("Admin does not list ai_supervisor_enabled")
        try:
            _expect(admin.put(
                "/api/v1/admin/config/ai_supervisor_enabled", json={"value": True},
            ), 200, "Turning the supervisor on")
            thread = _thread(admin, target_id)

            sized = _ask(admin, thread, SIZE_QUESTION)
            if _tools(sized).get("get_redo_logs") != "allowed":
                raise ValueError("The redo log question read nothing from the selected database")

            asked = _ask(admin, thread, SCHEMA_QUESTION)
            answer = _answer(asked)
            if _tools(asked).get("list_database_users") != "allowed":
                raise ValueError("The schema question was not read from the selected database")
            if "This database" not in (answer.get("routes") or []):
                raise ValueError("The schema question was not answered as a database question")
            if asked.get("llm_used") and _summary(asked) == _summary(sized):
                raise ValueError("The schema question was answered with the previous answer")
            reading = [str(line) for line in answer.get("reading") or []]
            own, system = _count(reading, OWN_HEADER), _count(reading, SYSTEM_HEADER)
            if not reading or not reading[0].startswith(OWN_HEADER) or own < 0 or system < 1:
                raise ValueError("The user reading did not separate application schemas from the ones Oracle maintains")
            if settings.oracle_username.upper() == "GSYSTEM" and not any(line.endswith(MONITORING_NOTE) for line in reading):
                raise ValueError("The monitoring account was not named among the application schemas")
            routed_by = str(answer.get("routed_by") or "")
            if require_model and (routed_by != "supervisor" or not asked.get("llm_used")):
                raise ValueError(
                    "The local model did not route and answer the schema question. Turn the local model on in "
                    "Admin, check that it is running, and run again."
                )
            result["schema_question"] = "read_from_database"
            result["application_schemas"] = own
            result["oracle_maintained_schemas"] = system
            result["routed_by"] = routed_by or "none"

            listed = _ask(admin, thread, LIST_QUESTION)
            if _tools(listed).get("list_database_users") != "allowed":
                raise ValueError("A request to list the schemas was not read from the selected database")

            explained = _ask(admin, thread, EXPLAIN_QUESTION)
            detail = _answer(explained)
            if routed_by == "supervisor" and any(tool in _tools(explained) for tool in ACTIVITY_TOOLS):
                raise ValueError("An explanation was answered with a database reading")
            if explained.get("llm_used") and _summary(explained) in {_summary(sized), _summary(asked), _summary(listed)}:
                raise ValueError("An explanation was answered with an earlier answer")
            if explained.get("llm_used") and "Oracle knowledge" in (detail.get("routes") or []):
                citations = explained.get("citations")
                cited = isinstance(citations, list) and any(
                    isinstance(item, dict) and item.get("kind") == "document" for item in citations
                )
                limits = str(detail.get("limitations") or "")
                if cited and CITED_NOTE not in limits:
                    raise ValueError("An explanation with a manual passage did not say so")
                if not cited and (detail.get("confidence") != "low" or UNCITED_NOTE not in limits):
                    raise ValueError("An explanation with no manual passage was not marked as low confidence")
                result["explanation"] = "cited" if cited else "uncited_low_confidence"
            else:
                result["explanation"] = "model_off"

            _expect(admin.post("/api/v1/admin/operators", json={
                "username": OPERATOR_USERNAME, "password": password, "role": "operator",
            }), 201, "Creating the temporary operator")
            signed = _sign_in(OPERATOR_USERNAME, password)
            _expect(signed, 200, "Operator sign-in")
            with _client(signed.cookies.get("sentinel_session", "")) as operator:
                own_thread = _thread(operator, target_id)
                for question in (SCHEMA_QUESTION, LIST_QUESTION):
                    refused = _ask(operator, own_thread, question)
                    if refused.get("content") != USER_LIST_DENIAL or _tools(refused) != {"list_database_users": "denied"}:
                        raise ValueError("An operator was not refused the list of database users")
                    if _answer(refused).get("reading"):
                        raise ValueError("An operator was shown a reading of database users")
            result["operator"] = "refused"
        finally:
            admin.put("/api/v1/admin/config/ai_supervisor_enabled", json={"value": before["ai_supervisor_enabled"]})
            _remove_operator(settings)
        if _config(admin).get("ai_supervisor_enabled") != before.get("ai_supervisor_enabled"):
            raise ValueError("The supervisor setting was not put back")
        result["restored"] = "yes"
    print(json.dumps(result), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "schema", "session", "checks", "serve"))
    parser.add_argument("--require-model", action="store_true", help="Fail unless the local model routed and answered")
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
        copilot_fix_checks(settings, Path(args.cookie_file), args.target_id, args.require_model)
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
