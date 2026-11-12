"""FastAPI lifecycle for APIs and the independent central collector."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from sentinel.api.v1.auth import router as auth_router
from sentinel.api.v1.chat import router as chat_router
from sentinel.api.v1.compliance import router as compliance_router
from sentinel.api.v1.databases import router
from sentinel.api.v1.events import router as events_router
from sentinel.api.v1.incidents import router as incidents_router
from sentinel.api.v1.knowledge import router as knowledge_router
from sentinel.auth.middleware import RequireSignIn
from sentinel.auth.service import ensure_bootstrap_operator
from sentinel.collectors.adapters import adapter_for
from sentinel.collectors.alert_flow import AlertEvaluationRunner
from sentinel.collectors.jobs import CollectionRunner
from sentinel.collectors.scheduler import CollectorScheduler
from sentinel.config.secrets import init_resolver
from sentinel.config.settings import Settings, get_settings
from sentinel.notifications.smtp import SMTPNotifier
from sentinel.persistence import session as persistence
from sentinel.rules.compliance_store import sync_policy_definitions

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = app.state.settings
    init_resolver(settings.secret_key)
    persistence.init_db(settings)
    engine, sessions = persistence.engine, persistence.SessionLocal
    if engine is None or sessions is None:
        raise RuntimeError("Database initialization failed")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            if not inspect(conn).has_table("poll_runs"):
                raise RuntimeError("Phase 2 schema missing; run alembic upgrade head before starting")
            if not inspect(conn).has_table("incidents"):
                raise RuntimeError("Phase 3 schema missing; run alembic upgrade head before starting")
            if not inspect(conn).has_table("policy_results"):
                raise RuntimeError("Phase 4 schema missing; run alembic upgrade head before starting")
            if not inspect(conn).has_table("documents"):
                raise RuntimeError("Phase 5 schema missing; run alembic upgrade head before starting")
            if not inspect(conn).has_table("chat_threads"):
                raise RuntimeError("Phase 6 schema missing; run alembic upgrade head before starting")
            if settings.auth_required and not inspect(conn).has_table("operator_accounts"):
                raise RuntimeError("Sign-in schema missing; run alembic upgrade head before starting")
        with sessions.begin() as session:
            sync_policy_definitions(session)
            if settings.auth_required:
                ensure_bootstrap_operator(session, settings)
        scheduler = None
        knowledge_scheduler = None
        if settings.scheduler_enabled:
            notifier = SMTPNotifier(settings)
            runner = CollectionRunner(
                sessions, settings, lambda target: adapter_for(target, settings), notifier,
            )
            alert_runner = AlertEvaluationRunner(
                sessions, settings, lambda target: adapter_for(target, settings), notifier,
            )
            scheduler = CollectorScheduler(runner, alert_runner)
            scheduler.start()
        if settings.knowledge_enabled:
            from sentinel.knowledge.service import KnowledgeIndexer, KnowledgeScheduler

            try:
                knowledge_scheduler = KnowledgeScheduler(
                    KnowledgeIndexer(sessions, settings), settings.knowledge_scan_interval_seconds,
                )
                knowledge_scheduler.start()
            except Exception:
                logger.exception("Knowledge scheduler failed to start")
                knowledge_scheduler = None
        app.state.collector_scheduler = scheduler
        app.state.session_factory = sessions
        app.state.knowledge_scheduler = knowledge_scheduler
        try:
            yield
        finally:
            if scheduler:
                await asyncio.to_thread(scheduler.stop)
            if knowledge_scheduler:
                await asyncio.to_thread(knowledge_scheduler.stop)
    finally:
        engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins,
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )
    app.add_middleware(RequireSignIn, settings=settings)
    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(incidents_router)
    app.include_router(events_router)
    app.include_router(compliance_router)
    app.include_router(knowledge_router)
    app.include_router(chat_router)

    @app.get("/health/live")
    def health_live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def health_ready() -> dict[str, str]:
        try:
            if persistence.engine is None:
                raise RuntimeError("Database is not initialized")
            with persistence.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, RuntimeError) as exc:
            logger.error("Readiness failed: %s", type(exc).__name__)
            raise HTTPException(503, "Database not ready") from exc
        return {"status": "ready", "database": "connected"}

    return app


app = create_app()
