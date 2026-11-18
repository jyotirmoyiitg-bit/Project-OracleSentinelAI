"""Agents: snapshots, investigations, findings, and per-database agent settings."""

import uuid
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.agents.collector import CollectionRefused, collect_fleet, collect_snapshot, last_collected
from sentinel.agents.compare import changes_for
from sentinel.agents.config import OVERRIDABLE, SettingError, describe, effective, set_override
from sentinel.agents.packs import ALL_KINDS, PACK_LABELS, PACKS, is_restricted, label, parse_packs
from sentinel.agents.runner import AgentRunner, RunRefused, record_collection
from sentinel.ai.gateway import build_gateway
from sentinel.auth.rbac import PermissionDenied, require
from sentinel.collectors.normalization import utcnow
from sentinel.config.secrets import SecretResolver
from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller, caller_from_headers, is_admin_operator
from sentinel.observability.audit import record_action
from sentinel.persistence.models import (
    AgentFinding,
    AgentRun,
    AgentStep,
    DatabaseTarget,
    EvidenceSnapshot,
)

router = APIRouter(prefix="/api/v1", tags=["agents"])
AGENTS_OFF = "Agents are turned off. An administrator can turn them on in Admin, Agents."


class SnapshotRequest(BaseModel):
    packs: list[str] | None = Field(default=None, max_length=3)


class InvestigationRequest(BaseModel):
    incident_id: str | None = None
    target_id: str | None = None
    goal: str = Field(default="", max_length=600)


class DecisionRequest(BaseModel):
    decision: Literal["accepted", "dismissed"]
    note: str = Field(default="", max_length=300)


