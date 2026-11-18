"""Private runtime helper for Verify_Phase24_Real.ps1. Never prints credentials."""

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
from sqlalchemy.orm import Session, sessionmaker

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
OPERATOR_USERNAME = "phase24-operator"
EMBEDDING_MODEL = "nomic-embed-text"
# Limits the Oracle manuals need. A stored value below one of these is raised before indexing.
KNOWLEDGE_FLOORS = {
    "knowledge_max_pages": 10000,
    "knowledge_max_file_bytes": 64 * 1024 * 1024,
    "knowledge_parser_timeout_seconds": 300.0,
}
READING_TOOLS = ("list_datafiles", "get_tablespace_pressure", "get_memory_status", "get_recent_ora_errors", "get_alert_log")
# One question for each thing a database chat must answer about its own database, with the reading that proves it.
DATABASE_QUESTIONS = (
    ("files", "Where does this database keep its datafiles?", "list_datafiles"),
    ("tablespaces", "Which tablespace is closest to running out of room?", "get_tablespace_pressure"),
    ("memory", "How is the memory divided between the SGA and the PGA here?", "get_memory_status"),
    ("errors", "Has this database logged any ORA errors in the last day?", "get_recent_ora_errors"),
    ("alert_log", "Show me what the alert log has been saying", "get_alert_log"),
)
EXPLAIN_QUESTION = "Can you help me understand what a tablespace is for?"
SEARCH_QUESTION = "why does a long query fail when the undo it needs has been overwritten"


