"""Pydantic schemas for database registry API."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, PlainSerializer, field_validator

from sentinel.collectors.profiles import validate_profile

UTCDateTime = Annotated[datetime, PlainSerializer(
    lambda value: value.replace(tzinfo=UTC).isoformat() if value.tzinfo is None else value.astimezone(UTC).isoformat(),
    return_type=str, when_used="json",
)]
HealthStatus = Literal["healthy", "warning", "critical", "unknown", "offline"]


class DatabaseTargetCreate(BaseModel):
    """Schema for creating a new database target."""

    engine: Literal["oracle"] = "oracle"
    display_name: str = Field(..., min_length=1, max_length=255, description="Human-readable name")
    tns_alias: str | None = Field(None, description="TNS alias for Oracle connections")
    environment: str = Field(default="unknown", description="Environment: production, staging, dev")
    owner: str = Field(default="", description="Business owner")
    enabled: bool = Field(default=True, description="Whether polling is enabled")
    poll_profile: dict[str, Any] = Field(default_factory=dict, description="Polling configuration")
    secret_reference: str | None = Field(None, description="Secret vault reference for credentials")

    @field_validator("poll_profile")
    @classmethod
    def check_profile(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_profile(value)


class DatabaseTargetUpdate(BaseModel):
    """Schema for updating a database target."""

    display_name: str | None = None
    tns_alias: str | None = None
    environment: str | None = None
    owner: str | None = None
    enabled: bool | None = None
    poll_profile: dict[str, Any] | None = None

    @field_validator("display_name", "environment", "owner", "enabled", "poll_profile")
    @classmethod
    def reject_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("This field cannot be null")
        return value

    @field_validator("poll_profile")
    @classmethod
    def check_profile(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_profile(value)


class ConnectionTestRequest(BaseModel):
    """Schema for testing a database connection."""

    tns_alias: str | None = None
    username: str | None = None
    password: str | None = None


class ConnectionTestResponse(BaseModel):
    """Response from a connection test."""

    status: str
    message: str
    version: str | None = None
    banner: str | None = None
    capabilities: dict[str, Any] | None = None


class DatabaseTargetResponse(BaseModel):
    """Schema for database target response."""

    id: UUID
    engine: str
    display_name: str
    tns_alias: str | None
    environment: str
    owner: str
    enabled: bool
    poll_profile: dict[str, Any]
    capability_snapshot: dict[str, Any]
    last_connection_at: UTCDateTime | None
    last_health_status: str
    created_at: UTCDateTime
    updated_at: UTCDateTime
    last_seen: UTCDateTime | None = None
    monitoring_status: str = "unknown"
    critical_incident_count: int | None = None
    trend_snapshots: list[ObservationResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ObservationResponse(BaseModel):
    """Schema for observation response."""

    id: UUID
    target_id: UUID
    metric_name: str
    timestamp: UTCDateTime
    value: float | None
    value_str: str | None
    unit: str | None
    labels: dict[str, str]
    quality: str
    source: str

    model_config = {"from_attributes": True}


class FleetResponse(BaseModel):
    items: list[DatabaseTargetResponse]
    total: int
    page: int
    page_size: int
    environments: list[str]


class FleetHealthSummary(BaseModel):
    total: int = 0
    healthy: int = 0
    warning: int = 0
    critical: int = 0
    offline: int = 0
    unknown: int = 0
    disabled: int = 0
    scheduler_enabled: bool


class TnsAliasResponse(BaseModel):
    name: str
    registered: bool


class TnsAliasListResponse(BaseModel):
    items: list[TnsAliasResponse]
    scheduler_enabled: bool


class TnsRegistrationRequest(BaseModel):
    tns_alias: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    environment: str = Field(default="dev", min_length=1, max_length=50)
    owner: str = Field(default="", max_length=255)

    @field_validator("tns_alias", "display_name", "environment", "owner", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class PollStatus(BaseModel):
    probe_type: str
    status: str
    last_success_at: UTCDateTime | None
    last_attempt_at: UTCDateTime | None
    error_class: str | None


class ComplianceSummary(BaseModel):
    pass_count: int = Field(default=0, serialization_alias="pass")
    fail: int = 0
    unknown: int = 0
    not_applicable: int = 0
    error: int = 0


class TablespaceSummary(BaseModel):
    name: str
    used_pct: float | None = None
    size_bytes: float | None = None
    autoextend: bool | None = None
    timestamp: UTCDateTime


class FraSummary(BaseModel):
    size_bytes: float | None = None
    used_bytes: float | None = None
    used_pct: float | None = None
    breakdown: list[ObservationResponse] = Field(default_factory=list)


class ArchiveSummary(BaseModel):
    log_mode: str | None = None
    destinations: list[ObservationResponse] = Field(default_factory=list)


class DatabaseDetailResponse(DatabaseTargetResponse):
    latest_observations: list[ObservationResponse]
    poll_status: list[PollStatus]
    compliance: ComplianceSummary
    tablespaces: list[TablespaceSummary]
    fra: FraSummary
    archive: ArchiveSummary


class CompliancePolicyResponse(BaseModel):
    policy_id: str
    version: str
    name: str
    engine: str
    enabled: bool
    approval_status: str
    severity: str
    applicability: dict[str, Any]
    evaluation_config: dict[str, Any]
    environment_overrides: dict[str, Any]
    recommended_action: str

    model_config = {"from_attributes": True}


PolicyResultStatus = Literal["pass", "fail", "unknown", "not_applicable", "error"]


class PolicyResultResponse(BaseModel):
    policy_id: str
    policy_version: str
    name: str
    result: PolicyResultStatus
    severity: str
    observed_at: UTCDateTime | None
    evidence: dict[str, Any]
    recommended_action: str


class DatabaseComplianceResponse(BaseModel):
    target_id: UUID
    environment: str
    pass_count: int = Field(serialization_alias="pass")
    fail: int
    unknown: int
    not_applicable: int
    error: int
    items: list[PolicyResultResponse]


class CompliancePolicyListResponse(BaseModel):
    items: list[CompliancePolicyResponse]


class ForecastHistoryPoint(BaseModel):
    timestamp: UTCDateTime
    used_bytes: float


class ForecastProjectionPoint(BaseModel):
    timestamp: UTCDateTime
    predicted_bytes: float
    lower_bytes: float
    upper_bytes: float


class CapacityForecastItem(BaseModel):
    resource: str
    status: Literal["unknown", "stable", "forecast"]
    capacity_bytes: float | None
    data_points: int
    data_span_hours: float
    growth_rate_gb_per_day: float | None
    confidence_interval_gb_per_day: tuple[float, float] | None
    days_to_exhaustion: float | None
    predicted_exhaustion_at: UTCDateTime | None
    early_warning: bool
    prediction_horizon_days: float
    history: list[ForecastHistoryPoint]
    projection: list[ForecastProjectionPoint]


class CapacityForecastResponse(BaseModel):
    target_id: UUID
    generated_at: UTCDateTime
    items: list[CapacityForecastItem]


class DiagnosticEventResponse(BaseModel):
    """Schema for diagnostic event response."""

    id: UUID
    target_id: UUID
    source: str
    source_event_key: str
    occurred_at: UTCDateTime
    severity: str
    code: str | None
    message: str
    raw_payload: dict[str, Any]

    model_config = {"from_attributes": True}


IncidentState = Literal["new", "acknowledged", "resolved"]


class IncidentEventResponse(BaseModel):
    """Audit trail entry for an incident state transition or recurrence."""

    id: UUID
    type: str
    timestamp: UTCDateTime
    actor: str
    reason: str | None
    before: dict[str, Any]
    after: dict[str, Any]

    model_config = {"from_attributes": True}


class IncidentResponse(BaseModel):
    """Schema for incident list/detail response."""

    id: UUID
    target_id: UUID
    rule_id: str
    fingerprint: str
    state: IncidentState
    severity: str
    message: str
    first_seen: UTCDateTime
    last_seen: UTCDateTime
    owner: str | None
    root_cause_candidate: str | None
    evidence: dict[str, Any]

    model_config = {"from_attributes": True}


class IncidentDetailResponse(IncidentResponse):
    events: list[IncidentEventResponse] = Field(default_factory=list)


class IncidentListResponse(BaseModel):
    items: list[IncidentResponse]
    total: int
    page: int
    page_size: int


class IncidentActionRequest(BaseModel):
    """Acknowledge/resolve request body."""

    actor: str = Field(default="operator", min_length=1, max_length=255)
    reason: str | None = Field(default=None, max_length=2000)


class NotificationResponse(BaseModel):
    """Schema for notification response."""

    id: UUID
    incident_id: UUID
    channel: str
    recipient: str
    subject: str
    attempt_count: int
    status: str
    error_class: str | None
    created_at: UTCDateTime
    sent_at: UTCDateTime | None

    model_config = {"from_attributes": True}


class PortalNotificationResponse(BaseModel):
    id: UUID
    incident_id: UUID
    target_id: UUID
    target_name: str
    severity: str
    message: str
    status: Literal["unread", "read"]
    created_at: UTCDateTime


class PortalNotificationListResponse(BaseModel):
    items: list[PortalNotificationResponse]
    total: int
    unread_count: int
    page: int
    page_size: int
