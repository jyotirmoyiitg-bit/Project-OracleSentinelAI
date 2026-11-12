"""Transactional probe execution with fenced leases and explicit poll-failure evidence."""

import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.collectors import checkpoints
from sentinel.collectors.health_status import compute_health
from sentinel.collectors.normalization import PROBES, normalize, utcnow
from sentinel.collectors.profiles import stale_after
from sentinel.config.settings import Settings
from sentinel.domain.models import CapabilityMatrix, Observation
from sentinel.notifications.smtp import SMTPNotifier
from sentinel.persistence.models import DatabaseTarget, DiagnosticEvent, PollRun
from sentinel.persistence.models import Observation as StoredObservation
from sentinel.persistence.telemetry import checkpoints_for, current_for, save_batch
from sentinel.rules.compliance_store import evaluate_target_compliance, sync_compliance_notifications

logger = logging.getLogger(__name__)


class ProbeAdapter(Protocol):
    async def collect_probe(self, probe: str, alias: str) -> list[Observation]: ...
    async def detect_capabilities(self, database_id: str) -> CapabilityMatrix: ...


class AdapterFactory(Protocol):
    def __call__(self, target: DatabaseTarget) -> ProbeAdapter: ...


class CollectionRunner:
    def __init__(
        self, sessions: sessionmaker[Session], settings: Settings, adapters: AdapterFactory,
        notifier: SMTPNotifier | None = None,
    ):
        self.sessions = sessions
        self.settings = settings
        self.adapters = adapters
        self.notifier = notifier or SMTPNotifier(settings)

    def run(self, target_id: uuid.UUID, probe: str) -> str:
        started = utcnow()
        with self.sessions.begin() as session:
            target = session.get(DatabaseTarget, target_id)
            if target is None or not target.enabled:
                return "disabled"
            version = checkpoints.claim(session, target_id, probe, started, self.settings.collector_lease_seconds)
            if version is None:
                return "leased"
            run_id = uuid.uuid4()
            session.add(PollRun(
                id=run_id, target_id=target_id, probe_type=probe, started_at=started,
                status="running", lease_version=version,
            ))
            _ = target.credentials
            configuration_version = target.configuration_version
            session.expunge(target)

        observations: list[Observation] = []
        caps = None
        error_class = None
        try:
            adapter = self.adapters(target)
            alias = target.tns_alias
            if not alias:
                raise ValueError("Target has no TNS alias")
            observations = normalize(asyncio.run(adapter.collect_probe(probe, alias)), str(target_id), probe)
            if not observations or all(obs.value is None for obs in observations):
                raise ValueError("Probe returned no measurements")
            if probe == "connectivity":
                caps = asyncio.run(adapter.detect_capabilities(alias)).to_dict()
        except Exception as exc:  # noqa: BLE001 -- durable failure boundary for independent scheduled jobs
            # One failing target must not stop other scheduled jobs. Store the class, not credentials in driver messages.
            error_class = type(exc).__name__
            logger.error("Poll failed: target=%s probe=%s error_class=%s", target_id, probe, error_class)

        finished = utcnow()
        with self.sessions.begin() as session:
            target = session.scalar(select(DatabaseTarget).where(DatabaseTarget.id == target_id).with_for_update())
            if target is None:
                return "deleted"
            run = session.get(PollRun, run_id)
            if run is None:
                raise RuntimeError("Poll run disappeared before finalization")
            if not target.enabled or target.configuration_version != configuration_version:
                checkpoints.finish(session, target_id, probe, version, finished, "TargetChanged")
                run.status = "obsolete"
                run.error_class = "TargetChanged"
                run.finished_at = finished
                run.duration_seconds = (finished - started).total_seconds()
                return "obsolete"
            if not checkpoints.finish(session, target_id, probe, version, finished, error_class):
                run.status = "interrupted"
                run.error_class = "LeaseExpired"
                run.finished_at = finished
                run.duration_seconds = (finished - started).total_seconds()
                logger.warning("Discarded expired poll result: target=%s probe=%s version=%s", target_id, probe, version)
                return "expired"
            run.status = "failed" if error_class else "success"
            run.finished_at = finished
            run.duration_seconds = (finished - started).total_seconds()
            run.error_class = error_class
            if error_class:
                session.add(DiagnosticEvent(
                    target_id=target_id, source="poll_failure", source_event_key=f"poll_failure:{run_id}",
                    occurred_at=finished, severity="warning", code="POLL_FAILED",
                    message=f"{probe} polling failed ({error_class})",
                    raw_payload={"poll_run_id": str(run_id), "probe_type": probe, "error_class": error_class},
                ))
            else:
                save_batch(session, target_id, probe, observations)
                target.last_seen = finished
                if probe == "connectivity" and caps is not None:
                    target.last_connection_at = finished
                    target.capability_snapshot = caps
            points = checkpoints_for(session, [target_id])
            current = current_for(session, [target_id])
            if probe in {"configuration", "instance", "fra_archive"}:
                self._evaluate_compliance(session, target, current, points, finished)
            target.last_health_status = compute_health(target, points, current, self.settings, finished)
            if target.last_health_status == "offline":
                target.health_valid_until = finished + timedelta(seconds=stale_after(
                    self.settings, target.poll_profile, target.environment, "connectivity",
                ))
            else:
                expirations = [
                    point.last_success_at + timedelta(seconds=stale_after(
                        self.settings, target.poll_profile, target.environment, point.collector_type,
                    )) for point in points if point.last_success_at and point.collector_type in PROBES
                ]
                expirations.extend(
                    obs.timestamp + timedelta(seconds=stale_after(
                        self.settings, target.poll_profile, target.environment, obs.probe_type,
                    )) for obs in current if obs.probe_type in PROBES
                )
                target.health_valid_until = min(expirations) if expirations else None
        return "failed" if error_class else "success"

    def _evaluate_compliance(self, session: Session, target: DatabaseTarget, current, points, finished) -> None:
        # A SAVEPOINT isolates a compliance bug from the outer poll transaction: on failure we
        # roll back only the compliance work (never raising) and the rest of the poll still commits.
        try:
            with session.begin_nested():
                evaluations = evaluate_target_compliance(session, target, current, points, self.settings, finished)
                sync_compliance_notifications(session, target, evaluations, self.settings, self.notifier, finished)
        except Exception as exc:  # noqa: BLE001 -- a compliance bug must never hard-stop collection
            logger.error(
                "Compliance evaluation failed: target=%s error_class=%s", target.id, type(exc).__name__,
            )

    def retain(self) -> None:
        cutoff = utcnow() - timedelta(days=self.settings.observation_retention_days)
        # Batches bound row locks and transaction size; later maintenance runs continue deletion.
        with self.sessions.begin() as session:
            for model, column in (
                (StoredObservation, StoredObservation.timestamp), (PollRun, PollRun.started_at),
                (DiagnosticEvent, DiagnosticEvent.occurred_at),
            ):
                ids = select(model.id).where(column < cutoff).limit(10000)
                if model is DiagnosticEvent:
                    ids = ids.where(DiagnosticEvent.source == "poll_failure")
                if model is PollRun:
                    ids = ids.where(PollRun.status != "running")
                session.execute(delete(model).where(model.id.in_(ids)))
