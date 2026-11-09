"""Persistence helpers for reviewed policy versions and their latest evaluations."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.collectors.health_status import is_fresh
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.persistence.models import (
    CollectorCheckpoint,
    CompliancePolicyVersion,
    CurrentObservation,
    DatabaseTarget,
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
