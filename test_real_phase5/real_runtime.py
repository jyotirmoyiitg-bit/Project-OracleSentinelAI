"""Private runtime helper for Verify_Phase5_Real.ps1; never prints credentials."""

import argparse
import json
import logging
import os
from pathlib import Path

import oracledb
from sentinel.config.settings import Settings
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


def load_settings() -> Settings:
    """Load root credentials plus Phase 5 live overrides, not inherited synthetic settings."""
    root_env, live_env = ROOT / ".env", Path(__file__).with_name(".env")
    if not root_env.is_file() or not live_env.is_file():
        raise ValueError("Both project-root .env and test_real_phase5\\.env are required")
    # Clear only model-field overrides in this child, not arbitrary env: secret
    # variables. This prevents a previous demo shell from overriding live settings.
    fields = {name.upper() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.upper() in fields:
            del os.environ[key]
    Settings.model_config["env_file"] = (str(root_env), str(live_env))
    settings = Settings()
    if settings.synthetic_mode or not settings.scheduler_enabled:
        raise ValueError("Live verification requires SYNTHETIC_MODE=false and SCHEDULER_ENABLED=true")
    if settings.ai_enabled:
        raise ValueError("Live Phase 5 verification requires AI_ENABLED=false")
    if settings.embedding_provider != "local":
        raise ValueError("Live Phase 5 verification requires EMBEDDING_PROVIDER=local")
    if settings.knowledge_enabled:
        raise ValueError(
            "Live Phase 5 verification requires KNOWLEDGE_ENABLED=false so the manual "
            "reindex does not race the knowledge scheduler",
        )
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
    # API module creation and Alembic call get_settings too. Ensure they see the
    # same absolute TNS directory while preserving the explicit file precedence.
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
    print(
        "Application schema upgraded to Alembic head "
        "(through the Phase 5 knowledge tables and pgvector indexes).",
        flush=True,
    )


def check_vector(settings: Settings, query: str) -> None:
    """Prove the live database can run pgvector and PostgreSQL full-text search."""
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            extension = connection.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"),
            ).scalar()
            chunks = connection.execute(
                text("SELECT COUNT(*) FROM document_chunks WHERE embedding IS NOT NULL"),
            ).scalar()
            distance = None
            if chunks:
                distance = connection.execute(
                    text(
                        "SELECT (embedding <=> embedding)::float "
                        "FROM document_chunks WHERE embedding IS NOT NULL LIMIT 1",
                    ),
                ).scalar()
            keyword_hits = connection.execute(
                text(
                    "SELECT COUNT(*) FROM document_chunks "
                    "WHERE content_tsv @@ plainto_tsquery('english', :query)",
                ),
                {"query": query},
            ).scalar()
            indexes = [
                row[0]
                for row in connection.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'document_chunks'"),
                )
            ]
        print(json.dumps({
            "vector_extension": bool(extension),
            "embedded_chunks": int(chunks or 0),
            "self_distance": distance,
            "keyword_hits": int(keyword_hits or 0),
            "indexes": indexes,
        }), flush=True)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "migrate", "serve", "check-vector"))
    parser.add_argument("--query", help="Full-text token to match in document_chunks (check-vector)")
    args = parser.parse_args()
    settings = load_settings()
    if args.action == "preflight":
        aliases = oracledb.ConnectParams(config_dir=settings.tns_admin).get_network_service_names()
        if not aliases:
            raise ValueError("No TNS aliases found in tnsnames.ora")
        # Return names and non-secret mode flags only. Never return descriptors or passwords.
        print(json.dumps({
            "aliases": sorted(aliases),
            "embedding_provider": settings.embedding_provider,
            "ai_enabled": settings.ai_enabled,
            "knowledge_enabled": settings.knowledge_enabled,
            "scheduler_enabled": settings.scheduler_enabled,
        }), flush=True)
    elif args.action == "migrate":
        migrate(settings)
    elif args.action == "check-vector":
        if not args.query or not args.query.strip():
            raise ValueError("check-vector requires --query <verification token>")
        check_vector(settings, args.query.strip())
    else:
        import uvicorn
        from sentinel.main import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, SQLAlchemyError, OSError, oracledb.Error) as exc:
        # Driver/validation exception text can embed a credential-bearing URL.
        # Our explicit ValueErrors are actionable and safe; never print driver text.
        if type(exc) is ValueError:
            logger.error("%s", exc)
        else:
            logger.error(
                "Live preparation failed (%s). Check database access and local configuration.",
                type(exc).__name__,
            )
        raise SystemExit(1) from None
