"""Load reviewed, versioned compliance policies from the repository YAML catalog."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PolicyDefinition:
    policy_id: str
    version: str
    name: str
    engine: str
    enabled: bool
    approval_status: str
    severity: str
    applicability: dict[str, Any]
    evaluation_config: dict[str, Any]
    environment_overrides: dict[str, Any]
    recommended_action: str


def load_policy_definitions() -> list[PolicyDefinition]:
    path = Path(__file__).resolve().parents[4] / "policies" / "compliance.yaml"
    with path.open(encoding="utf-8") as policy_file:
        raw = yaml.safe_load(policy_file)
    if not isinstance(raw, dict) or not isinstance(raw.get("policies"), list):
        raise TypeError(f"Invalid compliance policy catalog: {path}")
    definitions = []
    seen = set()
    for item in raw["policies"]:
        if not isinstance(item, dict):
            raise TypeError(f"Invalid policy entry in {path}")
        definition = PolicyDefinition(
            policy_id=_required_string(item, "id"),
            version=_required_string(item, "version"),
            name=_required_string(item, "name"),
            engine=_required_string(item, "engine"),
            enabled=bool(item.get("enabled", True)),
            approval_status=_required_string(item, "approval_status"),
            severity=_required_string(item, "severity"),
            applicability=_mapping(item, "applicability"),
            evaluation_config=_mapping(item, "evaluation_config"),
            environment_overrides=_mapping(item, "environment_overrides"),
            recommended_action=_required_string(item, "recommended_action"),
        )
        if definition.approval_status not in {"draft", "approved", "rejected"}:
            raise ValueError(f"Invalid approval status for policy {definition.policy_id}")
        if definition.severity not in {"info", "warning", "critical"}:
            raise ValueError(f"Invalid severity for policy {definition.policy_id}")
        identity = (definition.policy_id, definition.version)
        if identity in seen:
            raise ValueError(f"Duplicate compliance policy version: {identity}")
        seen.add(identity)
        definitions.append(definition)
    return definitions


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Policy field {key!r} must be a non-empty string")
    return value.strip()


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise TypeError(f"Policy field {key!r} must be a mapping")
    return value
