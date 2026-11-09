"""FastAPI lifecycle for APIs and the independent central collector."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from sentinel.api.v1.databases import router
from sentinel.collectors.adapters import adapter_for
from sentinel.collectors.jobs import CollectionRunner
from sentinel.collectors.scheduler import CollectorScheduler
from sentinel.config.secrets import init_resolver
from sentinel.config.settings import Settings, get_settings
from sentinel.persistence import session as persistence

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
        scheduler = None
        if settings.scheduler_enabled:
            runner = CollectionRunner(
                sessions, settings, lambda target: adapter_for(target, settings),
            )
            scheduler = CollectorScheduler(runner)
            scheduler.start()
        app.state.collector_scheduler = scheduler
        try:
            yield
        finally:
            if scheduler:
                await asyncio.to_thread(scheduler.stop)
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
    app.include_router(router)

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
