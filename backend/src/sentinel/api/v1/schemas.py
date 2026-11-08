"""Pydantic schemas for database registry API."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class DatabaseTargetCreate(BaseModel):
    """Schema for creating a new database target."""

    engine: str = Field(default="oracle", description="Database engine type")
    display_name: str = Field(..., description="Human-readable name")
    tns_alias: str | None = Field(None, description="TNS alias for Oracle connections")
    environment: str = Field(default="unknown", description="Environment: production, staging, dev")
    owner: str = Field(default="", description="Business owner")
    enabled: bool = Field(default=True, description="Whether polling is enabled")
    poll_profile: dict[str, Any] = Field(default_factory=dict, description="Polling configuration")
    secret_reference: str | None = Field(None, description="Secret vault reference for credentials")


class DatabaseTargetUpdate(BaseModel):
    """Schema for updating a database target."""

    display_name: str | None = None
    tns_alias: str | None = None
    environment: str | None = None
    owner: str | None = None
    enabled: bool | None = None
    poll_profile: dict[str, Any] | None = None


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
    last_connection_at: datetime | None
    last_health_status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ObservationResponse(BaseModel):
    """Schema for observation response."""

    metric_name: str
    timestamp: datetime
    value: float | None
    value_str: str | None
    unit: str | None
    labels: dict[str, str]
    quality: str
    source: str

    model_config = {"from_attributes": True}


class DiagnosticEventResponse(BaseModel):
    """Schema for diagnostic event response."""

    id: UUID
    source: str
    source_event_key: str
    occurred_at: datetime
    severity: str
    code: str | None
    message: str
    raw_payload: dict[str, Any]

    model_config = {"from_attributes": True}