class SettingRequest(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    value: Any = None


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


def _allow(caller: Caller, permission: str) -> None:
    try:
        require(caller, permission)
    except PermissionDenied as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _uuid(value: str | None, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{what} id is not valid") from exc


def _target(session: Session, target_id: str) -> DatabaseTarget:
    target = session.get(DatabaseTarget, _uuid(target_id, "Database"))
    if target is None:
        raise HTTPException(status_code=404, detail="Database not found")
    return target


def _runner(settings: Settings, sessions: sessionmaker[Session]) -> AgentRunner:
    return AgentRunner(settings, sessions, build_gateway(settings, SecretResolver(settings.secret_key)))


def _iso(value: Any) -> str | None:
    """Stored times are UTC without an offset. The portal needs the Z to show them in local time."""
    if value is None:
        return None
    return value.isoformat() + ("Z" if value.tzinfo is None else "")


def _finding_payload(finding: AgentFinding, names: dict[uuid.UUID, str], admin: bool) -> dict[str, object]:
    hidden = finding.restricted and not admin
    return {
        "id": str(finding.id),
        "run_id": str(finding.run_id),
        "target_id": str(finding.target_id) if finding.target_id else None,
        "target_name": names.get(finding.target_id, "") if finding.target_id else "",
        "incident_id": str(finding.incident_id) if finding.incident_id else None,
        "severity": finding.severity,
        "title": finding.title,
        "summary": "This finding uses evidence that only an administrator can read." if hidden else finding.summary,
        "next_steps": [] if hidden else list(finding.next_steps or []),
        "evidence": [] if hidden else list(finding.evidence or []),
        "confidence": finding.confidence,
        "verified": finding.verified,
        "verifier_note": finding.verifier_note,
        "written_by": finding.written_by,
        "restricted": finding.restricted,
        "hidden": hidden,
        "decision": finding.decision,
        "decided_by": finding.decided_by,
        "decided_at": _iso(finding.decided_at),
        "decision_note": finding.decision_note,
        "created_at": _iso(finding.created_at),
    }


def _run_payload(run: AgentRun, names: dict[uuid.UUID, str]) -> dict[str, object]:
    return {
        "id": str(run.id),
        "agent": run.agent,
        "trigger": run.trigger,
        "scope_type": run.scope_type,
        "target_id": str(run.target_id) if run.target_id else None,
        "target_name": names.get(run.target_id, "") if run.target_id else "",
        "incident_id": str(run.incident_id) if run.incident_id else None,
        "goal": run.goal,
        "playbook": run.playbook,
        "status": run.status,
        "principal": run.principal,
        "llm_used": run.llm_used,
        "model": run.model if run.llm_used else "",
        "steps_used": run.steps_used,
        "model_calls": run.model_calls,
        "note": run.note,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
    }


def _names(session: Session) -> dict[uuid.UUID, str]:
    return {row.id: row.display_name for row in session.scalars(select(DatabaseTarget).limit(1000))}


@router.get("/agents/status")
def agent_status(
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    _allow(caller, "dashboard.read")
    gateway = build_gateway(settings, SecretResolver(settings.secret_key))
    with _factory(request)() as session:
        open_findings = session.scalar(
            select(func.count()).select_from(AgentFinding).where(AgentFinding.decision == "open"),
        ) or 0
    return {
        "enabled": settings.agents_enabled,
        "can_run": settings.agents_enabled and _has(caller, "agent.run"),
        "can_configure": _has(caller, "config.write"),
        "model_writes_findings": gateway.available and gateway.name == "local",
        "model": gateway.model if gateway.available and gateway.name == "local" else "",
        "open_findings": int(open_findings),
        "packs": [
            {"name": name, "label": PACK_LABELS[name], "readings": [label(kind) for kind in kinds]}
            for name, kinds in PACKS.items()
        ],
        "limits": {
            "max_steps": settings.agent_max_steps,
            "max_model_calls": settings.agent_max_model_calls,
            "run_timeout_seconds": settings.agent_run_timeout_seconds,
        },
    }


def _has(caller: Caller, permission: str) -> bool:
    try:
        require(caller, permission)
    except PermissionDenied:
        return False
    return True


# ---- snapshots


@router.post("/databases/{target_id}/snapshots")
async def collect_database_snapshot(
    target_id: str,
    body: SnapshotRequest,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    """Collect the database's packs now and store them. No model is called."""
    _allow(caller, "agent.run")
    sessions = _factory(request)
    if not settings.agents_enabled:
        raise HTTPException(status_code=409, detail=AGENTS_OFF)
    try:
        packs = parse_packs(body.packs) if body.packs else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if packs and "configuration" in packs and not is_admin_operator(caller.role):
        raise HTTPException(status_code=403, detail="The configuration pack is limited to an admin operator.")
    with sessions() as session:
        target = _target(session, target_id)
        target_uuid = target.id
    started = utcnow()
    try:
        batch = await collect_snapshot(settings, sessions, target_uuid, packs=packs)
    except CollectionRefused as exc:
        status = {"not_found": 404, "too_soon": 429, "busy": 409}.get(exc.code, 409)
        with sessions.begin() as session:
            record_action(session, caller.principal, "snapshot.collect", str(target_uuid), "refused", {"reason": exc.code})
        raise HTTPException(status_code=status, detail=exc.reason) from exc
    with sessions.begin() as session:
        run = record_collection(
            session, caller, [batch], [], scope_type="database", target_id=target_uuid, started_at=started,
        )
        run_id = str(run.id)
    return {
        "run_id": run_id,
        "batch_id": str(batch.batch_id),
        "taken_at": _iso(batch.taken_at),
        "answered": batch.answered,
        "readings": [
            {
                "kind": item.kind, "label": label(item.kind), "status": item.status, "row_count": item.row_count,
                "restricted": item.restricted, "note": item.note if item.status != "ok" else "",
            }
            for item in batch.items
        ],
        "left_out": [label(kind) for kind in batch.left_out],
    }


@router.get("/databases/{target_id}/snapshots")
def database_snapshots(
    target_id: str,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    """The newest stored reading of each kind, and what changed against the one before it."""
    _allow(caller, "dashboard.read")
    admin = is_admin_operator(caller.role)
    with _factory(request)() as session:
        target = _target(session, target_id)
        applied = effective(session, settings, target.id)
        last = last_collected(session, target.id)
        total = session.scalar(
            select(func.count(func.distinct(EvidenceSnapshot.batch_id))).where(EvidenceSnapshot.target_id == target.id),
        ) or 0
        stored = session.scalars(select(EvidenceSnapshot.kind).where(EvidenceSnapshot.target_id == target.id).distinct()).all()
        kinds = [kind for kind in ALL_KINDS if kind in set(stored)]
        newest: dict[str, EvidenceSnapshot] = {}
        for kind in kinds:
            row = session.scalars(
                select(EvidenceSnapshot).where(EvidenceSnapshot.target_id == target.id, EvidenceSnapshot.kind == kind)
                .order_by(EvidenceSnapshot.taken_at.desc()).limit(1),
            ).first()
            if row is not None:
                newest[kind] = row
        changes = {item.kind: item for item in changes_for(session, target.id, kinds, admin)}
        readings = []
        for kind, row in newest.items():
            change = changes.get(kind)
            hidden = is_restricted(kind) and not admin
            readings.append({
                "kind": kind,
                "label": label(kind),
                "pack": row.pack,
                "taken_at": _iso(row.taken_at),
                "status": row.status,
                "row_count": row.row_count,
                "restricted": is_restricted(kind),
                "hidden": hidden,
                "previous_at": _iso(change.previous_at) if change is not None else None,
                "changes": [] if hidden or change is None else change.lines,
            })
        return {
            "target_id": str(target.id),
            "target_name": target.display_name,
            "enabled": applied.enabled,
            "can_collect": applied.enabled and _has(caller, "agent.run"),
            "last_taken_at": _iso(last),
            "snapshot_count": int(total),
            "retention_days": applied.retention_days,
            "packs": list(applied.packs),
            "readings": readings,
            "changed": sum(1 for item in readings if item["changes"]),
        }


@router.post("/agents/snapshots/fleet", status_code=202)
async def collect_fleet_snapshot(
    request: Request,
    tasks: BackgroundTasks,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    """Collect the default packs from every enabled database in the background."""
    _allow(caller, "agent.run")
    if not settings.agents_enabled:
        raise HTTPException(status_code=409, detail=AGENTS_OFF)
    sessions = _factory(request)
    started = utcnow()

    async def work() -> None:
        batches, refused = await collect_fleet(settings, sessions)
        with sessions.begin() as session:
            record_collection(session, caller, batches, refused, scope_type="fleet", target_id=None, started_at=started)

    tasks.add_task(work)
    return {"status": "started"}


# ---- investigations


@router.post("/agents/investigations", status_code=202)
async def start_investigation(
    body: InvestigationRequest,
    request: Request,
    tasks: BackgroundTasks,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    """Start an investigation of one incident or one database. The run continues after this response."""
    _allow(caller, "agent.run")
    if not settings.agents_enabled:
        raise HTTPException(status_code=409, detail=AGENTS_OFF)
    if not body.incident_id and not body.target_id:
        raise HTTPException(status_code=422, detail="An incident or a database is required.")
    sessions = _factory(request)
    runner = _runner(settings, sessions)
    try:
        run_id = runner.start_investigation(
            caller,
            incident_id=_uuid(body.incident_id, "Incident") if body.incident_id else None,
            target_id=_uuid(body.target_id, "Database") if body.target_id else None,
            goal=body.goal,
        )
    except RunRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from exc
    tasks.add_task(runner.run, run_id)
    with sessions() as session:
        run = session.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return _run_payload(run, _names(session))


@router.get("/agents/runs")
def list_runs(
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    target_id: Annotated[str | None, Query()] = None,
    incident_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, object]:
    _allow(caller, "dashboard.read")
    with _factory(request)() as session:
        query = select(AgentRun)
        if target_id:
            query = query.where(AgentRun.target_id == _uuid(target_id, "Database"))
        if incident_id:
            query = query.where(AgentRun.incident_id == _uuid(incident_id, "Incident"))
        rows = session.scalars(query.order_by(AgentRun.created_at.desc()).limit(limit)).all()
        names = _names(session)
        return {"items": [_run_payload(row, names) for row in rows]}


@router.get("/agents/runs/{run_id}")
def get_run(run_id: str, request: Request, caller: Annotated[Caller, Depends(_caller)]) -> dict[str, object]:
    """One run with its steps and its finding. Restricted lines are returned to an admin operator only."""
    _allow(caller, "dashboard.read")
    admin = is_admin_operator(caller.role)
    with _factory(request)() as session:
        run = session.get(AgentRun, _uuid(run_id, "Run"))
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        names = _names(session)
        steps = session.scalars(select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.seq)).all()
        finding = session.scalars(
            select(AgentFinding).where(AgentFinding.run_id == run.id).order_by(AgentFinding.created_at.desc()).limit(1),
        ).first()
        payload = _run_payload(run, names)
        payload["steps"] = [
            {
                "seq": step.seq,
                "kind": step.kind,
                "title": step.title,
                "outcome": step.outcome,
                "duration_ms": step.duration_ms,
                "lines": list((step.detail or {}).get("lines") or [])
                + (list((step.detail or {}).get("restricted_lines") or []) if admin else []),
                "restricted_hidden": bool((step.detail or {}).get("restricted_lines")) and not admin,
            }
            for step in steps
        ]
        payload["finding"] = _finding_payload(finding, names, admin) if finding is not None else None
        return payload


# ---- findings


@router.get("/agents/findings")
def list_findings(
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    decision: Annotated[Literal["open", "accepted", "dismissed", "all"], Query()] = "open",
    incident_id: Annotated[str | None, Query()] = None,
    target_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, object]:
    _allow(caller, "dashboard.read")
    admin = is_admin_operator(caller.role)
    with _factory(request)() as session:
        query = select(AgentFinding)
        if decision != "all":
            query = query.where(AgentFinding.decision == decision)
        if incident_id:
            query = query.where(AgentFinding.incident_id == _uuid(incident_id, "Incident"))
        if target_id:
            query = query.where(AgentFinding.target_id == _uuid(target_id, "Database"))
        rows = session.scalars(query.order_by(AgentFinding.created_at.desc()).limit(limit)).all()
        names = _names(session)
        return {"items": [_finding_payload(row, names, admin) for row in rows]}


@router.post("/agents/findings/{finding_id}/decision")
def decide_finding(
    finding_id: str,
    body: DecisionRequest,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
) -> dict[str, object]:
    """Accept or dismiss a finding. The decision is audited and does not change a database or an incident."""
    _allow(caller, "agent.run")
    admin = is_admin_operator(caller.role)
    with _factory(request).begin() as session:
        finding = session.get(AgentFinding, _uuid(finding_id, "Finding"))
        if finding is None:
            raise HTTPException(status_code=404, detail="Finding not found")
        if finding.restricted and not admin:
            raise HTTPException(status_code=403, detail="This finding is limited to an admin operator.")
        finding.decision = body.decision
        finding.decided_by = caller.principal[:200]
        finding.decided_at = utcnow()
        finding.decision_note = body.note.strip()
        record_action(
            session, caller.principal, f"agent.finding.{'accept' if body.decision == 'accepted' else 'dismiss'}",
            str(finding.id), body.decision, {"run_id": str(finding.run_id), "written_by": finding.written_by},
        )
        session.flush()
        return _finding_payload(finding, _names(session), admin)


# ---- per-database settings


@router.get("/databases/{target_id}/agent-settings")
def database_agent_settings(
    target_id: str,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    _allow(caller, "dashboard.read")
    with _factory(request)() as session:
        target = _target(session, target_id)
        return {
            "target_id": str(target.id),
            "system_enabled": settings.agents_enabled,
            "editable": _has(caller, "config.write"),
            "items": describe(session, settings, target.id),
        }


@router.put("/databases/{target_id}/agent-settings")
def update_database_agent_setting(
    target_id: str,
    body: SettingRequest,
    request: Request,
    caller: Annotated[Caller, Depends(_caller)],
    settings: Annotated[Settings, Depends(_settings)],
) -> dict[str, object]:
    """Set one agent setting for this database, or send a null value to use the system default again."""
    _allow(caller, "config.write")
    if body.key not in OVERRIDABLE:
        raise HTTPException(status_code=422, detail="That setting cannot be set for one database.")
    with _factory(request).begin() as session:
        target = _target(session, target_id)
        try:
            stored = set_override(session, settings, target.id, body.key, body.value, caller.principal)
        except SettingError as exc:
            raise HTTPException(status_code=422, detail=exc.message) from exc
        record_action(
            session, caller.principal, "agent.settings", str(target.id),
            "cleared" if body.value is None else "changed", {"key": body.key, "value": stored},
        )
        return {
            "target_id": str(target.id),
            "system_enabled": settings.agents_enabled,
            "editable": True,
            "items": describe(session, settings, target.id),
        }

