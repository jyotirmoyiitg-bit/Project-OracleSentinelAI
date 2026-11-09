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
    rule_states: Mapped[list["RuleState"]] = relationship(cascade="all, delete-orphan")
    incidents: Mapped[list["Incident"]] = relationship(back_populates="target", cascade="all, delete-orphan")


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


class RuleDefinition(Base):
    """Rule override/enablement state; the YAML files remain the source of truth for new rules."""

    __tablename__ = "rule_definitions"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=True)
    scope: Mapped[str] = mapped_column(String(50), default="all", nullable=True)
    expression: Mapped[str] = mapped_column(Text, default="", nullable=True)
    hysteresis: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    severity: Mapped[str] = mapped_column(String(20), default="warning", nullable=True)
    message: Mapped[str] = mapped_column(Text, default="", nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="yaml", nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=True)


class RuleState(Base):
    """Per rule/target (and optional scope key, e.g. tablespace name) hysteresis and cooldown state."""

    __tablename__ = "rule_states"
    __table_args__ = (UniqueConstraint("rule_id", "target_id", "scope_key", name="uq_rule_target_scope"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("database_targets.id", ondelete="CASCADE"))
    scope_key: Mapped[str] = mapped_column(String(255), default="", nullable=True)
    consecutive_matches: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=True)


class Incident(Base):
    """Deduplicated incident grouping recurring rule firings by fingerprint."""

    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_fingerprint", "fingerprint"),
        Index("ix_incidents_target_state", "target_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    fingerprint: Mapped[str] = mapped_column(String(255))
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("database_targets.id", ondelete="CASCADE"))
    rule_id: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20), default="new", nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="warning", nullable=True)
    message: Mapped[str] = mapped_column(Text, default="", nullable=True)
    first_seen: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    owner: Mapped[str | None] = mapped_column(String(255))
    root_cause_candidate: Mapped[str | None] = mapped_column(String(255))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=True)

    target: Mapped[DatabaseTarget] = relationship(back_populates="incidents")
    events: Mapped[list["IncidentEvent"]] = relationship(back_populates="incident", cascade="all, delete-orphan")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="incident", cascade="all, delete-orphan")


class IncidentEvent(Base):
    """Immutable audit trail of incident state transitions, notes, and evidence."""

    __tablename__ = "incident_events"
    __table_args__ = (Index("ix_incident_events_incident_time", "incident_id", "timestamp"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(50))
    timestamp: Mapped[datetime] = mapped_column(default=utcnow)
    actor: Mapped[str] = mapped_column(String(255), default="system", nullable=True)
    reason: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=True)
    after: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=True)

    incident: Mapped[Incident] = relationship(back_populates="events")


class Notification(Base):
    """Persistent portal alert or SMTP delivery attempt for an incident."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_dedup", "dedup_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(20), default="smtp", nullable=True)
    recipient: Mapped[str] = mapped_column(String(500), default="", nullable=True)
    subject: Mapped[str] = mapped_column(String(500), default="", nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=True)
    dedup_id: Mapped[str] = mapped_column(String(255), default="", nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)

    incident: Mapped[Incident] = relationship(back_populates="notifications")


class CompliancePolicyVersion(Base):
    """Immutable, reviewable version of a compliance policy."""

    __tablename__ = "policy_definitions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_policy_definition_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(255))
    engine: Mapped[str] = mapped_column(String(20), default="oracle")
    enabled: Mapped[bool] = mapped_column(default=True)
    approval_status: Mapped[str] = mapped_column(String(20), default="approved")
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    applicability: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evaluation_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    environment_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recommended_action: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PolicyResult(Base):
    """Latest durable evaluation for one target and immutable policy version."""

    __tablename__ = "policy_results"
    __table_args__ = (
        UniqueConstraint("target_id", "policy_id", "policy_version", name="uq_policy_result_target_version"),
        Index("ix_policy_results_target_observed", "target_id", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("database_targets.id", ondelete="CASCADE"))
    policy_id: Mapped[str] = mapped_column(String(100))
    policy_version: Mapped[str] = mapped_column(String(50))
    observed_at: Mapped[datetime] = mapped_column(default=utcnow)
    result: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(20))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recommended_action: Mapped[str] = mapped_column(Text, default="")
