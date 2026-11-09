from sentinel.persistence.models import RuleDefinition as RuleDefinitionRow
from sentinel.rules.definitions import RuleCondition, RuleDefinition
from sentinel.rules.store import sync_rule_definitions


def make_rule(**overrides):
    defaults = dict(
        id="tablespace_warning", enabled=True, scope="all",
        expression="storage.tablespace.used_pct > 85",
        condition=RuleCondition(metric="storage.tablespace.used_pct", operator=">", value=85),
        hysteresis=1, cooldown_minutes=30, severity="warning", message="full at {value}%",
    )
    defaults.update(overrides)
    return RuleDefinition(**defaults)


def test_new_rule_is_inserted_with_source_yaml(sessions):
    rule = make_rule()
    with sessions.begin() as session:
        merged = sync_rule_definitions(session, [rule])
    assert merged == [rule]
    with sessions() as session:
        row = session.get(RuleDefinitionRow, "tablespace_warning")
        assert row.source == "yaml"
        assert row.enabled is True
        assert row.severity == "warning"


def test_yaml_source_row_is_resynced_from_spec_every_call(sessions):
    rule = make_rule()
    with sessions.begin() as session:
        sync_rule_definitions(session, [rule])

    updated_rule = make_rule(scope="production", expression="storage.tablespace.used_pct > 90", message="new message")
    with sessions.begin() as session:
        merged = sync_rule_definitions(session, [updated_rule])
    assert merged[0].scope == "production"
    assert merged[0].message == "new message"
    with sessions() as session:
        row = session.get(RuleDefinitionRow, "tablespace_warning")
        assert row.scope == "production"
        assert row.message == "new message"
        assert row.source == "yaml"


def test_override_source_row_overrides_enabled_severity_cooldown_hysteresis(sessions):
    rule = make_rule()
    with sessions.begin() as session:
        sync_rule_definitions(session, [rule])
    with sessions.begin() as session:
        row = session.get(RuleDefinitionRow, "tablespace_warning")
        row.source = "override"
        row.enabled = False
        row.severity = "critical"
        row.cooldown_minutes = 5
        row.hysteresis = 4

    changed_yaml_rule = make_rule(scope="production", expression="storage.tablespace.used_pct > 70")
    with sessions.begin() as session:
        merged = sync_rule_definitions(session, [changed_yaml_rule])
    result = merged[0]
    assert result.enabled is False
    assert result.severity == "critical"
    assert result.cooldown_minutes == 5
    assert result.hysteresis == 4
    # The expression/scope come from the override spec passed in (unchanged by the DB row).
    assert result.scope == "production"
    assert result.expression == "storage.tablespace.used_pct > 70"
    with sessions() as session:
        row = session.get(RuleDefinitionRow, "tablespace_warning")
        assert row.source == "override"
        assert row.enabled is False
