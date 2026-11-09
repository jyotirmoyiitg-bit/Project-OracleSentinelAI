"""Registry and bounded dashboard endpoints. Reads never initiate Oracle collection."""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from sentinel.adapters.oracle.adapter import OracleAdapter
from sentinel.api.v1.dashboard import detail_response, effective_status, target_response
from sentinel.api.v1.schemas import (
    ConnectionTestRequest,
    ConnectionTestResponse,
    DatabaseDetailResponse,
    DatabaseTargetCreate,
    DatabaseTargetResponse,
    DatabaseTargetUpdate,
    FleetResponse,
    HealthStatus,
    ObservationResponse,
)
from sentinel.collectors.adapters import adapter_for
from sentinel.collectors.normalization import naive_utc, utcnow
from sentinel.config.settings import Settings
from sentinel.persistence.models import CollectorCheckpoint, CurrentObservation, DatabaseTarget
from sentinel.persistence.repositories import DatabaseTargetRepository, TargetCredentialRepository
from sentinel.persistence.session import get_session
from sentinel.persistence.telemetry import checkpoints_for, current_for, history

router = APIRouter(prefix="/api/v1/databases", tags=["databases"])


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


@router.get("", response_model=FleetResponse)
def list_databases(
    session: SessionDep, settings: SettingsDep,
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    environment: str | None = None, health_status: HealthStatus | None = None, enabled_only: bool = False,
) -> FleetResponse:
    now = utcnow()
    filters: list[ColumnElement[bool]] = []
    if enabled_only:
        filters.append(DatabaseTarget.enabled.is_(True))
    if environment:
        filters.append(DatabaseTarget.environment == environment)
    if health_status:
        filters.append(effective_status(now) == health_status)
    total = session.scalar(select(func.count()).select_from(DatabaseTarget).where(*filters)) or 0
    targets = list(session.scalars(select(DatabaseTarget).where(*filters)
                                  .order_by(DatabaseTarget.display_name, DatabaseTarget.id)
                                  .offset((page - 1) * page_size).limit(page_size)))
    ids = [target.id for target in targets]
    points, current = checkpoints_for(session, ids), current_for(session, ids)
    items = [
        target_response(target, [p for p in points if p.target_id == target.id],
                        [o for o in current if o.target_id == target.id], settings, now)
        for target in targets
    ]
    environments = list(session.scalars(select(DatabaseTarget.environment).distinct().order_by(DatabaseTarget.environment)))
    return FleetResponse(items=items, total=total, page=page, page_size=page_size, environments=environments)


@router.post("", response_model=DatabaseTargetResponse, status_code=201)
def register_database(body: DatabaseTargetCreate, session: SessionDep) -> DatabaseTarget:
    if body.secret_reference and not body.secret_reference.startswith("env:"):
        raise HTTPException(422, "Only env:VARIABLE secret references are supported")
    target = DatabaseTargetRepository.create(session=session, **body.model_dump(exclude={"secret_reference"}))
    if body.secret_reference:
        TargetCredentialRepository.save_credential(session, target.id, body.secret_reference)
    session.commit()
    return target


@router.get("/{target_id}", response_model=DatabaseDetailResponse)
def get_database(target_id: str, session: SessionDep, settings: SettingsDep) -> DatabaseDetailResponse:
    target = find_target(session, target_id)
    return detail_response(
        target, checkpoints_for(session, [target.id]), current_for(session, [target.id]), settings, utcnow(),
    )


@router.put("/{target_id}", response_model=DatabaseTargetResponse)
def update_database(
    target_id: str, body: DatabaseTargetUpdate, session: SessionDep,
) -> DatabaseTarget:
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
    session.commit()
    return target


@router.delete("/{target_id}", status_code=204)
def delete_database(target_id: str, session: SessionDep) -> None:
    session.delete(find_target(session, target_id))
    session.commit()


@router.post("/{target_id}/test-connection", response_model=ConnectionTestResponse)
def test_connection(
    target_id: str, session: SessionDep, settings: SettingsDep, body: ConnectionTestRequest | None = None,
) -> ConnectionTestResponse:
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
) -> list[ObservationResponse]:
    target = find_target(session, target_id)
    begin, stop = time_window(start, end, settings)
    return [ObservationResponse.model_validate(row) for row in history(
        session, target.id, begin, stop, metric_name, None, limit,
    )]


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
