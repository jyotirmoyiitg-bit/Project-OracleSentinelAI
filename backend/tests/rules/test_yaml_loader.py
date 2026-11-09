import pytest

from sentinel.rules.definitions import RuleCondition
from sentinel.rules.yaml_loader import load_rules, load_rules_from_text, parse_expression


@pytest.mark.parametrize(("expression", "metric", "operator", "value"), [
    ("storage.tablespace.used_pct > 85", "storage.tablespace.used_pct", ">", 85),
    ("storage.fra.used_pct >= 80.5", "storage.fra.used_pct", ">=", 80.5),
    ("diagnostic_events.code == 'ORA-00257'", "diagnostic_events.code", "==", "ORA-00257"),
    ("resources.used_pct != 0", "resources.used_pct", "!=", 0),
])
def test_parse_expression_valid(expression, metric, operator, value):
    condition = parse_expression(expression)
    assert condition == RuleCondition(metric=metric, operator=operator, value=value)


@pytest.mark.parametrize("expression", [
    "",
    "storage.tablespace.used_pct",
    "storage.tablespace.used_pct 85",
    "__import__('os').system('ls') > 85",
    "storage.tablespace.used_pct > __import__('os')",
    "storage.tablespace.used_pct > os.getenv('X')",
    "print('x') > 85",
    "storage.tablespace.used_pct >> 85",
])
def test_parse_expression_rejects_unsafe_or_malformed(expression):
    with pytest.raises(ValueError):
        parse_expression(expression)


def test_load_rules_from_text_parses_rule_list():
    text = """
rules:
  - id: rule_a
    enabled: true
    scope: all
    expression: storage.tablespace.used_pct > 85
    hysteresis: 2
    cooldown_minutes: 30
    severity: warning
    message: "full at {value}%"
"""
    rules = load_rules_from_text(text)
    assert len(rules) == 1
    rule = rules[0]
    assert rule.id == "rule_a"
    assert rule.hysteresis == 2
    assert rule.condition.operator == ">"


def test_load_rules_from_text_rejects_duplicate_ids():
    text = """
rules:
  - id: dup
    expression: storage.tablespace.used_pct > 85
  - id: dup
    expression: storage.fra.used_pct > 50
"""
    with pytest.raises(ValueError, match="Duplicate rule id"):
        load_rules_from_text(text)


@pytest.mark.parametrize("entry", [
    "id: missing_expression",
    "expression: 'storage.tablespace.used_pct > 85'",
])
def test_load_rules_from_text_rejects_malformed_entries(entry):
    text = f"rules:\n  - {entry}\n"
    with pytest.raises(ValueError):
        load_rules_from_text(text)


def test_load_rules_from_text_with_no_rules_key_returns_empty():
    assert load_rules_from_text("other: value\n") == []
    assert load_rules_from_text("") == []


def test_load_rules_reads_all_yaml_files_in_directory(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "rules:\n  - id: rule_a\n    expression: storage.tablespace.used_pct > 85\n", encoding="utf-8",
    )
    (tmp_path / "b.yml").write_text(
        "rules:\n  - id: rule_b\n    expression: storage.fra.used_pct > 50\n", encoding="utf-8",
    )
    rules = load_rules(tmp_path)
    assert {rule.id for rule in rules} == {"rule_a", "rule_b"}


def test_load_rules_rejects_duplicate_ids_across_files(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "rules:\n  - id: dup\n    expression: storage.tablespace.used_pct > 85\n", encoding="utf-8",
    )
    (tmp_path / "b.yaml").write_text(
        "rules:\n  - id: dup\n    expression: storage.fra.used_pct > 50\n", encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate rule id"):
        load_rules(tmp_path)


def test_load_rules_returns_empty_for_missing_path(tmp_path):
    assert load_rules(tmp_path / "does-not-exist") == []
