"""Typed SQLAlchemy models for registry, history, and collector state."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from sentinel.collectors.normalization import utcnow


class Base(DeclarativeBase):
    pass


class DatabaseTarget(Base):
    __tablename__ = "database_targets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    engine: Mapped[str] = mapped_column(String(20))
    display_name: Mapped[str] = mapped_column(String(255))
    tns_alias: Mapped[str | None] = mapped_column(String(255))
    environment: Mapped[str] = mapped_column(String(50), default="unknown", nullable=True)
    owner: Mapped[str] = mapped_column(String(255), default="", nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=True)
    poll_profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=True)
    capability_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=True)
    last_connection_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_health_status: Mapped[str] = mapped_column(String(50), default="unknown", nullable=True)
    health_valid_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)
    configuration_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=True)

    observations: Mapped[list[Observation]] = relationship(back_populates="target", cascade="all, delete-orphan")
    diagnostic_events: Mapped[list[DiagnosticEvent]] = relationship(back_populates="target", cascade="all, delete-orphan")
    checkpoints: Mapped[list[CollectorCheckpoint]] = relationship(back_populates="target", cascade="all, delete-orphan")
    credentials: Mapped[TargetCredential | None] = relationship(
        back_populates="target", uselist=False, cascade="all, delete-orphan",
    )
    poll_runs: Mapped[list[PollRun]] = relationship(cascade="all, delete-orphan")
    current_observations: Mapped[list[CurrentObservation]] = relationship(cascade="all, delete-orphan")


class TargetCredential(Base):
    __tablename__ = "target_credentials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("database_targets.id", ondelete="CASCADE"))
    secret_reference: Mapped[str] = mapped_column(String(500))
    username: Mapped[str] = mapped_column(String(255), default="GSYSTEM", nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=True)
    target: Mapped[DatabaseTarget] = relationship(back_populates="credentials")


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        Index("ix_observations_target_metric_time", "target_id", "metric_name", "timestamp"),
        Index("ix_observations_target_time", "target_id", "timestamp"),
        Index("ix_observations_timestamp", "timestamp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("database_targets.id", ondelete="CASCADE"))
    metric_name: Mapped[str] = mapped_column(String(255))
    timestamp: Mapped[datetime] = mapped_column(default=utcnow)
    value: Mapped[float | None] = mapped_column(Float)
    value_str: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(50))
    labels: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=True)
    quality: Mapped[str] = mapped_column(String(20), default="measured", nullable=True)
    source: Mapped[str] = mapped_column(String(255), default="", nullable=True)
    target: Mapped[DatabaseTarget] = relationship(back_populates="observations")


class DiagnosticEvent(Base):
    __tablename__ = "diagnostic_events"
    __table_args__ = (UniqueConstraint("target_id", "source_event_key", name="uq_target_source_event"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("database_targets.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(255), default="", nullable=True)
    source_event_key: Mapped[str] = mapped_column(String(500))
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    severity: Mapped[str] = mapped_column(String(20), default="info", nullable=True)
    code: Mapped[str | None] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text, default="", nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=True)
    target: Mapped[DatabaseTarget] = relationship(back_populates="diagnostic_events")


class CollectorCheckpoint(Base):
    __tablename__ = "collector_checkpoints"
    __table_args__ = (UniqueConstraint("target_id", "collector_type", name="uq_target_collector"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("database_targets.id", ondelete="CASCADE"))
    collector_type: Mapped[str] = mapped_column(String(100))
    cursor: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=dict, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=True)
    lease_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    error_class: Mapped[str | None] = mapped_column(String(100))
    target: Mapped[DatabaseTarget] = relationship(back_populates="checkpoints")


class PollRun(Base):
    __tablename__ = "poll_runs"
    __table_args__ = (
        Index("ix_poll_runs_target_probe_started", "target_id", "probe_type", "started_at"),
        Index("ix_poll_runs_started", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("database_targets.id", ondelete="CASCADE"))
    probe_type: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20))
    error_class: Mapped[str | None] = mapped_column(String(100))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    lease_version: Mapped[int] = mapped_column(Integer)


class CurrentObservation(Base):
    """Bounded latest-value projection; dashboard refresh never scans history."""

    __tablename__ = "current_observations"

    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("database_targets.id", ondelete="CASCADE"), primary_key=True)
    metric_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    label_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    observation_id: Mapped[uuid.UUID] = mapped_column()
    probe_type: Mapped[str] = mapped_column(String(100))
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    value: Mapped[float | None] = mapped_column(Float)
    value_str: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(50))
    labels: Mapped[dict[str, str]] = mapped_column(JSON)
    quality: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(255))
