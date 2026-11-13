"""OIDC stays optional. A missed identity provider does not remove local sign-in."""

import importlib
from unittest.mock import MagicMock, patch

import httpx
import pytest
from authlib.jose import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from sentinel.auth.oidc import _read


@pytest.fixture
def oidc_client(settings, sessions):
    secured = settings.model_copy(update={
        "auth_required": True,
        "auth_bootstrap_username": "ada",
        "auth_bootstrap_password": "local-pass-1",
        "auth_bootstrap_role": "operator",
        "auth_password_iterations": 1000,
        "auth_oidc_issuer": "https://idp.example.test",
        "auth_oidc_client_id": "sentinel",
        "auth_oidc_redirect_uri": "http://127.0.0.1:8000/api/v1/auth/oidc/callback",
        "auth_oidc_success_url": "http://127.0.0.1:5173/",
        "secret_key": "test-secret",
    })
    with patch("sentinel.config.settings.get_settings", return_value=secured):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(secured)
    with TestClient(app) as test_client:
        app.state.session_factory = sessions
        yield test_client, secured


def _rsa_token(nonce: str) -> tuple[dict[str, object], str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_numbers()
    jwks = {"keys": [{
        "kty": "RSA", "kid": "test", "use": "sig", "alg": "RS256",
        "n": _b64(public.n), "e": _b64(public.e),
    }]}
    token = jwt.encode({"alg": "RS256", "kid": "test"}, {
        "iss": "https://idp.example.test",
        "aud": "sentinel",
        "sub": "jsmith",
        "preferred_username": "jsmith",
        "role": "dba",
        "nonce": nonce,
        "exp": 4_102_444_800,
    }, key)
    encoded = token.decode("ascii") if isinstance(token, bytes) else str(token)
    return jwks, encoded


def _b64(value: int) -> str:
    import base64

    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_oidc_callback_issues_a_session_and_an_outage_keeps_local_sign_in(oidc_client) -> None:
    client, settings = oidc_client
    discovery = {
        "authorization_endpoint": "https://idp.example.test/auth",
        "token_endpoint": "https://idp.example.test/token",
        "jwks_uri": "https://idp.example.test/jwks",
    }
    http = MagicMock()
    http.get.return_value = _response(discovery)
    http.post.return_value = _response({"id_token": "placeholder"})
    with patch("sentinel.auth.oidc._client", return_value=http):
        started = client.get("/api/v1/auth/oidc/start", follow_redirects=False)
    assert started.status_code == 302
    assert started.headers["location"].startswith("https://idp.example.test/auth?")
    cookie = started.cookies.get("sentinel_oidc")
    assert cookie
    saved = _read(settings, cookie)
    assert saved is not None
    jwks, token = _rsa_token(str(saved["nonce"]))

    def get(url: str) -> MagicMock:
        if url.endswith("jwks"):
            return _response(jwks)
        return _response(discovery)

    http.get.side_effect = get
    http.post.return_value = _response({"id_token": token})
    client.cookies.set("sentinel_oidc", cookie)
    with patch("sentinel.auth.oidc._client", return_value=http):
        finished = client.get(
            "/api/v1/auth/oidc/callback",
            params={"code": "auth-code", "state": saved["state"]},
            follow_redirects=False,
        )
    assert finished.status_code == 302
    assert finished.headers["location"] == "http://127.0.0.1:5173/"
    token = finished.cookies["sentinel_session"]
    session = client.get("/api/v1/auth/session", headers={"cookie": f"sentinel_session={token}"})
    assert session.json()["principal"] == "jsmith"
    assert session.json()["role"] == "dba"
    assert session.json()["method"] == "oidc"
    client.post("/api/v1/auth/logout")
    http.get.side_effect = httpx.ConnectError("idp down")
    with patch("sentinel.auth.oidc._client", return_value=http):
        outage = client.get("/api/v1/auth/oidc/start", follow_redirects=False)
    assert outage.status_code == 503
    local = client.post("/api/v1/auth/login", json={"username": "ada", "password": "local-pass-1", "local": True})
    assert local.status_code == 200
    assert local.json()["method"] == "local"


def test_proposal_reads_stay_fast_for_a_small_fleet(client_factory, sessions, make_target) -> None:
    del sessions
    client = client_factory
    target_id = make_target()
    started = __import__("time").perf_counter()
    for _ in range(25):
        response = client.get("/api/v1/operations/proposals", headers={"X-Sentinel-Role": "operator", "X-Sentinel-Principal": "op"})
        assert response.status_code == 200
    assert __import__("time").perf_counter() - started < 8
    assert target_id


@pytest.fixture
def client_factory(settings, sessions):
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)
    with TestClient(app) as test_client:
        app.state.session_factory = sessions
        yield test_client


def _response(payload: dict[str, object]) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    return response
