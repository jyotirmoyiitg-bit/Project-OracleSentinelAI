"""Versioned compliance policies and deterministic capacity forecasts."""

import uuid
from typing import Annotated, TypeGuard

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.api.v1.schemas import (
    CapacityForecastItem,
    CapacityForecastResponse,
    CompliancePolicyListResponse,
    CompliancePolicyResponse,
    DatabaseComplianceResponse,
    PolicyResultResponse,
    PolicyResultStatus,
)
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.forecasting.service import forecast_for_target
from sentinel.persistence.models import CompliancePolicyVersion, DatabaseTarget
from sentinel.persistence.session import get_session
from sentinel.rules.compliance_store import current_compliance_results, sync_policy_definitions
from sentinel.rules.policy_definitions import load_policy_definitions

router = APIRouter(tags=["compliance", "forecasting"])

SessionDep = Annotated[Session, Depends(get_session)]


def _is_policy_result(value: str) -> TypeGuard[PolicyResultStatus]:
    return value in {"pass", "fail", "unknown", "not_applicable", "error"}


def _settings(request: Request) -> Settings:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise TypeError("Application settings are not initialized")
    return settings


def _target(session: Session, target_id: str) -> DatabaseTarget:
    try:
        target_uuid = uuid.UUID(target_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from exc
    target = session.get(DatabaseTarget, target_uuid)
    if target is None:
        raise HTTPException(status_code=404, detail="Database target not found")
    return target


@router.get("/api/v1/compliance/policies", response_model=CompliancePolicyListResponse)
def list_compliance_policies(session: SessionDep) -> CompliancePolicyListResponse:
    sync_policy_definitions(session)
    session.commit()
    return CompliancePolicyListResponse(items=[
        CompliancePolicyResponse.model_validate(policy)
        for policy in session.scalars(select(CompliancePolicyVersion).order_by(
            CompliancePolicyVersion.policy_id, CompliancePolicyVersion.created_at,
        ))
    ])


@router.get("/api/v1/databases/{target_id}/compliance", response_model=DatabaseComplianceResponse)
def get_database_compliance(target_id: str, session: SessionDep) -> DatabaseComplianceResponse:
    target = _target(session, target_id)
    definitions = load_policy_definitions()
    sync_policy_definitions(session, definitions)
    session.commit()
    stored = current_compliance_results(session, target.id)
    items = []
    counts = {"pass": 0, "fail": 0, "unknown": 0, "not_applicable": 0, "error": 0}
    for policy in definitions:
        result = stored.get((policy.policy_id, policy.version))
        value = result.result if result else "unknown"
        if not _is_policy_result(value):
            raise ValueError(f"Unknown stored policy result: {value}")
        counts[value] += 1
        items.append(PolicyResultResponse(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            name=policy.name,
            result=value,
            severity=result.severity if result else policy.severity,
            observed_at=result.observed_at if result else None,
            evidence=result.evidence if result else {},
            recommended_action=result.recommended_action if result else policy.recommended_action,
        ))
    return DatabaseComplianceResponse(
        target_id=target.id,
        environment=target.environment,
        pass_count=counts["pass"],
        fail=counts["fail"],
        unknown=counts["unknown"],
        not_applicable=counts["not_applicable"],
        error=counts["error"],
        items=items,
    )


@router.get("/api/v1/databases/{target_id}/forecast", response_model=CapacityForecastResponse)
def get_database_forecast(
    target_id: str,
    session: SessionDep,
    settings: Annotated[Settings, Depends(_settings)],
) -> CapacityForecastResponse:
    target = _target(session, target_id)
    now = utcnow()
    return CapacityForecastResponse(
        target_id=target.id,
        generated_at=now,
        items=[
            CapacityForecastItem.model_validate(item)
            for item in forecast_for_target(session, target.id, settings, now)
        ],
    )
