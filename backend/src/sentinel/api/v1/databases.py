"""Registry and bounded dashboard endpoints. Reads never initiate Oracle collection."""

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

import oracledb
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from sentinel.adapters.oracle.adapter import OracleAdapter
from sentinel.adapters.oracle.connection import network_aliases
from sentinel.api.v1.dashboard import detail_response, target_response
from sentinel.api.v1.schemas import (
    ComplianceSummary,
    ConnectionTestRequest,
    ConnectionTestResponse,
    DatabaseDetailResponse,
    DatabaseTargetCreate,
    DatabaseTargetResponse,
    DatabaseTargetUpdate,
    FleetCheckItem,
    FleetCheckResponse,
    FleetHealthSummary,
    FleetResponse,
    ObservationResponse,
    TnsAliasListResponse,
    TnsAliasResponse,
    TnsRegistrationRequest,
)
from sentinel.auth.rbac import PermissionDenied, require
from sentinel.collectors.adapters import adapter_for
from sentinel.collectors.jobs import CollectionRunner
from sentinel.collectors.normalization import PROBES, naive_utc, utcnow
from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller, caller_from_headers
from sentinel.observability.audit import record_action
from sentinel.persistence.models import CollectorCheckpoint, CurrentObservation, DatabaseTarget, Incident
from sentinel.persistence.repositories import DatabaseTargetRepository, TargetCredentialRepository
from sentinel.persistence.session import get_session
from sentinel.persistence.telemetry import checkpoints_for, current_for, history
from sentinel.rules.compliance_store import current_compliance_results, sync_policy_definitions
from sentinel.rules.policy_definitions import load_policy_definitions

router = APIRouter(prefix="/api/v1/databases", tags=["databases"])
logger = logging.getLogger(__name__)
DisplayedHealth = Literal["healthy", "warning", "critical", "unknown", "offline", "disabled"]


def runtime_settings(request: Request) -> Settings:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise TypeError("Application settings are not initialized")
    return settings


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(runtime_settings)]


