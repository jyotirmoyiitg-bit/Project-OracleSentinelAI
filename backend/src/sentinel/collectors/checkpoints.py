"""Atomic, version-fenced leases shared by collector processes."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from sentinel.persistence.models import CollectorCheckpoint, PollRun


def claim(session: Session, target_id: uuid.UUID, probe: str, now: datetime, lease_seconds: int) -> int | None:
    dialect = session.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise NotImplementedError("Collector leases support PostgreSQL and SQLite only")
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    session.execute(insert(CollectorCheckpoint).values(
        id=uuid.uuid4(), target_id=target_id, collector_type=probe,
        cursor={}, updated_at=now, lease_version=0, status="pending",
    ).on_conflict_do_nothing(index_elements=["target_id", "collector_type"]))
    version = session.execute(
        update(CollectorCheckpoint).where(
            CollectorCheckpoint.target_id == target_id,
            CollectorCheckpoint.collector_type == probe,
            or_(CollectorCheckpoint.lease_expires_at.is_(None), CollectorCheckpoint.lease_expires_at <= now),
        ).values(
            lease_version=CollectorCheckpoint.lease_version + 1,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            last_attempt_at=now, status="running", error_class=None,
        ).returning(CollectorCheckpoint.lease_version)
    ).scalar_one_or_none()
    if version is not None:
        interrupted = session.scalars(select(PollRun).where(
            PollRun.target_id == target_id, PollRun.probe_type == probe, PollRun.status == "running",
        )).all()
        for run in interrupted:
            run.status = "interrupted"
            run.error_class = "LeaseExpired"
            run.finished_at = now
            run.duration_seconds = (now - run.started_at).total_seconds()
    return version


def finish(
    session: Session, target_id: uuid.UUID, probe: str, version: int, now: datetime, error_class: str | None,
) -> bool:
    values: dict[str, object] = {
        "lease_expires_at": None, "status": "failed" if error_class else "success",
        "error_class": error_class, "updated_at": now,
    }
    if error_class is None:
        values.update(last_success_at=now, cursor={"last_success_at": now.isoformat(), "version": version})
    result = session.execute(update(CollectorCheckpoint).where(
        CollectorCheckpoint.target_id == target_id,
        CollectorCheckpoint.collector_type == probe,
        CollectorCheckpoint.lease_version == version,
        CollectorCheckpoint.lease_expires_at > now,
    ).values(**values).returning(CollectorCheckpoint.id)).scalar_one_or_none()
    return result is not None


def get_checkpoint(session: Session, target_id: uuid.UUID, probe: str) -> CollectorCheckpoint | None:
    return session.execute(select(CollectorCheckpoint).where(
        CollectorCheckpoint.target_id == target_id, CollectorCheckpoint.collector_type == probe,
    )).scalar_one_or_none()
