"""Require a signed-in operator before an API call. Health and sign-in stay open."""

from collections.abc import Awaitable, Callable

from sentinel.auth.tokens import read_token
from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller

PUBLIC_PATHS = frozenset({
    "/health/live",
    "/health/ready",
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/auth/session",
})
ASGIApp = Callable[..., Awaitable[None]]


class RequireSignIn:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: dict[str, object], receive: ASGIApp, send: ASGIApp) -> None:
        if scope.get("type") != "http" or not self.settings.auth_required:
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method") or "GET")
        path = str(scope.get("path") or "")
        if method == "OPTIONS" or path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        identity = read_token(self.settings, _cookie(scope, "sentinel_session") or "")
        if identity is None:
            await _reject(send)
            return
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["caller"] = Caller(identity.role, identity.principal)
        await self.app(scope, receive, send)


def _cookie(scope: dict[str, object], name: str) -> str | None:
    headers = scope.get("headers")
    if not isinstance(headers, list):
        return None
    prefix = f"{name}="
    for item in headers:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        key, value = item
        if key != b"cookie" or not isinstance(value, bytes):
            continue
        for part in value.decode("latin-1").split(";"):
            piece = part.strip()
            if piece.startswith(prefix):
                return piece[len(prefix):]
    return None


async def _reject(send: ASGIApp) -> None:
    body = b'{"detail":"Sign in required."}'
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": body})