def find_target(session: Session, target_id: str) -> DatabaseTarget:
    try:
        tid = uuid.UUID(target_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from exc
    target = session.get(DatabaseTarget, tid)
    if target is None:
        raise HTTPException(status_code=404, detail="Database target not found")
    return target


def _critical_counts(session: Session, target_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Open critical incidents per database. A database with none is zero, not unknown."""
    if not target_ids:
        return {}
    rows = session.execute(
        select(Incident.target_id, func.count())
        .where(Incident.target_id.in_(target_ids), Incident.state != "resolved", Incident.severity == "critical")
        .group_by(Incident.target_id),
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def fleet_rows(
    session: Session, settings: Settings, environment: str | None = None, enabled_only: bool = False,
) -> tuple[list[DatabaseTargetResponse], list[str]]:
    """Every registered database with the same health the fleet table shows."""
    now = utcnow()
    filters: list[ColumnElement[bool]] = []
    if enabled_only:
        filters.append(DatabaseTarget.enabled.is_(True))
    if environment:
        filters.append(DatabaseTarget.environment == environment)
    targets = list(session.scalars(
        select(DatabaseTarget).where(*filters).order_by(DatabaseTarget.display_name, DatabaseTarget.id),
    ))
    ids = [target.id for target in targets]
    points, current = checkpoints_for(session, ids), current_for(session, ids)
    items = [
        target_response(
            target, [point for point in points if point.target_id == target.id],
            [obs for obs in current if obs.target_id == target.id], settings, now,
        )
        for target in targets
    ]
    critical = _critical_counts(session, ids)
    for item in items:
        item.critical_incident_count = critical.get(item.id, 0)
    environments = list(session.scalars(select(DatabaseTarget.environment).distinct().order_by(DatabaseTarget.environment)))
    return items, environments


def _caller(request: Request) -> Caller:
    existing = getattr(request.state, "caller", None)
    if isinstance(existing, Caller):
        return existing
    try:
        return caller_from_headers(request.headers.get("X-Sentinel-Role"), request.headers.get("X-Sentinel-Principal"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _allow_registry_change(request: Request) -> Caller:
    """Registering, changing, removing, or test-connecting a database needs database.manage."""
    caller = _caller(request)
    try:
        require(caller, "database.manage")
    except PermissionDenied as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return caller


@router.get("", response_model=FleetResponse)
def list_databases(
    session: SessionDep, settings: SettingsDep,
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    environment: str | None = None, health_status: DisplayedHealth | None = None, enabled_only: bool = False,
) -> FleetResponse:
    items, environments = fleet_rows(session, settings, environment, enabled_only)
    if health_status:
        items = [item for item in items if item.monitoring_status == health_status]
    total = len(items)
    start = (page - 1) * page_size
    return FleetResponse(
        items=items[start:start + page_size], total=total, page=page, page_size=page_size, environments=environments,
    )


@router.post("", response_model=DatabaseTargetResponse, status_code=201)
def register_database(
    body: DatabaseTargetCreate, request: Request, session: SessionDep, settings: SettingsDep,
) -> DatabaseTarget:
    caller = _allow_registry_change(request)
    if body.secret_reference and not body.secret_reference.startswith("env:"):
        raise HTTPException(422, "Only env:VARIABLE secret references are supported")
    alias = (body.tns_alias or "").strip()
    if not alias:
        raise HTTPException(422, "Select a TNS alias. The monitoring account must connect before the database is registered.")
    result = check_portal_connection(alias, settings)
    if result.status != "connected":
        raise HTTPException(422, "The monitoring account could not connect, so the database was not registered.")
    target = DatabaseTargetRepository.create(session=session, **body.model_dump(exclude={"secret_reference"}))
    if body.secret_reference:
        TargetCredentialRepository.save_credential(session, target.id, body.secret_reference)
    target.last_connection_at = utcnow()
    record_action(session, caller.principal, "database.register", str(target.id), "created", {"tns_alias": alias})
    session.commit()
    return target


@router.get("/summary", response_model=FleetHealthSummary)
def fleet_health_summary(session: SessionDep, settings: SettingsDep) -> FleetHealthSummary:
    items, _environments = fleet_rows(session, settings)
    counts = {name: 0 for name in ("healthy", "warning", "critical", "offline", "unknown", "disabled")}
    for item in items:
        status = item.monitoring_status if item.monitoring_status in counts else "unknown"
        counts[status] += 1
    return FleetHealthSummary.model_validate({
        **counts, "total": len(items), "scheduler_enabled": settings.scheduler_enabled,
    })


def _collection_runner(request: Request, settings: Settings) -> CollectionRunner:
    scheduler = getattr(request.app.state, "collector_scheduler", None)
    existing = getattr(scheduler, "runner", None) if scheduler is not None else None
    if isinstance(existing, CollectionRunner):
        return existing
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Fleet status cannot be checked until the API is ready.")
    return CollectionRunner(factory, settings, lambda target: adapter_for(target, settings))


@router.post("/check-fleet", response_model=FleetCheckResponse)
def check_fleet(request: Request, session: SessionDep, settings: SettingsDep) -> FleetCheckResponse:
    """Recollect every enabled database, then return the same status the fleet table shows."""
    caller = _allow_registry_change(request)
    enabled_ids = list(session.scalars(
        select(DatabaseTarget.id).where(DatabaseTarget.enabled.is_(True)).order_by(
            DatabaseTarget.display_name, DatabaseTarget.id,
        ),
    ))
    session.commit()
    runner = _collection_runner(request, settings)
    for target_id in enabled_ids:
        for probe in PROBES:
            runner.run(target_id, probe)
    session.expire_all()
    items, _environments = fleet_rows(session, settings)
    record_action(session, caller.principal, "database.check_fleet", "fleet", "checked", {"checked": len(enabled_ids)})
    session.commit()
    return FleetCheckResponse(
        checked=len(enabled_ids),
        items=[
            FleetCheckItem(id=item.id, display_name=item.display_name, monitoring_status=item.monitoring_status)
            for item in items
        ],
    )


def configured_aliases(settings: Settings) -> list[str]:
    try:
        return network_aliases(settings)
    except ValueError as exc:
        logger.warning("TNS alias listing unavailable: %s", exc)
        raise HTTPException(503, str(exc)) from exc
    except (OSError, oracledb.Error) as exc:
        logger.error("TNS alias listing failed: error_class=%s", type(exc).__name__)
        raise HTTPException(503, "Could not read backend TNS aliases; check TNS_ADMIN and tnsnames.ora") from exc


@router.get("/tns-aliases", response_model=TnsAliasListResponse)
def list_tns_aliases(session: SessionDep, settings: SettingsDep) -> TnsAliasListResponse:
    registered = {alias.casefold() for alias in session.scalars(select(DatabaseTarget.tns_alias)) if alias}
    return TnsAliasListResponse(
        items=[TnsAliasResponse(name=alias, registered=alias.casefold() in registered)
               for alias in configured_aliases(settings)],
        scheduler_enabled=settings.scheduler_enabled,
    )


def canonical_alias(alias: str, settings: Settings) -> str:
    match = next((name for name in configured_aliases(settings) if name.casefold() == alias.casefold()), None)
    if match is None:
        raise HTTPException(422, "Select a configured alias from the backend tnsnames.ora")
    return match


def check_portal_connection(alias: str, settings: Settings) -> ConnectionTestResponse:
    target = DatabaseTarget(engine="oracle", tns_alias=alias)
    try:
        adapter = adapter_for(target, settings)
        result = asyncio.run(
            adapter.connection_manager.test_connection(alias) if isinstance(adapter, OracleAdapter)
            else adapter.test_connection(alias),
        )
    except (ValueError, OSError, oracledb.Error) as exc:
        logger.warning("Portal connection test failed: alias=%s error_class=%s", alias, type(exc).__name__)
        raise HTTPException(422, "Connection test failed; check backend credentials, listener and read-only grants") from exc
    if result["status"] != "connected":
        logger.warning("Portal connection test did not connect: alias=%s", alias)
        return ConnectionTestResponse(
            status="error", message="Connection failed; check backend credentials, listener and read-only grants",
        )
    return ConnectionTestResponse(
        status="connected", message="Connection successful", version=result.get("version"), banner=result.get("banner"),
    )


@router.post("/test-tns-connection", response_model=ConnectionTestResponse)
def test_tns_connection(
    body: TnsRegistrationRequest, request: Request, session: SessionDep, settings: SettingsDep,
) -> ConnectionTestResponse:
    caller = _allow_registry_change(request)
    alias = canonical_alias(body.tns_alias, settings)
    result = check_portal_connection(alias, settings)
    record_action(session, caller.principal, "database.test_connection", alias[:80], result.status, {"tns_alias": alias})
    session.commit()
    return result


@router.post("/register-from-tns", response_model=DatabaseTargetResponse, status_code=201)
def register_from_tns(
    body: TnsRegistrationRequest, request: Request, session: SessionDep, settings: SettingsDep,
) -> DatabaseTarget:
    caller = _allow_registry_change(request)
    alias = canonical_alias(body.tns_alias, settings)
    existing = session.scalar(select(DatabaseTarget.id).where(func.upper(DatabaseTarget.tns_alias) == alias.upper()))
    if existing is not None:
        raise HTTPException(409, "This TNS alias is already registered; select it in the database list")
    result = check_portal_connection(alias, settings)
    if result.status != "connected":
        raise HTTPException(422, result.message)
    target = DatabaseTargetRepository.create(
        session, engine="oracle", display_name=body.display_name, tns_alias=alias,
        environment=body.environment, owner=body.owner,
    )
    target.last_connection_at = utcnow()
    record_action(session, caller.principal, "database.register", str(target.id), "created", {"tns_alias": alias})
    session.commit()
    return target


@router.get("/{target_id}", response_model=DatabaseDetailResponse)
def get_database(target_id: str, session: SessionDep, settings: SettingsDep) -> DatabaseDetailResponse:
    target = find_target(session, target_id)
    definitions = load_policy_definitions()
    sync_policy_definitions(session, definitions)
    results = current_compliance_results(session, target.id)
    current_results = [results.get((item.policy_id, item.version)) for item in definitions]
    counts: dict[str, int] = {
        "pass_count": sum(result is not None and result.result == "pass" for result in current_results),
        "fail": sum(result is not None and result.result == "fail" for result in current_results),
        "unknown": sum(result is None or result.result == "unknown" for result in current_results),
        "not_applicable": sum(
            result is not None and result.result == "not_applicable" for result in current_results
        ),
        "error": sum(result is not None and result.result == "error" for result in current_results),
    }
    response = detail_response(
        target, checkpoints_for(session, [target.id]), current_for(session, [target.id]), settings, utcnow(),
    )
    response.compliance = ComplianceSummary(**counts)
    response.critical_incident_count = _critical_counts(session, [target.id]).get(target.id, 0)
    return response


@router.put("/{target_id}", response_model=DatabaseTargetResponse)
def update_database(
    target_id: str, body: DatabaseTargetUpdate, request: Request, session: SessionDep,
) -> DatabaseTarget:
    caller = _allow_registry_change(request)
    target = find_target(session, target_id)
    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(target, key, value)
    if updates:
        # A changed target must be re-measured before a cached health state can be trusted.
        target.last_health_status = "unknown"
        target.health_valid_until = None
        target.configuration_version += 1
        session.execute(delete(CurrentObservation).where(CurrentObservation.target_id == target.id))
        session.execute(update(CollectorCheckpoint).where(CollectorCheckpoint.target_id == target.id).values(
            last_success_at=None, status="pending", error_class=None,
        ))
        record_action(
            session, caller.principal, "database.update", str(target.id), "updated",
            {"fields": sorted(updates)},
        )
    session.commit()
    return target


@router.delete("/{target_id}", status_code=204)
def delete_database(target_id: str, request: Request, session: SessionDep) -> None:
    """Remove a database from the registry. This does not change Oracle."""
    caller = _allow_registry_change(request)
    target = find_target(session, target_id)
    record_action(
        session, caller.principal, "database.remove", str(target.id), "removed",
        {"display_name": target.display_name, "tns_alias": target.tns_alias or ""},
    )
    session.delete(target)
    session.commit()


@router.post("/{target_id}/test-connection", response_model=ConnectionTestResponse)
def test_connection(
    target_id: str, request: Request, session: SessionDep, settings: SettingsDep,
    body: ConnectionTestRequest | None = None,
) -> ConnectionTestResponse:
    caller = _allow_registry_change(request)
    target = find_target(session, target_id)
    alias = (body.tns_alias if body else None) or target.tns_alias
    if not alias:
        raise HTTPException(422, "No TNS alias configured")
    adapter = adapter_for(target, settings)
    if isinstance(adapter, OracleAdapter):
        result = asyncio.run(adapter.connection_manager.test_connection(
            alias, body.username if body else None, body.password if body else None,
        ))
    else:
        result = asyncio.run(adapter.test_connection(alias))
    caps = None
    if result["status"] == "connected":
        caps = asyncio.run(adapter.detect_capabilities(alias)).to_dict()
        target.capability_snapshot = caps
        target.last_connection_at = utcnow()
    record_action(
        session, caller.principal, "database.test_connection", str(target.id), str(result["status"])[:20],
        {"tns_alias": alias},
    )
    session.commit()
    # This manual check is not a full collection run and cannot mark the target healthy.
    return ConnectionTestResponse(
        status=result["status"], message=result["message"], version=result.get("version"),
        banner=result.get("banner"), capabilities=caps,
    )


def time_window(start: datetime | None, end: datetime | None, settings: Settings) -> tuple[datetime, datetime]:
    stop = naive_utc(end) if end else utcnow()
    begin = naive_utc(start) if start else stop - timedelta(hours=24)
    if begin > stop or (stop - begin) > timedelta(days=settings.observation_retention_days):
        raise HTTPException(422, f"Time range must be ordered and no longer than {settings.observation_retention_days} days")
    return begin, stop


@router.get("/{target_id}/metrics", response_model=list[ObservationResponse])
def get_metrics(
    target_id: str, session: SessionDep, settings: SettingsDep,
    start: datetime | None = None, end: datetime | None = None,
    metric_name: str | None = Query(None, max_length=255), limit: int = Query(1000, ge=1, le=5000),
    label: Annotated[list[str] | None, Query(description="Series filter written key=value, such as tablespace_name=USERS")] = None,
) -> list[ObservationResponse]:
    target = find_target(session, target_id)
    begin, stop = time_window(start, end, settings)
    return [ObservationResponse.model_validate(row) for row in history(
        session, target.id, begin, stop, metric_name, None, limit, labels=series_labels(label),
    )]


_LABEL_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_LABEL_FILTERS = 4


def series_labels(values: list[str] | None) -> dict[str, str]:
    """Read key=value series filters; the keys are label names and the values are compared, never run."""
    labels: dict[str, str] = {}
    for item in values or []:
        key, sep, value = item.partition("=")
        if not sep or not _LABEL_KEY.match(key) or not value or len(value) > 255:
            raise HTTPException(422, "A label filter is written key=value, with a lower-case key")
        labels[key] = value
    if len(labels) > _MAX_LABEL_FILTERS:
        raise HTTPException(422, f"At most {_MAX_LABEL_FILTERS} label filters are allowed")
    return labels


@router.get("/{target_id}/observations", response_model=list[ObservationResponse])
def get_observations(
    target_id: str, session: SessionDep, settings: SettingsDep,
    start: datetime | None = None, end: datetime | None = None,
    metric_name: str | None = Query(None, max_length=255),
    quality: Annotated[str | None, Query(pattern="^(measured|estimated|stale)$")] = None,
    limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0, le=100000),
) -> list[ObservationResponse]:
    target = find_target(session, target_id)
    begin, stop = time_window(start, end, settings)
    return [ObservationResponse.model_validate(row) for row in history(
        session, target.id, begin, stop, metric_name, quality, limit, offset,
    )]
