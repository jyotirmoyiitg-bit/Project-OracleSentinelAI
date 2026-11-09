"""Alert-log polling, rule evaluation, incident creation, and notification dispatch.

Mirrors the transactional lease pattern in `collectors.jobs.CollectionRunner`: one failing
target must never stop other scheduled jobs, and rules/incidents/notifications continue to
function even when AI is disabled (they have no dependency on it at all).
"""

import asyncio
import logging
import uuid
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.collectors import checkpoints
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.domain.models import DiagnosticEvent as DomainEvent
from sentinel.incidents.fingerprinting import compute_fingerprint
from sentinel.incidents.lifecycle import IncidentLifecycle
from sentinel.notifications.smtp import SMTPNotifier
from sentinel.notifications.templates import render
from sentinel.persistence.models import (
    DatabaseTarget,
    DiagnosticEvent,
    Incident,
    Notification,
    RuleState,
)
from sentinel.persistence.telemetry import current_for
from sentinel.rules.engine import RuleContext, RulesEngine, RuleStateView
from sentinel.rules.store import sync_rule_definitions
from sentinel.rules.yaml_loader import load_rules

logger = logging.getLogger(__name__)

ALERT_COLLECTOR_TYPE = "alert_log"


class EventPollingAdapter(Protocol):
    async def poll_events(self, alias: str, checkpoint: dict | None) -> tuple[list[DomainEvent], dict]: ...


class AlertEvaluationAdapterFactory(Protocol):
    def __call__(self, target: DatabaseTarget) -> EventPollingAdapter: ...


