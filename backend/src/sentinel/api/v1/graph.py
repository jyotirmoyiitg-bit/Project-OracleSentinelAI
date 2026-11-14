"""GraphDB. Every signed-in role may look. Memory, storage, and activity use fixed readings.

The shell does not query Oracle. The three picture endpoints open one Oracle session per
request and run every reading on it. A non-administrator receives the disk-storage summary
without file paths, redo members, or segment names.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sentinel.adapters.oracle.activity import Reader, shared_reader
from sentinel.api.v1.databases import find_target, runtime_settings
from sentinel.auth.rbac import PermissionDenied, has_permission, require
from sentinel.config.settings import Settings
from sentinel.graph.activity import ActivityPicture, load_activity
from sentinel.graph.memory import MemoryPicture, load_memory
from sentinel.graph.storage import StoragePicture, load_storage, summarize
from sentinel.knowledge.acl import Caller, caller_from_headers
from sentinel.persistence.session import get_session

router = APIRouter(prefix="/api/v1/graph", tags=["graph"])
database_graph = APIRouter(prefix="/api/v1/databases", tags=["graph"])

_NOT_COLLECTED = "Not collected in this phase."
_PERMISSION = "dashboard.read"
_DETAIL_PERMISSION = "platform.read"

type Loader[Picture: BaseModel] = Callable[[Settings, str, Reader | None], Awaitable[Picture]]


class GraphPanel(BaseModel):
    id: str
    title: str
    message: str


class GraphShell(BaseModel):
    panels: list[GraphPanel]
    detail: bool = True


SHELL = GraphShell(panels=[
    GraphPanel(id="memory", title="Memory structures", message=_NOT_COLLECTED),
    GraphPanel(id="physical", title="Physical structures", message=_NOT_COLLECTED),
    GraphPanel(id="logical", title="Logical structures", message=_NOT_COLLECTED),
    GraphPanel(id="management", title="Management", message=_NOT_COLLECTED),
])


def _caller(request: Request) -> Caller:
    existing = getattr(request.state, "caller", None)
    if isinstance(existing, Caller):
        return existing
    try:
        return caller_from_headers(request.headers.get("X-Sentinel-Role"), request.headers.get("X-Sentinel-Principal"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _allow(caller: Caller) -> None:
    try:
        require(caller, _PERMISSION)
    except PermissionDenied as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def sees_detail(caller: Caller) -> bool:
    """An administrator sees file paths and object names. Every other role sees the summary."""
    return has_permission(caller.role, _DETAIL_PERMISSION)


def _alias(session: Session, target_id: str) -> str:
    target = find_target(session, target_id)
    alias = (target.tns_alias or "").strip()
    if not target.enabled or not alias:
        raise HTTPException(status_code=404, detail="Database target not found")
    return alias


async def _picture[Picture: BaseModel](settings: Settings, alias: str, loader: Loader[Picture]) -> Picture:
    if settings.synthetic_mode:
        return await loader(settings, alias, None)
    async with shared_reader(settings, alias) as reader:
        return await loader(settings, alias, reader)


@router.get("", response_model=GraphShell)
def graph_shell(caller: Annotated[Caller, Depends(_caller)]) -> GraphShell:
    _allow(caller)
    return SHELL.model_copy(update={"detail": sees_detail(caller)})


@database_graph.get("/{target_id}/graph/memory", response_model=MemoryPicture)
def graph_memory(
    target_id: str,
    caller: Annotated[Caller, Depends(_caller)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(runtime_settings)],
) -> MemoryPicture:
    _allow(caller)
    alias = _alias(session, target_id)
    return asyncio.run(_picture(settings, alias, load_memory))


@database_graph.get("/{target_id}/graph/storage", response_model=StoragePicture)
def graph_storage(
    target_id: str,
    caller: Annotated[Caller, Depends(_caller)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(runtime_settings)],
) -> StoragePicture:
    _allow(caller)
    alias = _alias(session, target_id)
    picture = asyncio.run(_picture(settings, alias, load_storage))
    return picture if sees_detail(caller) else summarize(picture)


@database_graph.get("/{target_id}/graph/activity", response_model=ActivityPicture)
def graph_activity(
    target_id: str,
    caller: Annotated[Caller, Depends(_caller)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(runtime_settings)],
) -> ActivityPicture:
    _allow(caller)
    alias = _alias(session, target_id)
    return asyncio.run(_picture(settings, alias, load_activity))
