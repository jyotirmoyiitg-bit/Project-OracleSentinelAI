"""Persistence helpers for reviewed policy versions and their latest evaluations."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.collectors.health_status import is_fresh
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.incidents.fingerprinting import compute_fingerprint
from sentinel.incidents.lifecycle import IncidentLifecycle
from sentinel.notifications.smtp import SMTPNotifier
from sentinel.notifications.templates import render
from sentinel.persistence.models import (
    CollectorCheckpoint,
    CompliancePolicyVersion,
    CurrentObservation,
    DatabaseTarget,
    Incident,
    Notification,
    PolicyResult,
)
from sentinel.rules.compliance import MetricEvidence, PolicyEvaluation, evaluate_policy
from sentinel.rules.policy_definitions import PolicyDefinition, load_policy_definitions


def sync_policy_definitions(
    session: Session, definitions: list[PolicyDefinition] | None = None,
) -> list[CompliancePolicyVersion]:
    source = definitions or load_policy_definitions()
    existing = {
        (policy.policy_id, policy.version): policy
        for policy in session.scalars(select(CompliancePolicyVersion))
    }
    records = []
    for policy in source:
        identity = (policy.policy_id, policy.version)
        values = {
            "name": policy.name,
            "engine": policy.engine,
            "enabled": policy.enabled,
            "approval_status": policy.approval_status,
            "severity": policy.severity,
            "applicability": policy.applicability,
            "evaluation_config": policy.evaluation_config,
            "environment_overrides": policy.environment_overrides,
            "recommended_action": policy.recommended_action,
        }
        record = existing.get(identity)
        if record is None:
            record = CompliancePolicyVersion(policy_id=policy.policy_id, version=policy.version, **values)
            session.add(record)
        elif any(getattr(record, key) != value for key, value in values.items()):
            raise ValueError(
                f"Policy {policy.policy_id} version {policy.version} is immutable; publish a new version."
            )
        records.append(record)
    session.flush()
    return records


def evaluate_target_compliance(
    session: Session,
    target: DatabaseTarget,
    current: list[CurrentObservation],
    checkpoints: list[CollectorCheckpoint],
    settings: Settings,
    now: datetime | None = None,
) -> list[PolicyEvaluation]:
    checked_at = now or utcnow()
    definitions = load_policy_definitions()
    sync_policy_definitions(session, definitions)
    checkpoint_by_probe = {checkpoint.collector_type: checkpoint for checkpoint in checkpoints}
    metrics: dict[str, MetricEvidence] = {}
    parameters: dict[str, MetricEvidence] = {}

    for observation in current:
        probe = _probe_for(observation.metric_name)
        if probe is None or not is_fresh(checkpoint_by_probe.get(probe), target, settings, checked_at):
            continue
        value: float | str | None = (
            observation.value if observation.value is not None else observation.value_str
        )
        item = MetricEvidence(
            value,
            observation.timestamp,
            observation.source,
            observation.quality,
            str(observation.observation_id),
        )
        if observation.metric_name == "config.parameter":
            parameter = observation.labels.get("parameter", "").lower()
            if parameter:
                parameters[parameter] = item
        else:
            metrics[observation.metric_name] = item

    evaluations = [
        evaluate_policy(policy, target.engine, target.environment, metrics, parameters)
        for policy in definitions
    ]
    for evaluation in evaluations:
        result = session.scalar(select(PolicyResult).where(
            PolicyResult.target_id == target.id,
            PolicyResult.policy_id == evaluation.policy_id,
            PolicyResult.policy_version == evaluation.version,
        ).with_for_update())
        values: dict[str, Any] = {
            "observed_at": checked_at,
            "result": evaluation.result,
            "severity": evaluation.severity,
            "evidence": evaluation.evidence,
            "recommended_action": evaluation.recommended_action,
        }
        if result is None:
            session.add(PolicyResult(
                target_id=target.id,
                policy_id=evaluation.policy_id,
                policy_version=evaluation.version,
                **values,
            ))
        else:
            for key, value in values.items():
                setattr(result, key, value)
    session.flush()
    return evaluations


def current_compliance_results(session: Session, target_id: UUID) -> dict[tuple[str, str], PolicyResult]:
    return {
        (result.policy_id, result.policy_version): result
        for result in session.scalars(select(PolicyResult).where(PolicyResult.target_id == target_id))
    }


def _probe_for(metric_name: str) -> str | None:
    if metric_name.startswith(("database.", "instance.")):
        return "instance"
    if metric_name == "config.parameter":
        return "configuration"
    if metric_name.startswith("storage.fra."):
        return "fra_archive"
    return None


def sync_compliance_notifications(
    session: Session,
    target: DatabaseTarget,
    evaluations: list[PolicyEvaluation],
    settings: Settings,
    notifier: SMTPNotifier,
    now: datetime | None = None,
) -> None:
    """Raise a notification for each non-compliant policy instead of a hard stop.

    A failed policy evaluation never blocks collection or raises an exception — it opens
    (or extends) an incident via the Phase 3 lifecycle and dispatches a notification through
    the same portal/SMTP channels used for rule-based incidents. A policy that becomes
    compliant again automatically resolves its open incident.
    """
    now = now or utcnow()
    for evaluation in evaluations:
        rule_id = f"compliance.{evaluation.policy_id}"
        fingerprint = compute_fingerprint(rule_id, target.id, "")
        if evaluation.result == "fail":
            _open_noncompliance_incident(session, target, evaluation, rule_id, fingerprint, settings, notifier, now)
        elif evaluation.result == "pass":
            _resolve_if_open(session, fingerprint, evaluation, now)


def _open_noncompliance_incident(
    session: Session,
    target: DatabaseTarget,
    evaluation: PolicyEvaluation,
    rule_id: str,
    fingerprint: str,
    settings: Settings,
    notifier: SMTPNotifier,
    now: datetime,
) -> None:
    message = (
        f"Policy {evaluation.policy_id} (v{evaluation.version}) is not compliant"
        + (f": {evaluation.recommended_action}" if evaluation.recommended_action else "")
    )
    incident, created = IncidentLifecycle.open_or_update(
        session, target_id=target.id, rule_id=rule_id, fingerprint=fingerprint,
        severity=evaluation.severity, message=message, evidence=evaluation.evidence, now=now,
    )
    if not created:
        return
    session.flush()
    subject, _ = render(incident, target.display_name)
    session.add(Notification(
        incident_id=incident.id, channel="web", recipient="", subject=subject,
        dedup_id=f"{incident.id}:web", status="unread", attempt_count=0,
    ))
    if settings.smtp_enabled:
        for recipient in settings.notification_recipients:
            _notify_smtp(session, notifier, incident, target, recipient, now)


def _resolve_if_open(session: Session, fingerprint: str, evaluation: PolicyEvaluation, now: datetime) -> None:
    existing = session.scalar(
        select(Incident).where(Incident.fingerprint == fingerprint, Incident.state != "resolved"),
    )
    if existing is None:
        return
    IncidentLifecycle.resolve(
        session, existing.id, actor="system",
        reason=f"Policy {evaluation.policy_id} evaluated as compliant", now=now,
    )


def _notify_smtp(
    session: Session, notifier: SMTPNotifier, incident: Incident, target: DatabaseTarget, recipient: str, now: datetime,
) -> None:
    subject, body = render(incident, target.display_name)
    dedup_id = f"{incident.id}:smtp:{recipient}"
    existing = session.scalar(select(Notification).where(Notification.dedup_id == dedup_id))
    if existing is not None and existing.status == "sent":
        return
    sent, error_class = notifier.send(recipient, subject, body)
    record = existing or Notification(
        incident_id=incident.id, channel="smtp", recipient=recipient, subject=subject, dedup_id=dedup_id,
        attempt_count=0,
    )
    record.attempt_count = (record.attempt_count or 0) + 1
    record.status = "sent" if sent else "failed"
    record.error_class = error_class
    if sent:
        record.sent_at = now
    if existing is None:
        session.add(record)
