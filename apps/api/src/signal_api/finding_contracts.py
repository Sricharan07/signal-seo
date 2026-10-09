"""Versioned HTTP contracts for inspectable audit findings."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from signal_api.contracts import Contract


class LocalFixtureAnalysisRequest(Contract):
    schema_version: Literal[1]


class VerifiedHomepageAnalysisRequest(Contract):
    schema_version: Literal[1]
    idempotency_key: UUID


class FindingResponse(Contract):
    schema_version: Literal[1] = 1
    finding_id: UUID
    evidence_id: UUID
    command_id: UUID
    manifest_id: UUID
    finding_key: Literal["metadata.meta_description.missing"]
    title: Literal["Missing meta description"]
    summary: str = Field(min_length=1, max_length=500)
    resource_locator: str = Field(min_length=9, max_length=2048)
    severity: Literal["medium"]
    status: Literal["open"]
    confidence_class: Literal["deterministic"]
    source_kind: Literal["synthetic_fixture", "verified_origin"]
    source_identifier: str = Field(min_length=9, max_length=2048)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_observed_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    reused: bool

    @model_validator(mode="after")
    def validate_source_identity(self) -> Self:
        fixture = (
            self.source_kind == "synthetic_fixture"
            and self.resource_locator == "/fixture/missing-meta-description"
            and self.source_identifier == "fixture:local-pilot/missing-meta-description/v1"
        )
        verified = (
            self.source_kind == "verified_origin"
            and self.source_identifier.startswith("https://")
            and self.resource_locator == self.source_identifier
        )
        if not fixture and not verified:
            raise ValueError("Finding source identity is invalid.")
        return self


class PageObservationResponse(Contract):
    schema_version: Literal[1] = 1
    intent_id: UUID
    evidence_id: UUID
    finding_id: UUID | None
    command_id: UUID
    manifest_id: UUID
    origin: str = Field(pattern=r"^https://[^/?#\s]+$", min_length=9, max_length=2048)
    final_url: str = Field(pattern=r"^https://[^#\s]+$", min_length=9, max_length=2048)
    http_status: int = Field(ge=200, le=299)
    media_type: Literal["text/html", "application/xhtml+xml"]
    title: str | None = Field(default=None, min_length=1, max_length=300)
    heading: str | None = Field(default=None, min_length=1, max_length=500)
    meta_description: str | None = Field(default=None, min_length=1, max_length=500)
    body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    reused: bool


class VerifiedHomepageAnalysisResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    observation: PageObservationResponse
    finding: FindingResponse | None
    correlation_id: str = Field(min_length=1, max_length=64)


class LatestPageObservationResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    observation: PageObservationResponse | None
    correlation_id: str = Field(min_length=1, max_length=64)


class FindingsResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    findings: tuple[FindingResponse, ...] = Field(max_length=50)
    correlation_id: str = Field(min_length=1, max_length=64)


class LocalFixtureAnalysisResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    finding: FindingResponse
    correlation_id: str = Field(min_length=1, max_length=64)
