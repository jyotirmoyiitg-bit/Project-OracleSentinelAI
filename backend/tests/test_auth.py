"""Sign-in is required only when configured. Directory failure can use a local account."""

import importlib
from unittest.mock import patch

from fastapi.testclient import TestClient

from sentinel.auth.directory import DirectoryResult
from sentinel.persistence.session import get_session


def _client(settings, sessions) -> TestClient:
    secured = settings.model_copy(update={
        "auth_required": True,
        "auth_bootstrap_username": "ada",
        "auth_bootstrap_password": "local-pass-1",
        "auth_bootstrap_role": "operator",
        "auth_password_iterations": 1000,
    })
    with patch("sentinel.config.settings.get_settings", return_value=secured):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(secured)

    def request_session():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    client = TestClient(app)
    client.__enter__()
    app.state.session_factory = sessions
    return client


def test_api_requires_sign_in_and_a_local_account_works_without_directory(settings, sessions) -> None:
    client = _client(settings, sessions)
    try:
        assert client.get("/api/v1/databases").status_code == 401
        rejected = client.post("/api/v1/auth/login", json={"username": "ada", "password": "wrong-password", "local": True})
        assert rejected.status_code == 401
        unsigned = client.post("/api/v1/auth/login", json={"username": "ada", "password": "local-pass-1", "local": False})
        assert unsigned.status_code == 401
        signed_in = client.post("/api/v1/auth/login", json={"username": "ada", "password": "local-pass-1", "local": True})
        assert signed_in.status_code == 200
        assert signed_in.json()["method"] == "local"
        assert signed_in.json()["role"] == "operator"
        assert client.get("/api/v1/databases").status_code == 200
        assert client.get("/api/v1/admin/ai/providers", headers={"X-Sentinel-Role": "admin"}).status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_directory_outage_uses_the_local_account_and_a_rejected_directory_password_does_not(settings, sessions) -> None:
    client = _client(settings, sessions)
    try:
        client.post("/api/v1/auth/logout")
        with patch("sentinel.auth.service.authenticate_directory", return_value=DirectoryResult("unavailable")) as directory:
            local_sign_in = client.post("/api/v1/auth/login", json={"username": "ada", "password": "local-pass-1", "local": True})
        assert local_sign_in.status_code == 200
        assert local_sign_in.json()["method"] == "local"
        directory.assert_not_called()
        client.post("/api/v1/auth/logout")
        with patch("sentinel.auth.service.authenticate_directory", return_value=DirectoryResult("invalid")):
            rejected = client.post("/api/v1/auth/login", json={"username": "ada", "password": "local-pass-1", "local": False})
        assert rejected.status_code == 401
        assert client.get("/api/v1/databases").status_code == 401
    finally:
        client.__exit__(None, None, None)


def test_directory_operator_group_signs_in_without_a_local_account(settings, sessions) -> None:
    client = _client(settings, sessions)
    try:
        client.post("/api/v1/auth/logout")
        with patch(
            "sentinel.auth.service.authenticate_directory",
            return_value=DirectoryResult("authenticated", "operator", "jsmith"),
        ):
            signed_in = client.post("/api/v1/auth/login", json={"username": "jsmith", "password": "directory-secret", "local": False})
        assert signed_in.status_code == 200
        assert signed_in.json()["principal"] == "jsmith"
        assert signed_in.json()["method"] == "ad"
        with patch(
            "sentinel.auth.service.authenticate_directory",
            return_value=DirectoryResult("not_operator"),
        ):
            client.post("/api/v1/auth/logout")
            blocked = client.post("/api/v1/auth/login", json={"username": "guest", "password": "directory-secret", "local": False})
        assert blocked.status_code == 403
    finally:
        client.__exit__(None, None, None)
