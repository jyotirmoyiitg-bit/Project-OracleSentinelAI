"""Authorization-code OIDC. Local sign-in stays available when the IdP is down or unset."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from sentinel.config.settings import Settings

_STATE_COOKIE = "sentinel_oidc"


class OidcError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class OidcIdentity:
    principal: str
    role: str


def enabled(settings: Settings) -> bool:
    return bool(settings.auth_oidc_issuer.strip() and settings.auth_oidc_client_id.strip())


def begin(settings: Settings, client: httpx.Client | None = None) -> tuple[str, str]:
    """Return the IdP redirect and a signed state cookie."""
    if not enabled(settings):
        raise OidcError("OIDC is not configured.", 404)
    document = _discovery(settings, client)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    nonce = secrets.token_urlsafe(24)
    state = secrets.token_urlsafe(24)
    query = urlencode({
        "response_type": "code",
        "client_id": settings.auth_oidc_client_id,
        "redirect_uri": settings.auth_oidc_redirect_uri,
        "scope": "openid profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    cookie = _sign(settings, {"state": state, "nonce": nonce, "verifier": verifier, "exp": int(time.time()) + 600})
    return f"{document['authorization_endpoint']}?{query}", cookie


def complete(
    settings: Settings,
    code: str,
    state: str,
    cookie: str,
    client: httpx.Client | None = None,
) -> OidcIdentity:
    saved = _read(settings, cookie)
    if saved is None or saved.get("state") != state or not code.strip():
        raise OidcError("The sign-in state did not match. Start the organization sign-in again.", 401)
    document = _discovery(settings, client)
    token = _token(settings, document, code, str(saved["verifier"]), client)
    identity = _identity(settings, document, token, str(saved["nonce"]), client)
    return identity


def state_cookie_name() -> str:
    return _STATE_COOKIE


def _discovery(settings: Settings, client: httpx.Client | None) -> dict[str, Any]:
    url = settings.auth_oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    payload = _get_json(url, client)
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not str(payload.get(key) or "").startswith("https://"):
            raise OidcError("The identity provider metadata is incomplete.", 503)
    return payload


def _token(settings: Settings, document: dict[str, Any], code: str, verifier: str, client: httpx.Client | None) -> dict[str, Any]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.auth_oidc_redirect_uri,
        "client_id": settings.auth_oidc_client_id,
        "code_verifier": verifier,
    }
    secret = _client_secret(settings)
    if secret:
        form["client_secret"] = secret
    response = _post_form(str(document["token_endpoint"]), form, client)
    if "id_token" not in response:
        raise OidcError("The identity provider did not return an identity token.", 503)
    return response


def _identity(
    settings: Settings,
    document: dict[str, Any],
    token: dict[str, Any],
    nonce: str,
    client: httpx.Client | None,
) -> OidcIdentity:
    jwks = _get_json(str(document["jwks_uri"]), client)
    from authlib.jose import JsonWebKey, jwt

    try:
        claims = jwt.decode(str(token["id_token"]), JsonWebKey.import_key_set(jwks))
        claims.validate()
    except Exception as exc:
        raise OidcError("The identity token could not be validated.", 401) from exc
    issuer = settings.auth_oidc_issuer.rstrip("/")
    audience = claims.get("aud")
    audience_ok = audience == settings.auth_oidc_client_id or (
        isinstance(audience, list) and settings.auth_oidc_client_id in audience
    )
    if claims.get("iss") != issuer or not audience_ok or claims.get("nonce") != nonce:
        raise OidcError("The identity token did not match this application.", 401)
    principal = str(claims.get("preferred_username") or claims.get("sub") or "").strip()
    if not principal or len(principal) > 200:
        raise OidcError("The identity token did not include a principal.", 401)
    return OidcIdentity(principal, _role(claims))


def _role(claims: Any) -> str:
    raw = claims.get("role") if hasattr(claims, "get") else ""
    if not raw:
        roles = claims.get("roles") if hasattr(claims, "get") else None
        raw = roles[0] if isinstance(roles, list) and roles else ""
    role = str(raw or "").strip().lower()
    known = {
        "viewer", "operator", "dba", "admin", "auditor", "platform_admin", "security_auditor",
    }
    return role if role in known else "viewer"


def _client_secret(settings: Settings) -> str:
    ref = settings.auth_oidc_client_secret_ref.strip()
    if not ref:
        return ""
    if not ref.startswith("secret://"):
        raise OidcError("The OIDC client secret must be a secret:// reference.", 503)
    name = ref.removeprefix("secret://").split("/")[-1]
    if not name.isidentifier():
        raise OidcError("The OIDC client secret reference is invalid.", 503)
    import os

    value = os.environ.get(name, "").strip()
    if not value:
        raise OidcError("The OIDC client secret is not available.", 503)
    return value


def _get_json(url: str, client: httpx.Client | None) -> dict[str, Any]:
    own = client is None
    http = client or _client()
    try:
        try:
            response = http.get(url)
        except httpx.HTTPError as exc:
            raise OidcError("The identity provider could not be reached. Use local sign-in.", 503) from exc
    finally:
        if own:
            http.close()
    if response.status_code >= 400:
        raise OidcError("The identity provider could not be reached. Use local sign-in.", 503)
    payload = response.json()
    if not isinstance(payload, dict):
        raise OidcError("The identity provider returned an unexpected response.", 503)
    return payload


def _post_form(url: str, form: dict[str, str], client: httpx.Client | None) -> dict[str, Any]:
    own = client is None
    http = client or _client()
    try:
        try:
            response = http.post(url, data=form)
        except httpx.HTTPError as exc:
            raise OidcError("The identity provider could not be reached. Use local sign-in.", 503) from exc
    finally:
        if own:
            http.close()
    if response.status_code >= 400:
        raise OidcError("The identity provider rejected the sign-in.", 401)
    payload = response.json()
    if not isinstance(payload, dict):
        raise OidcError("The identity provider returned an unexpected response.", 503)
    return payload


def _client() -> httpx.Client:
    return httpx.Client(timeout=10, trust_env=False, follow_redirects=False)


def _sign(settings: Settings, payload: dict[str, object]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_key(settings), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _read(settings: Settings, token: str) -> dict[str, Any] | None:
    body, separator, signature = token.partition(".")
    if not separator or not body or not signature:
        return None
    expected = hmac.new(_key(settings), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    padded = body + "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("exp") or 0) < int(time.time()):
        return None
    return payload


def _key(settings: Settings) -> bytes:
    return hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
