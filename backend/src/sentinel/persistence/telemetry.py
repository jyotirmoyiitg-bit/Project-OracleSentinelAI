"""Indexed history and current-value queries for collection and dashboard reads."""

import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from sentinel.domain.models import Observation as DomainObservation
from sentinel.persistence.models import CollectorCheckpoint, CurrentObservation, Observation


def save_batch(session: Session, target_id: uuid.UUID, probe: str, observations: list[DomainObservation]) -> None:
    session.execute(delete(CurrentObservation).where(
        CurrentObservation.target_id == target_id, CurrentObservation.probe_type == probe,
    ))
    seen = set()
    for obs in observations:
        label_key = hashlib.sha256(json.dumps(obs.labels, sort_keys=True).encode()).hexdigest()
        identity = (obs.metric, label_key)
        if identity in seen:
            raise ValueError(f"Duplicate observation identity: {obs.metric}")
        seen.add(identity)
        observation_id = uuid.uuid4()
        values = {
            "target_id": target_id, "metric_name": obs.metric, "timestamp": obs.collected_at,
            "value": float(obs.value) if isinstance(obs.value, (int, float)) else None,
            "value_str": str(obs.value) if obs.value is not None and not isinstance(obs.value, (int, float)) else None,
            "unit": obs.unit, "labels": obs.labels, "quality": obs.quality, "source": obs.source,
        }
        session.add(Observation(id=observation_id, **values))
        session.add(CurrentObservation(
            observation_id=observation_id, label_key=label_key, probe_type=probe, **values,
        ))
    session.flush()


def current_for(session: Session, ids: list[uuid.UUID]) -> list[CurrentObservation]:
    if not ids:
        return []
    return list(session.scalars(select(CurrentObservation).where(CurrentObservation.target_id.in_(ids))))


def checkpoints_for(session: Session, ids: list[uuid.UUID]) -> list[CollectorCheckpoint]:
    if not ids:
        return []
    return list(session.scalars(select(CollectorCheckpoint).where(CollectorCheckpoint.target_id.in_(ids))))


def history(
    session: Session, target_id: uuid.UUID, start: datetime, end: datetime,
    metric_name: str | None, quality: str | None, limit: int, offset: int = 0,
) -> list[Observation]:
    stmt = select(Observation).where(
        Observation.target_id == target_id, Observation.timestamp >= start, Observation.timestamp <= end,
    )
    if metric_name:
        stmt = stmt.where(Observation.metric_name == metric_name)
    if quality:
        stmt = stmt.where(Observation.quality == quality)
    # Select the newest bounded window, then return it in chart order.
    rows = list(session.scalars(stmt.order_by(Observation.timestamp.desc(), Observation.id.desc())
                               .offset(offset).limit(limit)))
    return list(reversed(rows))
