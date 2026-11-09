import uuid
from datetime import datetime

from sentinel.notifications.templates import render
from sentinel.persistence.models import Incident


def make_incident(**overrides):
    defaults = dict(
        id=uuid.uuid4(), fingerprint="fp", target_id=uuid.uuid4(), rule_id="ora_00257",
        state="new", severity="critical", message="Archive log destination full (ORA-00257)",
        first_seen=datetime(2026, 1, 1, 0, 0, 0), last_seen=datetime(2026, 1, 1, 0, 5, 0),
    )
    defaults.update(overrides)
    return Incident(**defaults)


def test_render_subject_contains_severity_label_and_target_name():
    incident = make_incident()
    subject, _ = render(incident, "PRODDB")
    assert "CRITICAL" in subject
    assert "PRODDB" in subject


def test_render_body_contains_rule_id_message_and_incident_id():
    incident = make_incident()
    _, body = render(incident, "PRODDB")
    assert incident.rule_id in body
    assert incident.message in body
    assert str(incident.id) in body
