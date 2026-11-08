"""Repository pattern for data access."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from sentinel.domain.models import DiagnosticEvent as DomainEvent, Observation as DomainObservation
from sentinel.persistence.models import (
    CollectorCheckpoint,
    DatabaseTarget,
    DiagnosticEvent,
    Observation,
    TargetCredential,
)


class DatabaseTargetRepository:
    """Repository for database target CRUD operations."""

    @staticmethod
    def list_all(session: Session, enabled_only: bool = False) -> list[DatabaseTarget]:
        """List all registered database targets."""
        stmt = select(DatabaseTarget)
        if enabled_only:
            stmt = stmt.where(DatabaseTarget.enabled == True)
        stmt = stmt.order_by(DatabaseTarget.display_name)
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def get_by_id(session: Session, target_id: uuid.UUID) -> DatabaseTarget | None:
        """Get a database target by ID."""
        return session.get(DatabaseTarget, target_id)

    @staticmethod
    def create(
        session: Session,
        engine: str,
        display_name: str,
        tns_alias: str | None = None,
        environment: str = "unknown",
        owner: str = "",
        enabled: bool = True,
        poll_profile: dict[str, Any] | None = None,
    ) -> DatabaseTarget:
        """Create a new database target."""
        target = DatabaseTarget(
            engine=engine,
            display_name=display_name,
            tns_alias=tns_alias,
            environment=environment,
            owner=owner,
            enabled=enabled,
            poll_profile=poll_profile or {},
        )
        session.add(target)
        session.flush()
        return target

    @staticmethod
    def update(
        session: Session,
        target_id: uuid.UUID,
        **kwargs: Any,
    ) -> DatabaseTarget | None:
        """Update a database target."""
        target = session.get(DatabaseTarget, target_id)
        if target is None:
            return None
        for key, value in kwargs.items():
            if hasattr(target, key):
                setattr(target, key, value)
        target.updated_at = datetime.utcnow()
        session.flush()
        return target

    @staticmethod
    def delete(session: Session, target_id: uuid.UUID) -> bool:
        """Delete a database target."""
        target = session.get(DatabaseTarget, target_id)
        if target is None:
            return False
        session.delete(target)
        session.flush()
        return True

    @staticmethod
    def update_last_connection(session: Session, target_id: uuid.UUID, health_status: str) -> None:
        """Update last connection time and health status."""
        target = session.get(DatabaseTarget, target_id)
        if target:
            target.last_connection_at = datetime.utcnow()
            target.last_health_status = health_status
            session.flush()


class ObservationRepository:
    """Repository for observation storage and retrieval."""

    @staticmethod
    def save_observations(session: Session, target_id: uuid.UUID, observations: list[DomainObservation]) -> None:
        """Save a batch of observations."""
        for obs in observations:
            value = None
            value_str = None
            if isinstance(obs.value, (int, float)):
                value = float(obs.value)
            elif obs.value is not None:
                value_str = str(obs.value)

            row = Observation(
                target_id=target_id,
                metric_name=obs.metric,
                timestamp=obs.collected_at,
                value=value,
                value_str=value_str,
                unit=obs.unit,
                labels=obs.labels,
                quality=obs.quality,
                source=obs.source,
            )
            session.add(row)
        session.flush()

    @staticmethod
    def get_latest(session: Session, target_id: uuid.UUID, limit: int = 100) -> list[Observation]:
        """Get latest observations for a target."""
        stmt = (
            select(Observation)
            .where(Observation.target_id == target_id)
            .order_by(desc(Observation.timestamp))
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def get_by_metric(session: Session, target_id: uuid.UUID, metric_name: str, limit: int = 50) -> list[Observation]:
        """Get observations for a specific metric."""
        stmt = (
            select(Observation)
            .where(Observation.target_id == target_id, Observation.metric_name == metric_name)
            .order_by(desc(Observation.timestamp))
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())


class DiagnosticEventRepository:
    """Repository for diagnostic event storage and retrieval."""

    @staticmethod
    def save_events(session: Session, target_id: uuid.UUID, events: list[DomainEvent]) -> int:
        """Save diagnostic events, skipping duplicates. Returns count of new events."""
        count = 0
        for event in events:
            # Check for duplicate
            existing = session.execute(
                select(DiagnosticEvent).where(
                    DiagnosticEvent.target_id == target_id,
                    DiagnosticEvent.source_event_key == event.source_id,
                )
            ).scalar_one_or_none()

            if existing is None:
                row = DiagnosticEvent(
                    target_id=target_id,
                    source=event.source_id.split(":")[0] if ":" in event.source_id else event.source_id,
                    source_event_key=event.source_id,
                    occurred_at=event.occurred_at,
                    severity=event.severity,
                    code=event.code,
                    message=event.message,
                    raw_payload=event.attributes,
                )
                session.add(row)
                count += 1
        session.flush()
        return count

    @staticmethod
    def get_events(
        session: Session,
        target_id: uuid.UUID,
        severity: str | None = None,
        code: str | None = None,
        limit: int = 100,
    ) -> list[DiagnosticEvent]:
        """Get diagnostic events with optional filters."""
        stmt = select(DiagnosticEvent).where(DiagnosticEvent.target_id == target_id)
        if severity:
            stmt = stmt.where(DiagnosticEvent.severity == severity)
        if code:
            stmt = stmt.where(DiagnosticEvent.code == code)
        stmt = stmt.order_by(desc(DiagnosticEvent.occurred_at)).limit(limit)
        return list(session.execute(stmt).scalars().all())


class CollectorCheckpointRepository:
    """Repository for collector checkpoints."""

    @staticmethod
    def get_checkpoint(session: Session, target_id: uuid.UUID, collector_type: str) -> dict[str, Any]:
        """Get checkpoint for a collector."""
        checkpoint = session.execute(
            select(CollectorCheckpoint).where(
                CollectorCheckpoint.target_id == target_id,
                CollectorCheckpoint.collector_type == collector_type,
            )
        ).scalar_one_or_none()
        return checkpoint.cursor if checkpoint else {}

    @staticmethod
    def save_checkpoint(session: Session, target_id: uuid.UUID, collector_type: str, cursor: dict[str, Any]) -> None:
        """Save or update checkpoint."""
        checkpoint = session.execute(
            select(CollectorCheckpoint).where(
                CollectorCheckpoint.target_id == target_id,
                CollectorCheckpoint.collector_type == collector_type,
            )
        ).scalar_one_or_none()

        if checkpoint:
            checkpoint.cursor = cursor
            checkpoint.updated_at = datetime.utcnow()
        else:
            checkpoint = CollectorCheckpoint(
                target_id=target_id,
                collector_type=collector_type,
                cursor=cursor,
            )
            session.add(checkpoint)
        session.flush()


class TargetCredentialRepository:
    """Repository for target credentials."""

    @staticmethod
    def save_credential(
        session: Session,
        target_id: uuid.UUID,
        secret_reference: str,
        username: str = "GSYSTEM",
    ) -> TargetCredential:
        """Save credential reference for a target."""
        existing = session.execute(
            select(TargetCredential).where(TargetCredential.target_id == target_id)
        ).scalar_one_or_none()

        if existing:
            existing.secret_reference = secret_reference
            existing.username = username
            return existing
        else:
            cred = TargetCredential(
                target_id=target_id,
                secret_reference=secret_reference,
                username=username,
            )
            session.add(cred)
            session.flush()
            return cred
