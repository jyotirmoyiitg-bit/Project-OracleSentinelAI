"""Database session management for SQLAlchemy."""

from collections.abc import Iterator
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from sentinel.config.settings import Settings

engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def init_db(settings: Settings) -> None:
    """Initialize database engine and session factory."""
    global engine, SessionLocal
    options = {"pool_pre_ping": True}
    if not settings.database_url.startswith("sqlite"):
        options.update(pool_size=settings.database_pool_size, max_overflow=settings.database_max_overflow)
    engine = create_engine(settings.database_url, **options)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_session() -> Iterator[Session]:
    """Provide one request-scoped session and always close it."""
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    with SessionLocal() as session:
        yield session


def close_session(session: Session) -> None:
    """Close a database session."""
    session.close()
