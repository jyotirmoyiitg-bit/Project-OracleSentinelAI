"""Database registry API endpoints."""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from sentinel.adapters.oracle.adapter import OracleAdapter
from sentinel.config.settings import Settings, get_settings
from sentinel.persistence.repositories import (
    DatabaseTargetRepository,
    ObservationRepository,
)
from sentinel.persistence.session import get_session
from sentinel.api.v1.schemas import (
    ConnectionTestRequest,
    ConnectionTestResponse,
    DatabaseTargetCreate,
    DatabaseTargetResponse,
    DatabaseTargetUpdate,
    ObservationResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/databases", tags=["databases"])


def get_oracle_adapter(settings: Settings = Depends(get_settings)) -> OracleAdapter:
    """Get Oracle adapter instance."""
    return OracleAdapter(settings)


@router.get("", response_model=list[DatabaseTargetResponse])
def list_databases(
    enabled_only: bool = False,
    session: Session = Depends(get_session),
) -> list[Any]:
    """List all registered database targets."""
    targets = DatabaseTargetRepository.list_all(session, enabled_only=enabled_only)
    return targets


@router.post("", response_model=DatabaseTargetResponse, status_code=201)
def register_database(
    body: DatabaseTargetCreate,
    session: Session = Depends(get_session),
) -> Any:
    """Register a new database target."""
    target = DatabaseTargetRepository.create(
        session=session,
        engine=body.engine,
        display_name=body.display_name,
        tns_alias=body.tns_alias,
        environment=body.environment,
        owner=body.owner,
        enabled=body.enabled,
        poll_profile=body.poll_profile,
    )
    session.commit()
    return target


@router.get("/{target_id}", response_model=DatabaseTargetResponse)
def get_database(
    target_id: str,
    session: Session = Depends(get_session),
) -> Any:
    """Get database target details and capabilities."""
    try:
        tid = uuid.UUID(target_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    target = DatabaseTargetRepository.get_by_id(session, tid)
    if target is None:
        raise HTTPException(status_code=404, detail="Database target not found")
    return target


@router.put("/{target_id}", response_model=DatabaseTargetResponse)
def update_database(
    target_id: str,
    body: DatabaseTargetUpdate,
    session: Session = Depends(get_session),
) -> Any:
    """Update a database target."""
    try:
        tid = uuid.UUID(target_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    update_data = body.model_dump(exclude_unset=True)
    target = DatabaseTargetRepository.update(session, tid, **update_data)
    if target is None:
        raise HTTPException(status_code=404, detail="Database target not found")
    session.commit()
    return target


@router.delete("/{target_id}", status_code=204)
def delete_database(
    target_id: str,
    session: Session = Depends(get_session),
) -> None:
    """Delete a database target."""
    try:
        tid = uuid.UUID(target_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    deleted = DatabaseTargetRepository.delete(session, tid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Database target not found")
    session.commit()


@router.post("/{target_id}/test-connection", response_model=ConnectionTestResponse)
def test_connection(
    target_id: str,
    body: ConnectionTestRequest | None = None,
    session: Session = Depends(get_session),
    adapter: OracleAdapter = Depends(get_oracle_adapter),
) -> ConnectionTestResponse:
    """Test Oracle connection for a database target."""
    try:
        tid = uuid.UUID(target_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    target = DatabaseTargetRepository.get_by_id(session, tid)
    if target is None:
        raise HTTPException(status_code=404, detail="Database target not found")

    # Use TNS alias from request or target
    tns_alias = (body.tns_alias if body else None) or target.tns_alias
    if not tns_alias:
        return ConnectionTestResponse(
            status="error",
            message="No TNS alias configured",
        )

    # Test connection
    username = body.username if body else None
    password = body.password if body else None

    try:
        result = adapter.connection_manager.test_connection(tns_alias, username, password)
        status = result.get("status", "error")
        message = result.get("message", "Unknown error")
        version = result.get("version")
        banner = result.get("banner")

        if status == "connected":
            # Also detect capabilities
            caps = adapter.detect_capabilities(tns_alias)
            caps_dict = caps.to_dict()

            # Update capability snapshot
            target.capability_snapshot = caps_dict
            target.last_health_status = "healthy"
            DatabaseTargetRepository.update_last_connection(session, tid, "healthy")
            session.commit()

            return ConnectionTestResponse(
                status="connected",
                message=message,
                version=version,
                banner=banner,
                capabilities=caps_dict,
            )
        else:
            # Update health status
            target.last_health_status = "offline"
            DatabaseTargetRepository.update_last_connection(session, tid, "offline")
            session.commit()

            return ConnectionTestResponse(
                status="error",
                message=message,
            )
    except Exception as e:
        logger.error("Connection test failed for %s: %s", target_id, e)
        target.last_health_status = "offline"
        DatabaseTargetRepository.update_last_connection(session, tid, "offline")
        session.commit()

        return ConnectionTestResponse(
            status="error",
            message=str(e),
        )


@router.get("/{target_id}/metrics", response_model=list[ObservationResponse])
def get_metrics(
    target_id: str,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> list[Any]:
    """Get latest metrics for a database target."""
    try:
        tid = uuid.UUID(target_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    target = DatabaseTargetRepository.get_by_id(session, tid)
    if target is None:
        raise HTTPException(status_code=404, detail="Database target not found")

    observations = ObservationRepository.get_latest(session, tid, limit=limit)
    return observations