def load_settings() -> Settings:
    """Load root credentials plus Phase 24 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase24\\.env are required")
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if not settings.auth_required:
        raise ValueError("Live Phase 24 verification requires AUTH_REQUIRED=true")
    if settings.auth_oidc_issuer.strip() or settings.auth_oidc_client_id.strip():
        raise ValueError("Live Phase 24 verification leaves OIDC unset")
    if settings.ai_provider == "hosted" or settings.ai_fallback_provider != "disabled" or settings.ai_hosted_approved:
        raise ValueError("Live Phase 24 verification does not approve a hosted model or a fallback")
    if settings.operations_execution_enabled or settings.operations_execution_approved:
        raise ValueError("Live Phase 24 verification keeps privileged execution disabled")
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


def index_knowledge(settings: Settings) -> None:
    """Embed the knowledge folders once with the local embedding model.

    A file is skipped when its content and the embedding model are unchanged. A file whose model changed is
    embedded again from its stored passages without being parsed. Each file is committed on its own, so a
    stopped run continues with the next unfinished file. File names and counts are printed; text is not.
    """
    from sentinel.config.store import sync_public_config, update_public_config
    from sentinel.knowledge.service import KnowledgeIndexer

    engine = create_engine(settings.database_url)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    changed: list[str] = []
    try:
        with sessions.begin() as session:
            sync_public_config(session, settings)
            for key, floor in KNOWLEDGE_FLOORS.items():
                if getattr(settings, key) < floor:
                    update_public_config(session, settings, key, floor)
                    changed.append(key)
            if settings.embedding_ollama_model != EMBEDDING_MODEL:
                update_public_config(session, settings, "embedding_ollama_model", EMBEDDING_MODEL)
                changed.append("embedding_ollama_model")
            if settings.embedding_provider != "ollama":
                update_public_config(session, settings, "embedding_provider", "ollama")
                changed.append("embedding_provider")
        try:
            tags = httpx.get(settings.embedding_local_url.rstrip("/") + "/api/tags", timeout=10.0, trust_env=False)
            tags.raise_for_status()
            installed = {str(item.get("name", "")).split(":", 1)[0] for item in tags.json().get("models", [])}
        except (httpx.HTTPError, ValueError):
            raise ValueError("Ollama is not answering on this computer. Start Ollama and run again.") from None
        if EMBEDDING_MODEL not in installed:
            raise ValueError(f"The embedding model is not installed. Run: ollama pull {EMBEDDING_MODEL}")

        def show(relative: str, outcome: str, passages: int, seconds: float) -> None:
            detail = f"{passages} passages, {seconds:.0f} s" if passages else "skipped"
            print(f"  {relative:<74} {outcome:<12} {detail}", flush=True)

        started = time.monotonic()
        report = KnowledgeIndexer(sessions, settings).scan(progress=show)
        for error in report.errors:
            print(f"  not indexed: {error[:200]}", flush=True)
    finally:
        engine.dispose()
    print(json.dumps({
        "settings_stored": changed,
        "indexed": report.indexed,
        "updated": report.updated,
        "reembedded": report.reembedded,
        "unchanged": report.unchanged,
        "removed": report.deleted,
        "quarantined": report.quarantined,
        "failed": len(report.errors),
        "seconds": round(time.monotonic() - started),
    }), flush=True)


def _config(client: httpx.Client) -> dict[str, object]:
    response = client.get("/api/v1/admin/config")
    _expect(response, 200, "Reading the stored settings")
    items = response.json().get("items")
    if not isinstance(items, list):
        raise TypeError("The stored settings returned an unexpected response")
    return {str(item["key"]): item.get("value") for item in items if isinstance(item, dict)}


def _set(client: httpx.Client, key: str, value: object) -> None:
    _expect(client.put(f"/api/v1/admin/config/{key}", json={"value": value}), 200, f"Storing {key}")


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


def _no_sql(payload: object, what: str) -> None:
    folded = json.dumps(payload).lower()
    if "from v$" in folded or "from dba_" in folded or re.search(r"\bselect\s+[\w*,\s]+\s+from\b", folded):
        raise ValueError(f"{what} contained SQL")


def copilot_checks(settings: Settings, cookie_file: Path, target_id: str, require_model: bool) -> None:
    """Confirm the index, the search, the supervisor, and stored-snapshot answers against the running API.

    Agents are turned on for the snapshot check when they are off, and every changed setting is put back.
    No answer text, reading, or path is printed.
    """
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie or not target_id.strip():
        raise ValueError("checks requires a session cookie and a target id")
    _remove_operator(settings)
    password = secrets.token_urlsafe(18) + "a1"
    result: dict[str, object] = {}
    with _client(cookie) as admin:
        before = _config(admin)
        if before.get("embedding_provider") != "ollama" or before.get("embedding_dimensions") != 768:
            raise ValueError("The local embedding model is not the stored provider. Run the indexing step first.")
        for key in ("ai_supervisor_enabled", "ai_snapshot_max_age_seconds", "embedding_ollama_model"):
            if key not in before:
                raise ValueError(f"Admin does not list {key}")

        listed = admin.get("/api/v1/knowledge/documents", params={"page_size": 100})
        _expect(listed, 200, "The knowledge document list")
        documents = [item for item in listed.json().get("items", []) if isinstance(item, dict) and item.get("ingestion_status") == "indexed"]
        current = [item for item in documents if item.get("embedding_provider") == "ollama"]
        if not documents or len(current) != len(documents):
            raise ValueError("Not every indexed document was embedded with the local model. Run the indexing step again.")
        result["documents_indexed"] = len(documents)
        result["manuals_indexed"] = sum(1 for item in documents if item.get("category") == "Knowledge")

        again = admin.post("/api/v1/knowledge/reindex", json={})
        _expect(again, 200, "A second indexing run")
        second = again.json()
        if any(int(second.get(key) or 0) for key in ("indexed", "updated", "reembedded")):
            raise ValueError("A second indexing run embedded a file that had not changed")
        result["second_run"] = "nothing_to_do"
        result["second_run_unchanged"] = int(second.get("unchanged") or 0)

        found = admin.post("/api/v1/knowledge/search", json={"query": SEARCH_QUESTION, "limit": 5, "category": "Knowledge"})
        _expect(found, 200, "A search by meaning")
        passages = found.json().get("items") or []
        if result["manuals_indexed"] and not passages:
            raise ValueError("A search by meaning found no passage in the manuals")
        result["search"] = "found" if passages else "no_manuals"

        status = admin.get("/api/v1/agents/status")
        _expect(status, 200, "Agent status")
        model_on = bool(status.json().get("model_writes_findings"))
        agents_were = bool(status.json().get("enabled"))
        overrides = admin.get(f"/api/v1/databases/{target_id}/agent-settings")
        _expect(overrides, 200, "Database agent settings")
        gap_before = next(
            (item.get("database_value") for item in overrides.json().get("items", [])
             if isinstance(item, dict) and item.get("key") == "agent_snapshot_min_gap_seconds" and item.get("overridden")),
            None,
        )
        try:
            _set(admin, "ai_supervisor_enabled", True)
            thread = _thread(admin, target_id)

            refused = _ask(admin, thread, "select * from v$session")
            if _tools(refused) != {"execute_sql": "denied"}:
                raise ValueError("A pasted statement was not refused")
            about = _ask(admin, thread, "Explain what an object grant is")
            if str(about.get("content") or "").startswith("SQL cannot be entered"):
                raise ValueError("A question about a command was treated as a pasted statement")
            result["pasted_sql"] = "refused"

            explained = _ask(admin, thread, EXPLAIN_QUESTION)
            routed_by = str(_answer(explained).get("routed_by") or "")
            routes = _answer(explained).get("routes") or []
            if routed_by == "supervisor":
                if "Oracle knowledge" not in routes or any(tool in _tools(explained) for tool in READING_TOOLS):
                    raise ValueError("An explanation was answered with a database reading")
                result["explanation"] = "oracle_knowledge"
            else:
                result["explanation"] = "fixed_patterns"
            result["routed_by"] = routed_by or "none"
            if require_model and routed_by != "supervisor":
                raise ValueError(
                    "The supervisor did not route the question. Turn the local model on in Admin, "
                    "check that it is running, and run again."
                )

            answered = 0
            for name, question, tool in DATABASE_QUESTIONS:
                message = _ask(admin, thread, question)
                tools = _tools(message)
                if tools.get(tool) == "allowed":
                    answered += 1
                elif routed_by == "supervisor" and not any(tools.get(item) == "allowed" for item in READING_TOOLS):
                    raise ValueError(f"The question about {name} read nothing from the selected database")
                if "This database" not in (_answer(message).get("routes") or []):
                    raise ValueError(f"The question about {name} was not answered as a database question")
            result["database_topics_answered"] = answered
            result["database_topics_asked"] = len(DATABASE_QUESTIONS)

            if not agents_were:
                _set(admin, "agents_enabled", True)
            _expect(admin.put(
                f"/api/v1/databases/{target_id}/agent-settings", json={"key": "agent_snapshot_min_gap_seconds", "value": 0},
            ), 200, "Allowing a snapshot now")
            collected = admin.post(f"/api/v1/databases/{target_id}/snapshots", json={"packs": ["core"]})
            _expect(collected, 200, "Collecting a snapshot")
            stored = _ask(admin, thread, "How much memory is the SGA using on this database?")
            note = str(_answer(stored).get("source_note") or "")
            live = _ask(admin, thread, "How much memory is the SGA using on this database right now?")
            if _tools(stored).get("get_memory_status") == "allowed" and not note.startswith("From the snapshot stored"):
                raise ValueError("A memory question did not use the snapshot that had just been stored")
            if str(_answer(live).get("source_note") or ""):
                raise ValueError("A question that asked for the value now was answered from a snapshot")
            result["stored_snapshot"] = "used" if note else "not_asked"

            _expect(admin.post("/api/v1/admin/operators", json={
                "username": OPERATOR_USERNAME, "password": password, "role": "operator",
            }), 201, "Creating the temporary operator")
            signed = _sign_in(OPERATOR_USERNAME, password)
            _expect(signed, 200, "Operator sign-in")
            with _client(signed.cookies.get("sentinel_session", "")) as operator:
                _expect(operator.put(
                    "/api/v1/admin/config/embedding_provider", json={"value": "local"},
                ), 403, "An operator changing the embedding provider")
                _expect(operator.post("/api/v1/knowledge/reindex", json={}), 403, "An operator starting an index run")
            _expect(admin.put(
                "/api/v1/admin/config/embedding_local_url", json={"value": "http://10.0.0.5:11434"},
            ), 400, "Pointing the embedding model at another computer")
            result["access"] = "admin_only"
        finally:
            admin.put(
                f"/api/v1/databases/{target_id}/agent-settings",
                json={"key": "agent_snapshot_min_gap_seconds", "value": gap_before},
            )
            for key in ("ai_supervisor_enabled", "agents_enabled"):
                admin.put(f"/api/v1/admin/config/{key}", json={"value": before[key]})
            _remove_operator(settings)
        after = _config(admin)
        if any(after.get(key) != before.get(key) for key in ("ai_supervisor_enabled", "agents_enabled", "embedding_provider")):
            raise ValueError("The settings were not put back")
        result["model"] = "on" if model_on else "off"
        result["restored"] = "yes"
    print(json.dumps(result), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "schema", "index", "session", "checks", "serve"))
    parser.add_argument("--require-model", action="store_true", help="Fail unless the supervisor routed the question")
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
            "embedding_local_url": settings.embedding_local_url,
            "embedding_model": EMBEDDING_MODEL,
            "knowledge_intake_path": settings.knowledge_intake_path,
        }), flush=True)
    elif args.action == "migrate":
        migrate(settings)
    elif args.action == "schema":
        schema_status(settings)
    elif args.action == "index":
        index_knowledge(settings)
    elif args.action == "session":
        if not args.cookie_file or not args.cookie_file.strip():
            raise ValueError("session requires --cookie-file")
        write_session(settings, Path(args.cookie_file))
    elif args.action == "checks":
        if not args.cookie_file or not args.cookie_file.strip() or not args.target_id or not args.target_id.strip():
            raise ValueError("checks requires --cookie-file and --target-id")
        copilot_checks(settings, Path(args.cookie_file), args.target_id, args.require_model)
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
