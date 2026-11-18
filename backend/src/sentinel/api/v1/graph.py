"""GraphDB. Every signed-in role may look. Memory, storage, and activity use fixed readings.

The shell does not query Oracle. The three picture endpoints open one Oracle session per
request and run every reading on it. A non-administrator receives the disk-storage summary
without file paths, redo members, or segment names. The history endpoint returns stored
counter samples from PostgreSQL and does not query Oracle.

MemoryDB, StorageDB, ParameterDB, and AlertDB add four more pictures of one database. They are open
to every signed-in role too. A parameter value that holds a path or an address, and a file path in
the alert log, are shown to an administrator only.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sentinel.adapters.oracle.activity import Reader, shared_reader
from sentinel.api.v1.databases import find_target, runtime_settings
from sentinel.auth.rbac import PermissionDenied, has_permission, require
from sentinel.config.settings import Settings
from sentinel.graph.activity import ActivityPicture, load_activity
from sentinel.graph.history import GraphHistory, caption, read_history
from sentinel.graph.memory import MemoryPicture, load_memory
from sentinel.graph.sections import (
    AlertTail,
    ParameterList,
    SgaBreakdown,
    TablespaceUtilization,
    alert_tail_for_role,
    load_alert_tail,
    load_parameters,
    load_sga_breakdown,
    load_tablespaces,
    parameters_for_role,
)
from sentinel.graph.storage import StoragePicture, load_storage, summarize
from sentinel.graph.wait_activity import WaitActivity, load_wait_activity
from sentinel.graph.waits import WaitPicture, load_waits
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


@database_graph.get("/{target_id}/graph/history", response_model=GraphHistory)
def graph_history(
    target_id: str,
    caller: Annotated[Caller, Depends(_caller)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(runtime_settings)],
    minutes: Annotated[int, Query(ge=5, le=1440)] = 60,
) -> GraphHistory:
    """Stored counter samples for the charts and meters. Oracle is not read."""
    _allow(caller)
    _alias(session, target_id)
    samples = read_history(session, find_target(session, target_id).id, minutes)
    return GraphHistory(
        samples=samples,
        enabled=bool(settings.scheduler_enabled and settings.graph_history_enabled),
        interval_seconds=settings.graph_history_interval_seconds,
        caption=caption(settings, len(samples)),
    )


@database_graph.get("/{target_id}/graph/waits", response_model=WaitPicture)
def graph_waits(
    target_id: str,
    caller: Annotated[Caller, Depends(_caller)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(runtime_settings)],
) -> WaitPicture:
    _allow(caller)
    alias = _alias(session, target_id)
    return asyncio.run(_picture(settings, alias, load_waits))


@database_graph.get("/{target_id}/wait/activity", response_model=WaitActivity)
def wait_activity(
    target_id: str,
    caller: Annotated[Caller, Depends(_caller)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(runtime_settings)],
) -> WaitActivity:
    _allow(caller)
    alias = _alias(session, target_id)
    return asyncio.run(_picture(settings, alias, load_wait_activity))


@database_graph.get("/{target_id}/memory/sga", response_model=SgaBreakdown)
def memory_sga(
    target_id: str,
    caller: Annotated[Caller, Depends(_caller)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(runtime_settings)],
) -> SgaBreakdown:
    """MemoryDB: how the SGA is divided between its pools."""
    _allow(caller)
    alias = _alias(session, target_id)
    return asyncio.run(_picture(settings, alias, load_sga_breakdown))


@database_graph.get("/{target_id}/storage/tablespaces", response_model=TablespaceUtilization)
def storage_tablespaces(
    target_id: str,
    caller: Annotated[Caller, Depends(_caller)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(runtime_settings)],
) -> TablespaceUtilization:
    """StorageDB: size, free space, and fragments of every tablespace."""
    _allow(caller)
    alias = _alias(session, target_id)
    return asyncio.run(_picture(settings, alias, load_tablespaces))


@database_graph.get("/{target_id}/parameters", response_model=ParameterList)
def database_parameters(
    target_id: str,
    caller: Annotated[Caller, Depends(_caller)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(runtime_settings)],
) -> ParameterList:
    """ParameterDB: the initialization parameters in effect. Paths and addresses are shown to an administrator."""
    _allow(caller)
    alias = _alias(session, target_id)
    picture = asyncio.run(_picture(settings, alias, load_parameters))
    return parameters_for_role(picture, sees_detail(caller))


@database_graph.get("/{target_id}/alert-log", response_model=AlertTail)
def database_alert_log(
    target_id: str,
    caller: Annotated[Caller, Depends(_caller)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(runtime_settings)],
) -> AlertTail:
    """AlertDB: the most recent lines of the alert log. File paths are shown to an administrator."""
    _allow(caller)
    alias = _alias(session, target_id)
    picture = asyncio.run(_picture(settings, alias, load_alert_tail))
    return alert_tail_for_role(picture, sees_detail(caller))
