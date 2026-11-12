"""Copilot threads and server-sent answer events."""

import json
import uuid
from collections.abc import Iterator
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.ai.gateway import build_gateway, hosted_is_configured
from sentinel.ai.orchestrator import Orchestrator, disclosure_for, public_guided_commands
from sentinel.config.secrets import SecretResolver
from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller, caller_from_headers
from sentinel.persistence.models import ChatMessage, ChatThread, DatabaseTarget, Incident

router = APIRouter(prefix="/api/v1", tags=["chat"])


class ChatThreadRequest(BaseModel):
    scope_type: Literal["fleet", "database", "incident"] = "fleet"
    scope_id: str | None = None


class ChatMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


def _settings(request: Request) -> Settings:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise HTTPException(status_code=503, detail="Application settings are not initialized")
    return settings


def _factory(request: Request) -> sessionmaker[Session]:
    factory = getattr(request.app.state, "session_factory", None)
    if not callable(getattr(factory, "begin", None)):
        raise HTTPException(status_code=503, detail="Database is not initialized")
    return cast(sessionmaker[Session], factory)


def _caller(request: Request) -> Caller:
    existing = getattr(request.state, "caller", None)
    if isinstance(existing, Caller):
        return existing
    try:
        return caller_from_headers(request.headers.get("X-Sentinel-Role"), request.headers.get("X-Sentinel-Principal"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _thread_payload(thread: ChatThread, settings: Settings, gateway_disclosure: str) -> dict[str, object]:
    return {
        "id": str(thread.id),
        "scope_type": thread.scope_type,
        "scope_id": thread.scope_id,
        "provider": thread.provider,
        "model": thread.model,
        "llm_enabled": settings.ai_enabled and settings.ai_provider != "disabled",
        "disclosure": gateway_disclosure,
        "guided_commands": public_guided_commands(),
    }


def _validate_scope(session: Session, scope_type: str, scope_id: str | None) -> str | None:
    if scope_type == "fleet":
        if scope_id:
            raise HTTPException(status_code=400, detail="A fleet chat does not take a scope id")
        return None
    try:
        identifier = uuid.UUID(scope_id or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Scope id must be a UUID") from exc
    if scope_type == "database":
        target = session.get(DatabaseTarget, identifier)
        if target is None or not target.enabled:
            raise HTTPException(status_code=404, detail="Database not found")
    else:
        incident = session.get(Incident, identifier)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
    return str(identifier)


@router.post("/chat/threads")
def create_thread(
    body: ChatThreadRequest,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    gateway = build_gateway(settings, SecretResolver(settings.secret_key))
    with _factory(request).begin() as session:
        scope_id = _validate_scope(session, body.scope_type, body.scope_id)
        thread = ChatThread(
            principal=caller.principal, role=caller.role, scope_type=body.scope_type, scope_id=scope_id,
            provider=gateway.name, model=gateway.model,
        )
        session.add(thread)
        session.flush()
        return _thread_payload(thread, settings, disclosure_for(gateway))


@router.get("/chat/threads/{thread_id}/messages")
def list_messages(
    thread_id: str,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
) -> dict[str, object]:
    del caller
    with _factory(request)() as session:
        thread = _existing(session, thread_id)
        messages = session.scalars(
            select(ChatMessage).where(ChatMessage.thread_id == thread.id).order_by(ChatMessage.created_at),
        ).all()
        return {
            "thread_id": str(thread.id),
            "items": [_message_payload(item) for item in messages],
        }


@router.post("/chat/threads/{thread_id}/messages")
async def create_message(
    thread_id: str,
    body: ChatMessageRequest,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> StreamingResponse:
    gateway = build_gateway(settings, SecretResolver(settings.secret_key))
    orchestrator = Orchestrator(settings, gateway)
    with _factory(request).begin() as session:
        thread = _existing(session, thread_id)
        answer = await orchestrator.answer(session, thread, caller, body.content)
    def events() -> Iterator[str]:
        yield _event("status", {
            "provider": answer.provider, "model": answer.model, "llm_used": answer.llm_used,
            "provider_status": answer.provider_status, "disclosure": answer.disclosure, "intent": answer.intent,
        })
        for tool in answer.tools:
            yield _event("tool", tool)
        for citation in answer.citations:
            yield _event("citation", citation)
        yield _event("message", {
            "id": answer.message_id, "role": "assistant", "content": answer.content,
            "provider": answer.provider, "model": answer.model, "llm_used": answer.llm_used,
            "provider_status": answer.provider_status, "citations": answer.citations, "tools": answer.tools,
            "guided_commands": answer.guided_commands,
        })
        yield _event("done", {"message_id": answer.message_id, "correlation_id": answer.correlation_id})
    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/admin/ai/providers")
async def provider_status(
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    if caller.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator role is required")
    gateway = build_gateway(settings, SecretResolver(settings.secret_key))
    health = await gateway.health()
    health["hosted_approved"] = settings.ai_hosted_approved
    health["hosted_secret_configured"] = hosted_is_configured(settings)
    return health


def _existing(session: Session, thread_id: str) -> ChatThread:
    try:
        identifier = uuid.UUID(thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from exc
    thread = session.get(ChatThread, identifier)
    if thread is None:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    return thread


def _message_payload(message: ChatMessage) -> dict[str, object]:
    return {
        "id": str(message.id),
        "role": message.role,
        "content": message.content,
        "citations": message.citations or [],
        "tools": message.tool_trace or [],
        "provider": message.provider,
        "model": message.model,
        "llm_used": message.llm_used,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _event(name: str, payload: dict[str, object]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"
