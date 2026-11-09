import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from sentinel.collectors.normalization import utcnow
from sentinel.incidents.lifecycle import IncidentLifecycle
from sentinel.persistence.models import DiagnosticEvent, Incident, Notification, RuleState


@pytest.fixture
def runtime(settings, tmp_path):
    helper = Path(__file__).resolve().parents[2] / "test_real_phase3" / "real_runtime.py"
    spec = importlib.util.spec_from_file_location("phase3_real_test_runtime", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "default.yaml").write_text(
        "rules:\n"
        "  - id: ora_00257\n"
        "    scope: all\n"
        "    expression: diagnostic_events.code == 'ORA-00257'\n"
        "    severity: critical\n"
        "    hysteresis: 1\n"
        "    cooldown_minutes: 60\n"
        "    message: Archive log destination full\n",
        encoding="utf-8",
    )
    settings.rules_path = str(rules_dir)
    settings.synthetic_mode = False
    settings.smtp_host = None
    settings.notification_recipients = ["ops@example.com"]
    return module


def test_simulate_alert_runs_live_pipeline_without_oracle_connection(
    runtime, sessions, settings, make_target, capsys,
):
    original_rules_path = settings.rules_path
    target_id = make_target(alias="LIVE_TEST")
    fired_at = utcnow()
    with sessions.begin() as session:
        historical, _ = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="ora_00257", fingerprint="historical",
            severity="critical", message="Historical incident",
        )
        historical_id = historical.id
        IncidentLifecycle.resolve(session, historical.id, "previous-verification")
        session.add(RuleState(
            target_id=target_id, rule_id="ora_00257", scope_key="ORA-00257",
            consecutive_matches=5, last_fired_at=fired_at,
        ))

    run_id = str(uuid.uuid4())
    runtime.simulate_alert(settings, "LIVE_TEST", "ORA-00257", run_id)

    result = json.loads(capsys.readouterr().out)
    assert result["target_id"] == str(target_id)
    assert result["run_outcome"] == "success"
    assert result["run_id"] == run_id
    assert result["rule_id"] == f"verify_phase3_{run_id.replace('-', '')}_ora_00257"
    incident_id = uuid.UUID(result["incident_id"])
    runtime.simulate_alert(settings, "LIVE_TEST", "ORA-00257", run_id)
    assert json.loads(capsys.readouterr().out) == result
    with sessions() as session:
        events = list(session.scalars(select(DiagnosticEvent).where(DiagnosticEvent.target_id == target_id)))
        assert len(events) == 1
        event = events[0]
        assert event.code == "ORA-00257"
        assert event.raw_payload["synthetic"] is True
        assert event.raw_payload["run_id"] == run_id
        incident = session.get(Incident, incident_id)
        assert incident.rule_id == result["rule_id"]
        notifications = list(session.scalars(select(Notification).where(Notification.incident_id == incident.id)))
        assert len(notifications) == 1
        notification = notifications[0]
        assert notification.channel == "web"
        assert notification.status == "unread"
        assert notification.error_class is None

    with sessions.begin() as session:
        IncidentLifecycle.resolve(session, incident_id, "test_real_phase3")
    runtime.simulate_alert(settings, "LIVE_TEST", "ORA-00257")
    second_result = json.loads(capsys.readouterr().out)
    assert second_result["incident_id"] != result["incident_id"]
    assert second_result["rule_id"] != result["rule_id"]
    with sessions() as session:
        incidents = list(session.scalars(select(Incident).where(Incident.target_id == target_id)))
        assert len(incidents) == 3
        assert {item.state for item in incidents} == {"new", "resolved"}
        assert len(list(session.scalars(select(Notification).where(Notification.channel == "web")))) == 2
        assert session.get(Incident, historical_id).state == "resolved"
        state = session.scalar(select(RuleState).where(RuleState.rule_id == "ora_00257"))
        assert state.consecutive_matches == 5
        assert state.last_fired_at == fired_at
    assert settings.rules_path == original_rules_path


def test_simulate_alert_reports_lease_without_claiming_an_incident(runtime, settings, make_target, monkeypatch, capsys):
    target_id = make_target(alias="LIVE_TEST")
    rule_paths = []

    def leased(runner, target):
        assert target == target_id
        rule_paths.append(Path(runner.settings.rules_path))
        assert rule_paths[-1].is_dir()
        return "leased"

    monkeypatch.setattr("sentinel.collectors.alert_flow.AlertEvaluationRunner.run", leased)
    runtime.simulate_alert(settings, "LIVE_TEST", "ORA-00257")
    result = json.loads(capsys.readouterr().out)
    assert result["run_outcome"] == "leased"
    assert result["incident_id"] is None
    assert not rule_paths[0].exists()


def test_simulate_alert_rejects_success_without_an_incident(runtime, settings, make_target, monkeypatch):
    make_target(alias="LIVE_TEST")
    monkeypatch.setattr("sentinel.collectors.alert_flow.AlertEvaluationRunner.run", lambda *_: "success")
    with pytest.raises(ValueError, match="created no verification incident"):
        runtime.simulate_alert(settings, "LIVE_TEST", "ORA-00257")


@pytest.mark.parametrize("code", ["ORA-99999", "ORA-01653"])
def test_simulate_alert_rejects_missing_configured_rule(runtime, settings, code):
    with pytest.raises(ValueError, match="enabled YAML event rule"):
        runtime.simulate_alert(settings, "LIVE_TEST", code)
