import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.api.v1.incidents import router
from sentinel.incidents.lifecycle import IncidentLifecycle
from sentinel.persistence.models import Notification
from sentinel.persistence.session import get_session


@pytest.fixture
def client(sessions):
    app = FastAPI()
    app.include_router(router)

    def request_session():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    with TestClient(app) as client:
        yield client


def seed_notification(sessions, target_id, channel="web"):
    with sessions.begin() as session:
        incident, _ = IncidentLifecycle.open_or_update(
            session, target_id=target_id, rule_id="ora_00257", fingerprint=str(uuid.uuid4()),
            severity="critical", message="Archive destination full", evidence={},
        )
        session.flush()
        notification = Notification(
            incident_id=incident.id, channel=channel, status="unread" if channel == "web" else "sent",
            dedup_id=f"{incident.id}:{channel}",
        )
        session.add(notification)
        session.flush()
        return str(notification.id), str(incident.id)


def test_empty_portal_inbox(client):
    assert client.get("/api/v1/portal-notifications").json() == {
        "items": [], "total": 0, "unread_count": 0, "page": 1, "page_size": 10,
    }


def test_portal_count_pagination_read_persistence_and_incident_independence(client, sessions, make_target):
    target_id = make_target("PORTAL_TEST")
    nid, iid = seed_notification(sessions, target_id)
    seed_notification(sessions, target_id)
    smtp_id, _ = seed_notification(sessions, target_id, "smtp")
    response = client.get("/api/v1/portal-notifications?page_size=1").json()
    assert response["total"] == response["unread_count"] == 2
    assert len(response["items"]) == 1
    item = response["items"][0]
    assert item["target_id"] == str(target_id)
    assert item["target_name"] == "PORTAL_TEST"
    assert item["message"] == "Archive destination full"
    assert item["severity"] == "critical"
    assert item["created_at"].endswith("+00:00")
    second = client.get("/api/v1/portal-notifications?page=2&page_size=1").json()
    assert second["items"][0]["id"] != item["id"]
    for _ in range(2):
        marked = client.post(f"/api/v1/portal-notifications/{nid}/read")
        assert marked.status_code == 200
        assert marked.json()["status"] == "read"
    assert client.get("/api/v1/portal-notifications").json()["unread_count"] == 1
    assert client.get(f"/api/v1/incidents/{iid}").json()["state"] == "new"
    assert client.post(f"/api/v1/portal-notifications/{smtp_id}/read").status_code == 404
    assert len(client.get("/api/v1/notifications?channel=smtp").json()) == 1
    assert client.post(f"/api/v1/portal-notifications/{uuid.uuid4()}/read").status_code == 404
    assert client.post("/api/v1/portal-notifications/invalid/read").status_code == 400


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101"])
def test_portal_inbox_rejects_invalid_pagination(client, query):
    assert client.get(f"/api/v1/portal-notifications?{query}").status_code == 422
