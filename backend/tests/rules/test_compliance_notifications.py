from datetime import datetime

from sqlalchemy import func, select

from sentinel.persistence.models import DatabaseTarget, Incident, Notification
from sentinel.rules.compliance import PolicyEvaluation
from sentinel.rules.compliance_store import sync_compliance_notifications


class _FakeNotifier:
    def __init__(self, sent: bool = True, error_class: str | None = None):
        self.sent = sent
        self.error_class = error_class
        self.calls: list[tuple[str, str, str]] = []

    def send(self, recipient: str, subject: str, body: str) -> tuple[bool, str | None]:
        self.calls.append((recipient, subject, body))
        return self.sent, self.error_class


def _failing(policy_id="oracle.archivelog.enabled", version="1"):
    return PolicyEvaluation(
        policy_id=policy_id, version=version, result="fail", severity="critical",
        evidence={"database.log_mode": {"value": "NOARCHIVELOG"}}, recommended_action="Enable ARCHIVELOG mode",
    )


def _passing(policy_id="oracle.archivelog.enabled", version="1"):
    return PolicyEvaluation(
        policy_id=policy_id, version=version, result="pass", severity="critical",
        evidence={"database.log_mode": {"value": "ARCHIVELOG"}}, recommended_action="Enable ARCHIVELOG mode",
    )


def test_noncompliant_policy_opens_incident_and_portal_notification_without_raising(sessions, make_target, settings):
    target_id = make_target()
    with sessions.begin() as session:
        target = session.get(DatabaseTarget, target_id)
        sync_compliance_notifications(
            session, target, [_failing()], settings, _FakeNotifier(), datetime(2026, 1, 1),
        )
    with sessions() as session:
        incident = session.scalar(select(Incident).where(Incident.target_id == target_id))
        assert incident is not None
        assert incident.rule_id == "compliance.oracle.archivelog.enabled"
        assert incident.state == "new"
        assert incident.severity == "critical"
        notification = session.scalar(select(Notification).where(Notification.incident_id == incident.id))
        assert notification is not None
        assert notification.channel == "web"
        assert notification.status == "unread"


def test_repeated_failure_does_not_duplicate_incident_or_portal_notification(sessions, make_target, settings):
    target_id = make_target()
    with sessions.begin() as session:
        target = session.get(DatabaseTarget, target_id)
        sync_compliance_notifications(session, target, [_failing()], settings, _FakeNotifier(), datetime(2026, 1, 1))
    with sessions.begin() as session:
        target = session.get(DatabaseTarget, target_id)
        sync_compliance_notifications(session, target, [_failing()], settings, _FakeNotifier(), datetime(2026, 1, 2))
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Incident)) == 1
        assert session.scalar(select(func.count()).select_from(Notification)) == 1


def test_policy_becoming_compliant_resolves_the_open_incident(sessions, make_target, settings):
    target_id = make_target()
    with sessions.begin() as session:
        target = session.get(DatabaseTarget, target_id)
        sync_compliance_notifications(session, target, [_failing()], settings, _FakeNotifier(), datetime(2026, 1, 1))
    with sessions.begin() as session:
        target = session.get(DatabaseTarget, target_id)
        sync_compliance_notifications(session, target, [_passing()], settings, _FakeNotifier(), datetime(2026, 1, 2))
    with sessions() as session:
        incident = session.scalar(select(Incident).where(Incident.target_id == target_id))
        assert incident.state == "resolved"


def test_recurring_noncompliance_after_resolution_opens_a_fresh_incident(sessions, make_target, settings):
    target_id = make_target()
    with sessions.begin() as session:
        target = session.get(DatabaseTarget, target_id)
        sync_compliance_notifications(session, target, [_failing()], settings, _FakeNotifier(), datetime(2026, 1, 1))
    with sessions.begin() as session:
        target = session.get(DatabaseTarget, target_id)
        sync_compliance_notifications(session, target, [_passing()], settings, _FakeNotifier(), datetime(2026, 1, 2))
    with sessions.begin() as session:
        target = session.get(DatabaseTarget, target_id)
        sync_compliance_notifications(session, target, [_failing()], settings, _FakeNotifier(), datetime(2026, 1, 3))
    with sessions() as session:
        incidents = list(session.scalars(select(Incident).where(Incident.target_id == target_id)))
        assert len(incidents) == 2
        assert {incident.state for incident in incidents} == {"resolved", "new"}


def test_smtp_notification_dispatched_to_each_recipient_when_enabled(sessions, make_target, settings):
    target_id = make_target()
    settings.smtp_enabled = True
    settings.notification_recipients = ["dba@example.com", "oncall@example.com"]
    notifier = _FakeNotifier(sent=True)
    with sessions.begin() as session:
        target = session.get(DatabaseTarget, target_id)
        sync_compliance_notifications(session, target, [_failing()], settings, notifier, datetime(2026, 1, 1))
    assert len(notifier.calls) == 2
    with sessions() as session:
        smtp_notifications = list(session.scalars(select(Notification).where(Notification.channel == "smtp")))
        assert len(smtp_notifications) == 2
        assert all(record.status == "sent" for record in smtp_notifications)


def test_passing_or_unknown_policy_never_opens_an_incident(sessions, make_target, settings):
    target_id = make_target()
    unknown = PolicyEvaluation(
        policy_id="oracle.flashback.enabled", version="1", result="unknown", severity="warning",
        evidence={}, recommended_action="",
    )
    with sessions.begin() as session:
        target = session.get(DatabaseTarget, target_id)
        sync_compliance_notifications(session, target, [_passing(), unknown], settings, _FakeNotifier(), datetime(2026, 1, 1))
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Incident)) == 0