class AlertEvaluationRunner:
    def __init__(
        self, sessions: sessionmaker[Session], settings: Settings, adapters: AlertEvaluationAdapterFactory,
        notifier: SMTPNotifier | None = None,
    ):
        self.sessions = sessions
        self.settings = settings
        self.adapters = adapters
        self.notifier = notifier or SMTPNotifier(settings)

    def run(self, target_id: uuid.UUID) -> str:
        started = utcnow()
        with self.sessions.begin() as session:
            target = session.get(DatabaseTarget, target_id)
            if target is None or not target.enabled:
                return "disabled"
            version = checkpoints.claim(session, target_id, ALERT_COLLECTOR_TYPE, started, self.settings.collector_lease_seconds)
            if version is None:
                return "leased"
            checkpoint_row = checkpoints.get_checkpoint(session, target_id, ALERT_COLLECTOR_TYPE)
            cursor = dict(checkpoint_row.cursor) if checkpoint_row else {}
            alias = target.tns_alias
            configuration_version = target.configuration_version
            _ = target.credentials
            session.expunge(target)

        events: list[DomainEvent] = []
        new_cursor = cursor
        error_class = None
        try:
            if not alias:
                raise ValueError("Target has no TNS alias")
            adapter = self.adapters(target)
            events, new_cursor = asyncio.run(adapter.poll_events(alias, cursor))
        except Exception as exc:  # noqa: BLE001 -- durable failure boundary for independent scheduled jobs
            error_class = type(exc).__name__
            logger.error("Alert-log poll failed: target=%s error_class=%s", target_id, error_class)

        finished = utcnow()
        with self.sessions.begin() as session:
            target = session.get(DatabaseTarget, target_id)
            if target is None:
                return "deleted"
            if not target.enabled or target.configuration_version != configuration_version:
                checkpoints.finish(session, target_id, ALERT_COLLECTOR_TYPE, version, finished, "TargetChanged")
                return "obsolete"
            if not checkpoints.finish(session, target_id, ALERT_COLLECTOR_TYPE, version, finished, error_class):
                return "expired"
            if error_class:
                return "failed"

            new_event_codes = self._persist_events(session, target_id, events)
            checkpoint_row = checkpoints.get_checkpoint(session, target_id, ALERT_COLLECTOR_TYPE)
            if checkpoint_row is not None:
                checkpoint_row.cursor = {**new_cursor, "version": version}

            self._evaluate_rules(session, target, new_event_codes, finished)
        return "success"

    @staticmethod
    def _persist_events(session: Session, target_id: uuid.UUID, events: list[DomainEvent]) -> dict[str, int]:
        new_event_codes: dict[str, int] = {}
        for event in events:
            exists = session.scalar(select(DiagnosticEvent.id).where(
                DiagnosticEvent.target_id == target_id, DiagnosticEvent.source_event_key == event.source_id,
            ))
            if exists is not None:
                continue
            source = event.source_id.split(":", 1)[0] if ":" in event.source_id else event.source_id
            session.add(DiagnosticEvent(
                target_id=target_id, source=source, source_event_key=event.source_id,
                occurred_at=event.occurred_at, severity=event.severity, code=event.code,
                message=event.message, raw_payload=event.attributes,
            ))
            if event.code:
                new_event_codes[event.code] = new_event_codes.get(event.code, 0) + 1
        return new_event_codes

    def _evaluate_rules(
        self, session: Session, target: DatabaseTarget, event_codes: dict[str, int], now,
    ) -> None:
        rules = sync_rule_definitions(session, load_rules(self.settings.rules_path))
        engine = RulesEngine(rules)

        current = current_for(session, [target.id])
        metrics: dict[str, float] = {}
        scoped_metrics: dict[str, dict[str, float]] = {}
        for obs in current:
            if obs.value is None:
                continue
            tablespace = obs.labels.get("tablespace_name")
            if tablespace:
                scoped_metrics.setdefault(obs.metric_name, {})[tablespace] = obs.value
            else:
                metrics[obs.metric_name] = obs.value

        context = RuleContext(
            target_id=str(target.id), now=now, metrics=metrics, scoped_metrics=scoped_metrics,
            event_codes=event_codes,
        )
        state_rows = {
            (row.rule_id, row.scope_key): row
            for row in session.scalars(select(RuleState).where(RuleState.target_id == target.id))
        }
        states = {
            key: RuleStateView(consecutive_matches=row.consecutive_matches, last_fired_at=row.last_fired_at)
            for key, row in state_rows.items()
        }
        firings, updated_states = engine.evaluate(context, states)

        for key, view in updated_states.items():
            rule_id, scope_key = key
            row = state_rows.get(key)
            if row is None:
                row = RuleState(target_id=target.id, rule_id=rule_id, scope_key=scope_key)
                session.add(row)
            row.consecutive_matches = view.consecutive_matches
            row.last_fired_at = view.last_fired_at

        for firing in firings:
            fingerprint = compute_fingerprint(firing.rule.id, target.id, firing.scope_key)
            incident, created = IncidentLifecycle.open_or_update(
                session, target_id=target.id, rule_id=firing.rule.id, fingerprint=fingerprint,
                severity=firing.rule.severity, message=firing.message,
                evidence={"observed": firing.observed, "scope_key": firing.scope_key}, now=now,
            )
            if created:
                session.flush()
                subject, _ = render(incident, target.display_name)
                session.add(Notification(
                    incident_id=incident.id, channel="web", recipient="", subject=subject,
                    dedup_id=f"{incident.id}:web", status="unread", attempt_count=0,
                ))
                if self.settings.smtp_enabled:
                    for recipient in self.settings.notification_recipients:
                        self._notify(session, incident, target, recipient)

    def _notify(self, session: Session, incident: Incident, target: DatabaseTarget, recipient: str) -> None:
        subject, body = render(incident, target.display_name)
        dedup_id = f"{incident.id}:smtp:{recipient}"
        existing = session.scalar(select(Notification).where(Notification.dedup_id == dedup_id))
        if existing is not None and existing.status == "sent":
            return
        sent, error_class = self.notifier.send(recipient, subject, body)
        record = existing or Notification(
            incident_id=incident.id, channel="smtp", recipient=recipient, subject=subject, dedup_id=dedup_id,
            attempt_count=0,
        )
        record.attempt_count = (record.attempt_count or 0) + 1
        record.status = "sent" if sent else "failed"
        record.error_class = error_class
        if sent:
            record.sent_at = utcnow()
        if existing is None:
            session.add(record)
