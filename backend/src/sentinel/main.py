"""FastAPI application entry point for Oracle Sentinel AI."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sentinel.api.v1.databases import router as databases_router
from sentinel.config.secrets import init_resolver
from sentinel.config.settings import Settings, get_settings
from sentinel.persistence.models import Base
from sentinel.persistence.session import init_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan events."""
    settings: Settings = app.state.settings
    logger.info("Starting Oracle Sentinel AI %s", settings.app_version)

    # Initialize secret resolver for decrypting .env passwords
    init_resolver(settings.secret_key)
    logger.info("Secret resolver initialized")

    # Initialize database
    try:
        init_db(settings)
        # Create tables if they don't exist
        from sentinel.persistence.session import engine
        Base.metadata.create_all(bind=engine)
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize database: %s", e)

    yield

    logger.info("Shutting down Oracle Sentinel AI")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Oracle Sentinel AI - Central Oracle Database Monitoring Platform",
        lifespan=lifespan,
    )
    
    # Store settings in app state
    app.state.settings = settings
    
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Register routers
    app.include_router(databases_router)
    
    # Health endpoints
    @app.get("/health/live")
    async def health_live():
        """Application liveness check."""
        return {"status": "alive"}
    
    @app.get("/health/ready")
    async def health_ready():
        """Application readiness check — verifies PostgreSQL connection."""
        from sentinel.persistence.session import get_session, close_session
        
        try:
            session = get_session()
            session.execute("SELECT 1")
            close_session(session)
            return {"status": "ready", "database": "connected"}
        except Exception as e:
            logger.error("Readiness check failed: %s", e)
            return {"status": "not_ready", "database": str(e)}
    
    return app


# Create app instance for uvicorn
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "sentinel.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
