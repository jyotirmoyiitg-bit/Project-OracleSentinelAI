import pytest
from sqlalchemy import func, select

from sentinel.collectors.adapters import adapter_for
from sentinel.collectors.alert_flow import AlertEvaluationRunner
from sentinel.collectors.normalization import utcnow
from sentinel.domain.models import DiagnosticEvent as DomainEvent
from sentinel.persistence.models import DiagnosticEvent, Incident, Notification, TargetCredential

RULE_YAML = """
rules:
  - id: ora_00257
    enabled: true
    scope: all
    expression: diagnostic_events.code == 'ORA-00257'
    hysteresis: 1
    cooldown_minutes: 60
    severity: critical
    message: "Archive log destination full (ORA-00257)"
"""


class FakeAdapter:
    def __init__(self, events):
        self._events = events

    async def poll_events(self, alias, checkpoint):
        return list(self._events), {"last_offset": len(self._events)}


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def send(self, recipient, subject, body):
        self.calls.append((recipient, subject, body))
        return True, None


def _make_event():
    return DomainEvent(
        database_id="SYNTHETIC_HEALTHY", source_id="alert_log:1", occurred_at=utcnow(),
        severity="critical", code="ORA-00257", message="ORA-00257: archiver error. Connect internal only, until freed.",
    )


def test_alert_event_opens_incident_and_sends_notification_without_ai(sessions, settings, make_target, tmp_path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "default.yaml").write_text(RULE_YAML, encoding="utf-8")
    settings.rules_path = str(rules_dir)
    settings.notification_recipients = ["ops@example.com"]
    settings.smtp_enabled = True
    assert settings.ai_enabled is False  # default; the flow has zero AI/LLM dependency

    target_id = make_target()
    event = _make_event()
    adapter = FakeAdapter([event])
    notifier = FakeNotifier()
    runner = AlertEvaluationRunner(sessions, settings, lambda target: adapter, notifier=notifier)

    result = runner.run(target_id)
    assert result == "success"

    with sessions() as session:
        events = session.scalars(select(DiagnosticEvent).where(DiagnosticEvent.target_id == target_id)).all()
        assert len(events) == 1
        assert events[0].code == "ORA-00257"

        incidents = session.scalars(select(Incident).where(Incident.target_id == target_id)).all()
        assert len(incidents) == 1
        incident = incidents[0]
        assert incident.rule_id == "ora_00257"
        assert incident.severity == "critical"
        assert incident.message == "Archive log destination full (ORA-00257)"

        notifications = session.scalars(select(Notification).where(Notification.incident_id == incident.id)).all()
        assert len(notifications) == 2
        email = next(row for row in notifications if row.channel == "smtp")
        portal = next(row for row in notifications if row.channel == "web")
        assert email.status == "sent"
        assert email.recipient == "ops@example.com"
        assert portal.status == "unread"
    assert len(notifier.calls) == 1


def test_overlapping_poll_of_same_event_is_deduplicated_no_duplicate_rows(sessions, settings, make_target, tmp_path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "default.yaml").write_text(RULE_YAML, encoding="utf-8")
    settings.rules_path = str(rules_dir)
    settings.notification_recipients = ["ops@example.com"]
    settings.smtp_enabled = True

    target_id = make_target()
    event = _make_event()
    adapter = FakeAdapter([event])
    notifier = FakeNotifier()
    runner = AlertEvaluationRunner(sessions, settings, lambda target: adapter, notifier=notifier)

    assert runner.run(target_id) == "success"
    # Same adapter instance keeps returning the SAME record on the next poll (simulated overlap).
    assert runner.run(target_id) == "success"

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DiagnosticEvent)
                              .where(DiagnosticEvent.target_id == target_id)) == 1
        assert session.scalar(select(func.count()).select_from(Incident)
                              .where(Incident.target_id == target_id)) == 1
        assert session.scalar(select(func.count()).select_from(Notification)
                              .where(Notification.status == "sent")) == 1
        assert session.scalar(select(func.count()).select_from(Notification)
                              .where(Notification.channel == "web")) == 1
    assert len(notifier.calls) == 1


@pytest.mark.parametrize("recipients", [[], ["ops@example.com"]])
def test_smtp_disabled_still_creates_one_portal_alert(sessions, settings, make_target, tmp_path, recipients):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "default.yaml").write_text(RULE_YAML, encoding="utf-8")
    settings.rules_path = str(rules_dir)
    settings.smtp_enabled = False
    settings.notification_recipients = recipients
    notifier = FakeNotifier()
    runner = AlertEvaluationRunner(sessions, settings, lambda target: FakeAdapter([_make_event()]), notifier)
    target_id = make_target()

    assert runner.run(target_id) == "success"
    assert runner.run(target_id) == "success"
    assert notifier.calls == []
    with sessions() as session:
        notifications = list(session.scalars(select(Notification)))
        assert len(notifications) == 1
        assert notifications[0].channel == "web"
        assert notifications[0].status == "unread"
        assert notifications[0].attempt_count == 0
        assert notifications[0].error_class is None


def test_smtp_failure_does_not_remove_portal_alert(sessions, settings, make_target, tmp_path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "default.yaml").write_text(RULE_YAML, encoding="utf-8")
    settings.rules_path = str(rules_dir)
    settings.smtp_enabled = True
    settings.smtp_host = None
    settings.notification_recipients = ["ops@example.com"]
    runner = AlertEvaluationRunner(sessions, settings, lambda target: FakeAdapter([_make_event()]))

    assert runner.run(make_target()) == "success"
    with sessions() as session:
        notifications = list(session.scalars(select(Notification)))
        assert len(notifications) == 2
        portal = next(row for row in notifications if row.channel == "web")
        smtp = next(row for row in notifications if row.channel == "smtp")
        assert portal.status == "unread"
        assert smtp.status == "failed"
        assert smtp.error_class == "SMTPNotConfigured"


@pytest.mark.parametrize("with_credentials", [False, True])
def test_live_adapter_reads_credentials_after_target_detachment(
    sessions, settings, make_target, tmp_path, monkeypatch, with_credentials,
):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "default.yaml").write_text(RULE_YAML, encoding="utf-8")
    settings.rules_path = str(rules_dir)
    settings.synthetic_mode = False
    settings.oracle_username = "DEFAULT_MONITOR"
    target_id = make_target(alias="LIVE_TEST")
    if with_credentials:
        monkeypatch.setenv("SENTINEL_TEST_ORACLE_PASSWORD", "fixture-only")
        with sessions.begin() as session:
            session.add(TargetCredential(
                target_id=target_id, username="TARGET_MONITOR",
                secret_reference="env:SENTINEL_TEST_ORACLE_PASSWORD",
            ))

    adapter_settings = []

    def fake_oracle_adapter(config):
        adapter_settings.append(config)
        return FakeAdapter([_make_event()])

    monkeypatch.setattr("sentinel.collectors.adapters.OracleAdapter", fake_oracle_adapter)
    runner = AlertEvaluationRunner(sessions, settings, lambda target: adapter_for(target, settings))

    assert runner.run(target_id) == "success"
    assert len(adapter_settings) == 1
    assert adapter_settings[0].oracle_username == ("TARGET_MONITOR" if with_credentials else "DEFAULT_MONITOR")
    if with_credentials:
        assert adapter_settings[0].oracle_password == "fixture-only"
    with sessions() as session:
        assert session.scalar(select(Incident).where(Incident.target_id == target_id)) is not None
