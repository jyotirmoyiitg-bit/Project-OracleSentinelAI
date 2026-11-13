"""Knowledge HTTP API, including no-LLM search and administrator reindex."""

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from sentinel.config.settings import Settings
from sentinel.persistence.session import get_session


@pytest.fixture
def settings(tmp_path: Path):
    intake = tmp_path / "intake"
    for name in ("ora-19c-manual", "Policy", "Quarantine"):
        (intake / name).mkdir(parents=True)
    (intake / "ora-19c-manual" / "runbook.md").write_text(
        "# ORA-00257\n\nThe fast recovery area is full and the archiver is waiting.\n",
        encoding="utf-8",
    )
    (intake / "Policy" / "archivelog.md").write_text(
        "# ARCHIVELOG standard\n\nProduction databases must run in ARCHIVELOG mode.\n",
        encoding="utf-8",
    )
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'sentinel.db'}",
        synthetic_mode=True,
        scheduler_enabled=False,
        knowledge_enabled=False,
        ai_enabled=False,
        ai_provider="disabled",
        knowledge_intake_path=str(intake),
    )


@pytest.fixture
def client(settings, sessions):
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)

    def request_session():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    with TestClient(app) as test_client:
        app.state.session_factory = sessions
        yield test_client


def test_search_lists_citations_without_a_language_model(client: TestClient) -> None:
    denied = client.post("/api/v1/knowledge/reindex", json={})
    assert denied.status_code == 403
    created = client.post("/api/v1/knowledge/reindex", json={}, headers={"X-Sentinel-Role": "admin"})
    assert created.status_code == 200
    assert created.json()["indexed"] == 2
    documents = client.get("/api/v1/knowledge/documents")
    assert documents.status_code == 200
    body = documents.json()
    assert body["total"] == 2
    assert {item["category"] for item in body["items"]} == {"Knowledge", "Policy"}
    assert all(item["ingestion_status"] == "indexed" for item in body["items"])
    found = client.post("/api/v1/knowledge/search", json={"query": "ORA-00257 fast recovery area"})
    assert found.status_code == 200
    payload = found.json()
    assert payload["llm_used"] is False
    assert payload["retrieval"] == "hybrid"
    assert payload["items"][0]["citation"]["relative_path"] == "ora-19c-manual/runbook.md"
    assert payload["items"][0]["citation"]["heading"] == "ORA-00257"
    assert "fast recovery area" in payload["items"][0]["excerpt"]
    escaped = client.post("/api/v1/knowledge/reindex", json={"paths": ["../.env"]}, headers={"X-Sentinel-Role": "admin"})
    assert escaped.status_code == 422


def test_acl_update_removes_a_document_from_operator_search(client: TestClient) -> None:
    client.post("/api/v1/knowledge/reindex", json={}, headers={"X-Sentinel-Role": "admin"})
    documents = client.get("/api/v1/knowledge/documents").json()["items"]
    policy = next(item for item in documents if item["relative_path"] == "Policy/archivelog.md")
    updated = client.post(
        f"/api/v1/knowledge/documents/{policy['id']}/acl",
        json={"roles": ["dba"], "principals": []},
        headers={"X-Sentinel-Role": "admin"},
    )
    assert updated.status_code == 200
    assert updated.json()["acl_roles"] == ["dba"]
    hidden = client.post("/api/v1/knowledge/search", json={"query": "ARCHIVELOG standard production"})
    assert all(item["citation"]["relative_path"] != "Policy/archivelog.md" for item in hidden.json()["items"])
    visible = client.post(
        "/api/v1/knowledge/search",
        json={"query": "ARCHIVELOG standard production"},
        headers={"X-Sentinel-Role": "dba"},
    )
    assert visible.json()["items"][0]["citation"]["relative_path"] == "Policy/archivelog.md"
    assert client.get("/api/v1/knowledge/documents").json()["total"] == 1
    assert client.get("/api/v1/knowledge/documents", headers={"X-Sentinel-Role": "dba"}).json()["total"] == 2
