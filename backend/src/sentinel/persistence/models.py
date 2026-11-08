"""SQLAlchemy models for Oracle Sentinel persistence layer."""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


class DatabaseTarget(Base):
    """Registered database target."""

    __tablename__ = "database_targets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    engine = Column(String(20), nullable=False)  # oracle, mysql, sqlserver
    display_name = Column(String(255), nullable=False)
    tns_alias = Column(String(255), nullable=True)
    environment = Column(String(50), default="unknown")  # production, staging, dev
    owner = Column(String(255), default="")
    enabled = Column(Boolean, default=True)
    poll_profile = Column(JSON, default=dict)
    capability_snapshot = Column(JSON, default=dict)
    last_connection_at = Column(DateTime, nullable=True)
    last_health_status = Column(String(50), default="unknown")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    observations = relationship("Observation", back_populates="target", cascade="all, delete-orphan")
    diagnostic_events = relationship("DiagnosticEvent", back_populates="target", cascade="all, delete-orphan")
    checkpoints = relationship("CollectorCheckpoint", back_populates="target", cascade="all, delete-orphan")
    credentials = relationship("TargetCredential", back_populates="target", uselist=False, cascade="all, delete-orphan")


class TargetCredential(Base):
    """Credential reference for a database target (never stores actual passwords)."""

    __tablename__ = "target_credentials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id = Column(UUID(as_uuid=True), ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False)
    secret_reference = Column(String(500), nullable=False)  # Reference to secret vault, not the password
    username = Column(String(255), default="GSYSTEM")
    rotated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    target = relationship("DatabaseTarget", back_populates="credentials")


class Observation(Base):
    """Metric observation from a database target."""

    __tablename__ = "observations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id = Column(UUID(as_uuid=True), ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False)
    metric_name = Column(String(255), nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    value = Column(Float, nullable=True)
    value_str = Column(Text, nullable=True)  # For string values
    unit = Column(String(50), nullable=True)
    labels = Column(JSON, default=dict)
    quality = Column(String(20), default="measured")
    source = Column(String(255), default="")

    target = relationship("DatabaseTarget", back_populates="observations")


class DiagnosticEvent(Base):
    """Diagnostic event from a database target (e.g., alert log entry)."""

    __tablename__ = "diagnostic_events"
    __table_args__ = (
        UniqueConstraint("target_id", "source_event_key", name="uq_target_source_event"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id = Column(UUID(as_uuid=True), ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False)
    source = Column(String(255), default="")
    source_event_key = Column(String(500), nullable=False)
    occurred_at = Column(DateTime, nullable=False)
    severity = Column(String(20), default="info")
    code = Column(String(50), nullable=True)
    message = Column(Text, default="")
    raw_payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    target = relationship("DatabaseTarget", back_populates="diagnostic_events")


class CollectorCheckpoint(Base):
    """Checkpoint for incremental collection."""

    __tablename__ = "collector_checkpoints"
    __table_args__ = (
        UniqueConstraint("target_id", "collector_type", name="uq_target_collector"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id = Column(UUID(as_uuid=True), ForeignKey("database_targets.id", ondelete="CASCADE"), nullable=False)
    collector_type = Column(String(100), nullable=False)  # e.g., "alert_log", "health"
    cursor = Column(JSON, default=dict)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    target = relationship("DatabaseTarget", back_populates="checkpoints")
