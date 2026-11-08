"""Database session management for SQLAlchemy."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sentinel.config.settings import Settings

engine = None
SessionLocal = None


def init_db(settings: Settings) -> None:
    """Initialize database engine and session factory."""
    global engine, SessionLocal
    engine = create_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_session() -> Session:
    """Get a database session."""
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return SessionLocal()


def close_session(session: Session) -> None:
    """Close a database session."""
    session.close()
