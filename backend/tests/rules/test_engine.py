from datetime import datetime, timedelta

from sentinel.rules.definitions import RuleCondition, RuleDefinition
from sentinel.rules.engine import RuleContext, RuleStateView, RulesEngine

NOW = datetime(2026, 1, 1, 0, 0, 0)


def make_rule(**overrides):
    defaults = dict(
        id="tablespace_warning", enabled=True, scope="all",
        expression="storage.tablespace.used_pct > 85",
        condition=RuleCondition(metric="storage.tablespace.used_pct", operator=">", value=85),
        hysteresis=1, cooldown_minutes=30, severity="warning", message="full at {value}%",
    )
    defaults.update(overrides)
    return RuleDefinition(**defaults)


def test_fires_only_after_hysteresis_consecutive_matches():
    rule = make_rule(hysteresis=3)
    engine = RulesEngine([rule])
    states: dict = {}
    for i in range(2):
        context = RuleContext(target_id="t1", now=NOW + timedelta(minutes=i), metrics={"storage.tablespace.used_pct": 90})
        firings, states = engine.evaluate(context, states)
        assert firings == []
    context = RuleContext(target_id="t1", now=NOW + timedelta(minutes=2), metrics={"storage.tablespace.used_pct": 90})
    firings, states = engine.evaluate(context, states)
    assert len(firings) == 1
    assert firings[0].rule.id == "tablespace_warning"
    assert firings[0].observed == 90
    assert states[("tablespace_warning", "")].consecutive_matches == 3
    assert states[("tablespace_warning", "")].last_fired_at == NOW + timedelta(minutes=2)


def test_non_matching_evaluation_resets_consecutive_matches():
    rule = make_rule(hysteresis=3)
    engine = RulesEngine([rule])
    states: dict = {}
    for i in range(2):
        context = RuleContext(target_id="t1", now=NOW + timedelta(minutes=i), metrics={"storage.tablespace.used_pct": 90})
        _, states = engine.evaluate(context, states)
    assert states[("tablespace_warning", "")].consecutive_matches == 2
    context = RuleContext(target_id="t1", now=NOW + timedelta(minutes=2), metrics={"storage.tablespace.used_pct": 10})
    firings, states = engine.evaluate(context, states)
    assert firings == []
    assert states[("tablespace_warning", "")].consecutive_matches == 0


def test_cooldown_prevents_refiring_within_window():
    rule = make_rule(hysteresis=1, cooldown_minutes=30)
    engine = RulesEngine([rule])
    states: dict = {}
    first = RuleContext(target_id="t1", now=NOW, metrics={"storage.tablespace.used_pct": 90})
    firings, states = engine.evaluate(first, states)
    assert len(firings) == 1

    still_within = RuleContext(target_id="t1", now=NOW + timedelta(minutes=10), metrics={"storage.tablespace.used_pct": 90})
    firings, states = engine.evaluate(still_within, states)
    assert firings == []
    assert states[("tablespace_warning", "")].consecutive_matches == 2

    after_cooldown = RuleContext(target_id="t1", now=NOW + timedelta(minutes=31), metrics={"storage.tablespace.used_pct": 90})
    firings, states = engine.evaluate(after_cooldown, states)
    assert len(firings) == 1
    assert firings[0].message


def test_scoped_metrics_fire_independently_per_scope_key():
    rule = make_rule(hysteresis=1)
    engine = RulesEngine([rule])
    context = RuleContext(
        target_id="t1", now=NOW,
        scoped_metrics={"storage.tablespace.used_pct": {"USERS": 90, "SYSTEM": 10}},
    )
    firings, states = engine.evaluate(context, {})
    assert len(firings) == 1
    assert firings[0].scope_key == "USERS"
    assert states[("tablespace_warning", "USERS")].consecutive_matches == 1
    assert states[("tablespace_warning", "SYSTEM")].consecutive_matches == 0


def test_event_code_rule_fires_when_code_count_positive():
    rule = make_rule(
        id="ora_00257", expression="diagnostic_events.code == 'ORA-00257'",
        condition=RuleCondition(metric="diagnostic_events.code", operator="==", value="ORA-00257"),
        hysteresis=1,
    )
    engine = RulesEngine([rule])
    context = RuleContext(target_id="t1", now=NOW, event_codes={"ORA-00257": 1})
    firings, states = engine.evaluate(context, {})
    assert len(firings) == 1
    assert firings[0].scope_key == "ORA-00257"
    assert firings[0].observed == "ORA-00257"

    no_events = RuleContext(target_id="t1", now=NOW + timedelta(minutes=1), event_codes={})
    firings, states = engine.evaluate(no_events, states)
    assert firings == []


def test_maintenance_suppresses_all_firings_and_returns_states_unchanged():
    rule = make_rule(hysteresis=1)
    engine = RulesEngine([rule])
    prior_states = {("tablespace_warning", ""): RuleStateView(consecutive_matches=5, last_fired_at=NOW)}
    context = RuleContext(
        target_id="t1", now=NOW + timedelta(minutes=1), metrics={"storage.tablespace.used_pct": 90}, maintenance=True,
    )
    firings, states = engine.evaluate(context, prior_states)
    assert firings == []
    assert states == prior_states


def test_disabled_rule_is_excluded_from_evaluation():
    rule = make_rule(enabled=False, hysteresis=1)
    engine = RulesEngine([rule])
    context = RuleContext(target_id="t1", now=NOW, metrics={"storage.tablespace.used_pct": 90})
    firings, states = engine.evaluate(context, {})
    assert firings == []
    assert states == {}
