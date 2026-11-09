from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from sentinel.incidents.fingerprinting import compute_fingerprint
from sentinel.incidents.lifecycle import IncidentLifecycle, InvalidTransitionError
from sentinel.persistence.models import Incident, IncidentEvent

NOW = datetime(2026, 1, 1, 0, 0, 0)


def test_open_or_update_creates_new_incident_with_opened_event(sessions, make_target):
    target_id = make_target()
    fingerprint = compute_fingerprint("rule_a", target_id, "")
    with sessions.begin() as session:
        incident, created = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="rule_a", fingerprint=fingerprint,
            severity="warning", message="tablespace full", now=NOW,
        )
        assert created is True
        assert incident.state == "new"
        incident_id = incident.id
    with sessions() as session:
        row = session.get(Incident, incident_id)
        assert row.severity == "warning"
        assert row.message == "tablespace full"
        events = session.scalars(select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)).all()
        assert len(events) == 1
        assert events[0].type == "opened"


def test_open_or_update_same_fingerprint_updates_existing_and_writes_recurrence(sessions, make_target):
    target_id = make_target()
    fingerprint = compute_fingerprint("rule_a", target_id, "")
    with sessions.begin() as session:
        incident, _ = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="rule_a", fingerprint=fingerprint,
            severity="warning", message="first", now=NOW,
        )
        incident_id = incident.id

    later = NOW + timedelta(minutes=5)
    with sessions.begin() as session:
        incident, created = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="rule_a", fingerprint=fingerprint,
            severity="warning", message="second", now=later,
        )
        assert created is False
        assert incident.id == incident_id
        assert incident.last_seen == later
        assert incident.message == "second"

    with sessions() as session:
        events = session.scalars(
            select(IncidentEvent).where(IncidentEvent.incident_id == incident_id).order_by(IncidentEvent.timestamp),
        ).all()
        assert [event.type for event in events] == ["opened", "recurrence"]
        assert session.scalar(select(Incident.id)) == incident_id


def test_open_or_update_escalates_severity_only_when_higher(sessions, make_target):
    target_id = make_target()
    fingerprint = compute_fingerprint("rule_a", target_id, "")
    with sessions.begin() as session:
        IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="rule_a", fingerprint=fingerprint,
            severity="warning", message="first", now=NOW,
        )

    with sessions.begin() as session:
        incident, _ = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="rule_a", fingerprint=fingerprint,
            severity="info", message="still going", now=NOW + timedelta(minutes=1),
        )
        assert incident.severity == "warning"

    with sessions.begin() as session:
        incident, _ = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="rule_a", fingerprint=fingerprint,
            severity="critical", message="escalated", now=NOW + timedelta(minutes=2),
        )
        assert incident.severity == "critical"


def test_acknowledge_then_resolve_valid_transitions(sessions, make_target):
    target_id = make_target()
    fingerprint = compute_fingerprint("rule_a", target_id, "")
    with sessions.begin() as session:
        incident, _ = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="rule_a", fingerprint=fingerprint,
            severity="warning", message="first", now=NOW,
        )
        incident_id = incident.id

    with sessions.begin() as session:
        incident = IncidentLifecycle.acknowledge(session, incident_id, actor="operator", now=NOW + timedelta(minutes=1))
        assert incident.state == "acknowledged"

    with sessions.begin() as session:
        incident = IncidentLifecycle.resolve(session, incident_id, actor="operator", now=NOW + timedelta(minutes=2))
        assert incident.state == "resolved"

    with sessions() as session:
        events = session.scalars(
            select(IncidentEvent).where(IncidentEvent.incident_id == incident_id).order_by(IncidentEvent.timestamp),
        ).all()
        assert [event.type for event in events] == ["opened", "acknowledged", "resolved"]


def test_new_can_resolve_directly(sessions, make_target):
    target_id = make_target()
    fingerprint = compute_fingerprint("rule_a", target_id, "")
    with sessions.begin() as session:
        incident, _ = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="rule_a", fingerprint=fingerprint,
            severity="warning", message="first", now=NOW,
        )
        incident_id = incident.id
    with sessions.begin() as session:
        incident = IncidentLifecycle.resolve(session, incident_id, actor="operator", now=NOW + timedelta(minutes=1))
        assert incident.state == "resolved"


def test_resolving_already_resolved_incident_raises_and_does_not_mutate(sessions, make_target):
    target_id = make_target()
    fingerprint = compute_fingerprint("rule_a", target_id, "")
    with sessions.begin() as session:
        incident, _ = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="rule_a", fingerprint=fingerprint,
            severity="warning", message="first", now=NOW,
        )
        incident_id = incident.id
    with sessions.begin() as session:
        IncidentLifecycle.resolve(session, incident_id, actor="operator", now=NOW + timedelta(minutes=1))

    with pytest.raises(InvalidTransitionError):
        with sessions.begin() as session:
            IncidentLifecycle.resolve(session, incident_id, actor="operator", now=NOW + timedelta(minutes=2))

    with sessions() as session:
        row = session.get(Incident, incident_id)
        assert row.state == "resolved"
        assert row.last_seen == NOW + timedelta(minutes=1)
        events = session.scalars(select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)).all()
        assert [event.type for event in events] == ["opened", "resolved"]


def test_acknowledging_already_acknowledged_incident_raises(sessions, make_target):
    target_id = make_target()
    fingerprint = compute_fingerprint("rule_a", target_id, "")
    with sessions.begin() as session:
        incident, _ = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="rule_a", fingerprint=fingerprint,
            severity="warning", message="first", now=NOW,
        )
        incident_id = incident.id
    with sessions.begin() as session:
        IncidentLifecycle.acknowledge(session, incident_id, actor="operator", now=NOW + timedelta(minutes=1))

    with pytest.raises(InvalidTransitionError):
        with sessions.begin() as session:
            IncidentLifecycle.acknowledge(session, incident_id, actor="operator", now=NOW + timedelta(minutes=2))


def test_resolved_incident_never_reopened_a_new_incident_is_created(sessions, make_target):
    target_id = make_target()
    fingerprint = compute_fingerprint("rule_a", target_id, "")
    with sessions.begin() as session:
        incident, _ = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="rule_a", fingerprint=fingerprint,
            severity="warning", message="first", now=NOW,
        )
        first_incident_id = incident.id
    with sessions.begin() as session:
        IncidentLifecycle.resolve(session, first_incident_id, actor="operator", now=NOW + timedelta(minutes=1))

    with sessions.begin() as session:
        incident, created = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="rule_a", fingerprint=fingerprint,
            severity="warning", message="recurred", now=NOW + timedelta(minutes=2),
        )
        assert created is True
        assert incident.id != first_incident_id
        assert incident.state == "new"

    with sessions() as session:
        assert session.scalar(select(Incident.id).where(Incident.fingerprint == fingerprint).limit(1)) is not None
        all_ids = set(session.scalars(select(Incident.id).where(Incident.fingerprint == fingerprint)))
        assert all_ids == {first_incident_id, incident.id}
