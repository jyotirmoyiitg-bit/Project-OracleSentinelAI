"""Private runtime helper for Verify_Phase9_Real.ps1. Never prints credentials."""

import argparse
import json
import logging
import os
from pathlib import Path

import httpx
import oracledb
from sentinel.config.settings import Settings
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)
PHASE8_TABLES = (
    "operation_proposals",
    "database_entitlements",
    "audit_events",
    "operator_accounts",
    "privileged_operations",
    "app_config",
)


def load_settings() -> Settings:
    """Load root credentials plus Phase 8 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase9\\.env are required")
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if not settings.auth_required:
        raise ValueError("Live Phase 9 verification requires AUTH_REQUIRED=true")
    if settings.auth_oidc_issuer.strip() or settings.auth_oidc_client_id.strip():
        raise ValueError("Live Phase 9 verification leaves OIDC unset")
    if settings.ai_enabled or settings.ai_provider != "disabled":
        raise ValueError("Live Phase 9 verification requires AI_ENABLED=false and AI_PROVIDER=disabled")
    if settings.ai_fallback_provider != "disabled" or settings.ai_hosted_approved:
        raise ValueError("Live Phase 9 verification does not approve a hosted model or a fallback")
    if settings.operations_execution_enabled or settings.operations_execution_approved:
        raise ValueError("Live Phase 9 verification keeps privileged execution disabled")
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
    print("Application schema upgraded to Alembic head (through Phase 9, revision 0010).", flush=True)


def schema_status(settings: Settings) -> None:
    """Report the revision and Phase 7 tables. No row contents."""
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
    missing = [name for name in PHASE8_TABLES if name not in tables]
    if str(version) != "0010" or missing or stored != 0:
        raise ValueError("Phase 9 schema is not at revision 0010 with an empty privileged table")
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
    print(json.dumps({
        "principal": str(body.get("principal") or ""),
        "role": str(body.get("role") or ""),
        "method": str(body.get("method") or ""),
    }), flush=True)


def _message(client: httpx.Client, thread_id: str, content: str) -> dict[str, object]:
    response = client.post(f"/api/v1/chat/threads/{thread_id}/messages", json={"content": content})
    if response.status_code != 200:
        raise ValueError("Copilot message was not accepted")
    message: dict[str, object] | None = None
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = json.loads(line.split(":", 1)[1])
        if isinstance(payload, dict) and "content" in payload and "tools" in payload:
            message = payload
    if message is None:
        raise ValueError("Copilot message did not return an answer")
    return message


def _allowed(message: dict[str, object], tool_name: str) -> bool:
    tools = message.get("tools")
    if not isinstance(tools, list):
        return False
    return any(
        isinstance(item, dict) and item.get("tool") == tool_name and item.get("decision") == "allowed"
        for item in tools
    )


def _named(message: dict[str, object], tool_name: str) -> bool:
    tools = message.get("tools")
    if not isinstance(tools, list):
        return False
    return any(isinstance(item, dict) and item.get("tool") == tool_name for item in tools)


def _denied(message: dict[str, object], tool_name: str) -> bool:
    tools = message.get("tools")
    if not isinstance(tools, list):
        return False
    return any(
        isinstance(item, dict) and item.get("tool") == tool_name and item.get("decision") == "denied"
        for item in tools
    )


def policy_checks(settings: Settings, cookie_file: Path, target_id: str) -> None:
    """Check assistant-policy boundaries. The model stays off, so no hosted call is made."""
    del settings
    cookie = cookie_file.read_text(encoding="utf-8").strip()
    if not cookie or not target_id.strip():
        raise ValueError("policy requires a session cookie and a target id")
    with httpx.Client(
        base_url="http://127.0.0.1:8000",
        cookies={"sentinel_session": cookie},
        timeout=180.0,
        trust_env=False,
    ) as client:
        created = client.post("/api/v1/chat/threads", json={"scope_type": "database", "scope_id": target_id})
        if created.status_code != 200:
            raise ValueError("Copilot thread was not created")
        thread_id = str(created.json().get("id") or "")
        if not thread_id:
            raise ValueError("Copilot thread did not return an id")
        pasted = _message(client, thread_id, "select * from v$session")
        if "SQL cannot be entered" not in str(pasted.get("content")) or not _denied(pasted, "execute_sql"):
            raise ValueError("Pasted SQL was not refused")
        if _allowed(pasted, "execute_sql"):
            raise ValueError("Pasted SQL was executed")
        concept = _message(client, thread_id, "what is a chain block?")
        concept_text = str(concept.get("content"))
        if concept.get("llm_used") is not False or "language model is not answering" not in concept_text.lower():
            raise ValueError("A general Oracle question called the disabled model or skipped the guidance path")
        if "was not queried" not in concept_text and "not queried" not in concept_text:
            raise ValueError("A general Oracle question did not say the database was not queried")
        if not _named(concept, "search_knowledge"):
            raise ValueError("A general Oracle question did not search approved documents")
        policy = _message(client, thread_id, "Does company policy require every database to run in ARCHIVELOG?")
        policy_text = str(policy.get("content"))
        if policy.get("llm_used") is not False:
            raise ValueError("A company-policy question used the disabled model")
        if "could not establish the company requirement" not in policy_text and "Collected evidence" not in policy_text:
            raise ValueError("A company-policy question was not tied to an approved document")
        change = _message(client, thread_id, "Enable ARCHIVELOG")
        if not _denied(change, "execute_sql") or _allowed(change, "execute_sql"):
            raise ValueError("A database change was not refused")
        if "language model is not answering" not in str(change.get("content")).lower():
            raise ValueError("A database change was not kept on the explanation path")
        measured = _message(client, thread_id, "How big is the USERS tablespace?")
        if not _allowed(measured, "get_tablespace_pressure") and not _allowed(measured, "get_tablespace_status"):
            raise ValueError("A tablespace question did not use an approved reading")
        if measured.get("llm_used") is not False or _allowed(measured, "execute_sql"):
            raise ValueError("A tablespace question used the model or executed SQL")
    print(json.dumps({
        "pasted_sql": "refused",
        "concept": "guidance_without_model",
        "company_policy": "evidence_or_not_established",
        "change": "not_executed",
        "tablespace": "approved_reading",
    }), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "schema", "session", "policy", "serve"))
    parser.add_argument("--cookie-file", help="File that receives or supplies the session cookie")
    parser.add_argument("--target-id", help="Database target id for the policy checks")
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
    elif args.action == "policy":
        if not args.cookie_file or not args.cookie_file.strip() or not args.target_id or not args.target_id.strip():
            raise ValueError("policy requires --cookie-file and --target-id")
        policy_checks(settings, Path(args.cookie_file), args.target_id)
    else:
        import uvicorn
        from sentinel.main import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=8000)


if __name__ == "__main__":
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
