"""Deterministic compliance evaluation with evidence from collected Oracle metrics."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sentinel.rules.policy_definitions import PolicyDefinition


@dataclass(frozen=True)
class MetricEvidence:
    value: float | str | None
    observed_at: datetime
    source: str
    quality: str
    observation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "observed_at": self.observed_at.isoformat(),
            "source": self.source,
            "quality": self.quality,
            "observation_id": self.observation_id,
        }


@dataclass(frozen=True)
class PolicyEvaluation:
    policy_id: str
    version: str
    result: str
    severity: str
    evidence: dict[str, Any]
    recommended_action: str


def evaluate_policy(
    policy: PolicyDefinition,
    engine: str,
    environment: str,
    metrics: dict[str, MetricEvidence],
    parameters: dict[str, MetricEvidence],
) -> PolicyEvaluation:
    override = policy.environment_overrides.get(environment.lower(), {})
    if not isinstance(override, dict):
        raise TypeError(f"Environment override for {environment!r} must be a mapping")
    severity = override.get("severity", policy.severity)
    if severity not in {"info", "warning", "critical"}:
        raise ValueError(f"Invalid environment severity override for policy {policy.policy_id}")
    evidence: dict[str, Any] = {}
    result: str
    applicability = _applicability(policy, engine, environment, metrics)

    if applicability is False or override.get("required") is False or not policy.enabled or policy.approval_status != "approved":
        result = "not_applicable"
    elif applicability is None:
        result = "unknown"
        evidence["reason"] = "Database version is unavailable for policy applicability"
    elif policy.policy_id == "oracle.omf.configured":
        parameter = str(override.get(
            "parameter", policy.evaluation_config.get("parameter", "db_create_file_dest"),
        )).lower()
        item = parameters.get(parameter)
        evidence[parameter] = item.to_dict() if item else None
        result = _configured(item)
    elif policy.policy_id == "oracle.archivelog.enabled":
        item = metrics.get("database.log_mode")
        evidence["database.log_mode"] = item.to_dict() if item else None
        required_mode = str(override.get("required_mode", policy.evaluation_config.get("required_mode", "ARCHIVELOG")))
        result = "unknown" if item is None else "pass" if str(item.value).upper() == required_mode.upper() else "fail"
    elif policy.policy_id == "oracle.fra.configured":
        destination = parameters.get("db_recovery_file_dest")
        size = parameters.get("db_recovery_file_dest_size")
        capacity = metrics.get("storage.fra.space_limit")
        evidence.update({
            "db_recovery_file_dest": destination.to_dict() if destination else None,
            "db_recovery_file_dest_size": size.to_dict() if size else None,
            "storage.fra.space_limit": capacity.to_dict() if capacity else None,
        })
        if destination is None or size is None or capacity is None:
            result = "unknown"
        else:
            configured_size = _oracle_size_bytes(size.value)
            actual_capacity = _oracle_size_bytes(capacity.value)
            minimum_capacity = float(override.get("minimum_capacity_gb", 0)) * (1024 ** 3)
            result = (
                "pass" if destination.value and configured_size > 0 and actual_capacity > 0
                and actual_capacity >= minimum_capacity
                else "fail"
            )
    elif policy.policy_id == "oracle.flashback.enabled":
        item = metrics.get("database.flashback_on")
        evidence["database.flashback_on"] = item.to_dict() if item else None
        required_value = str(override.get("required_value", "YES"))
        result = "unknown" if item is None else "pass" if str(item.value).upper() == required_value.upper() else "fail"
    elif policy.policy_id == "oracle.backup.freshness":
        if policy.evaluation_config.get("evidence_supported") is not True:
            result = "not_applicable"
            evidence["reason"] = "No authorized backup evidence source is configured"
        else:
            item = metrics.get("backup.last_success_at")
            evidence["backup.last_success_at"] = item.to_dict() if item else None
            result = "unknown"
    else:
        result = "error"
        evidence["reason"] = "No evaluator is registered for this policy"

    return PolicyEvaluation(
        policy_id=policy.policy_id,
        version=policy.version,
        result=result,
        severity=severity,
        evidence=evidence,
        recommended_action=policy.recommended_action,
    )


def _configured(item: MetricEvidence | None) -> str:
    if item is None:
        return "unknown"
    if item.value is None:
        return "unknown"
    return "pass" if str(item.value).strip() else "fail"


def _oracle_size_bytes(value: float | str | None) -> float:
    if value is None:
        return 0
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGTP]?)\s*", str(value), re.IGNORECASE)
    if match is None:
        return 0
    suffixes = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5}
    return float(match.group(1)) * suffixes[match.group(2).upper()]


def _applicability(
    policy: PolicyDefinition,
    engine: str,
    environment: str,
    metrics: dict[str, MetricEvidence],
) -> bool | None:
    if engine.lower() != policy.engine.lower():
        return False
    applicability = policy.applicability
    engines = applicability.get("engines")
    if isinstance(engines, list) and engine.lower() not in {str(value).lower() for value in engines}:
        return False
    environments = applicability.get("environments")
    if isinstance(environments, list) and environment.lower() not in {
        str(value).lower() for value in environments
    }:
        return False
    minimum, maximum = applicability.get("minimum_version"), applicability.get("maximum_version")
    if minimum is None and maximum is None:
        return True
    version = metrics.get("instance.version")
    if version is None:
        return None
    installed = _version_parts(str(version.value))
    if installed is None:
        return None
    minimum_parts = _version_parts(str(minimum)) if minimum is not None else None
    maximum_parts = _version_parts(str(maximum)) if maximum is not None else None
    if minimum is not None and minimum_parts is None or maximum is not None and maximum_parts is None:
        return None
    if minimum_parts is not None and installed < minimum_parts:
        return False
    return maximum_parts is None or installed <= maximum_parts


def _version_parts(value: str) -> tuple[int, ...] | None:
    components = re.findall(r"\d+", value)
    if not components:
        return None
    return tuple(int(component) for component in components[:5]) + (0,) * max(0, 5 - len(components))
