from dataclasses import replace
from datetime import datetime

from sentinel.rules.compliance import MetricEvidence, evaluate_policy
from sentinel.rules.policy_definitions import load_policy_definitions


def _evidence(value):
    return MetricEvidence(value, datetime(2026, 1, 1), "fixture", "measured")


def test_policies_use_actual_metrics_as_evidence():
    policies = {item.policy_id: item for item in load_policy_definitions()}
    metrics = {
        "database.log_mode": _evidence("ARCHIVELOG"),
        "database.flashback_on": _evidence("YES"),
        "storage.fra.space_limit": _evidence(1024),
    }
    parameters = {
        "db_create_file_dest": _evidence("/u01/oradata"),
        "db_recovery_file_dest": _evidence("/u02/fra"),
        "db_recovery_file_dest_size": _evidence("10G"),
    }
    assert evaluate_policy(policies["oracle.omf.configured"], "oracle", "prod", metrics, parameters).result == "pass"
    archive = evaluate_policy(policies["oracle.archivelog.enabled"], "oracle", "prod", metrics, parameters)
    assert archive.result == "pass"
    assert archive.evidence["database.log_mode"]["value"] == "ARCHIVELOG"
    assert evaluate_policy(policies["oracle.fra.configured"], "oracle", "prod", metrics, parameters).result == "pass"
    fra = evaluate_policy(policies["oracle.fra.configured"], "oracle", "production", metrics, parameters)
    assert fra.severity == "critical"
    assert evaluate_policy(policies["oracle.flashback.enabled"], "oracle", "prod", metrics, parameters).result == "pass"


def test_missing_or_unconfigured_values_are_unknown_or_fail():
    policies = {item.policy_id: item for item in load_policy_definitions()}
    assert evaluate_policy(
        policies["oracle.archivelog.enabled"], "oracle", "prod", {}, {},
    ).result == "unknown"
    assert evaluate_policy(
        policies["oracle.omf.configured"], "oracle", "prod", {}, {"db_create_file_dest": _evidence("")},
    ).result == "fail"
    assert evaluate_policy(
        policies["oracle.backup.freshness"], "oracle", "prod", {}, {},
    ).result == "not_applicable"


def test_environment_override_can_mark_policy_not_applicable_and_change_severity():
    policy = next(item for item in load_policy_definitions() if item.policy_id == "oracle.omf.configured")
    overridden = replace(
        policy,
        environment_overrides={"development": {"required": False, "severity": "info"}},
    )
    result = evaluate_policy(overridden, "oracle", "development", {}, {})
    assert result.result == "not_applicable"
    assert result.severity == "info"


def test_environment_override_can_change_the_required_archive_mode():
    policy = next(item for item in load_policy_definitions() if item.policy_id == "oracle.archivelog.enabled")
    overridden = replace(policy, environment_overrides={"lab": {"required_mode": "NOARCHIVELOG"}})
    result = evaluate_policy(
        overridden, "oracle", "lab", {"database.log_mode": _evidence("NOARCHIVELOG")}, {},
    )
    assert result.result == "pass"


def test_policy_version_applicability_returns_unknown_without_version_and_not_applicable_outside_range():
    policy = next(item for item in load_policy_definitions() if item.policy_id == "oracle.omf.configured")
    restricted = replace(policy, applicability={"minimum_version": "19.0"})
    assert evaluate_policy(restricted, "oracle", "prod", {}, {}).result == "unknown"
    old_version = {"instance.version": _evidence("12.2.0.1")}
    assert evaluate_policy(restricted, "oracle", "prod", old_version, {}).result == "not_applicable"


def test_policy_version_is_immutable_in_catalog():
    policy = next(item for item in load_policy_definitions() if item.policy_id == "oracle.omf.configured")
    assert policy.version
    assert policy.approval_status == "approved"
