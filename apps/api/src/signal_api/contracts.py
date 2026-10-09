"""Versioned public response contracts for the read-only API foundation."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HealthResponse(Contract):
    status: str
    service: str
    version: str


class CapabilityAvailability(StrEnum):
    DISABLED = "disabled"
    INTERNAL_ONLY = "internal_only"


class Capability(Contract):
    key: str
    availability: CapabilityAvailability


class CapabilitiesResponse(Contract):
    schema_version: int
    release_status: str
    production_writes_enabled: bool
    capabilities: tuple[Capability, ...]


class ErrorDetail(Contract):
    code: str
    message: str
    retryable: bool
    correlation_id: str


class ErrorResponse(Contract):
    error: ErrorDetail
