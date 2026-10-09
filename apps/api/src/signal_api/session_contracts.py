"""Versioned browser identity and tenant-selection contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import UUID4, Field, StrictInt, field_validator
from signal_core.crawl_urls import CrawlUrlRejected, normalize_crawl_url
from signal_core.site_onboarding import InvalidSiteOnboarding, normalize_public_site_origin

from signal_api.contracts import Contract


class OrganizationSummary(Contract):
    tenant_id: UUID
    name: str = Field(min_length=1, max_length=200)
    role_key: Literal["viewer", "analyst", "editor", "approver", "admin", "owner"]


class OrganizationsResponse(Contract):
    schema_version: int = 1
    organizations: tuple[OrganizationSummary, ...]


class CsrfTokenResponse(Contract):
    schema_version: int = 1
    csrf_token: str = Field(min_length=43, max_length=43)


class TenantSelectionRequest(Contract):
    tenant_id: UUID


class TenantSessionResponse(Contract):
    schema_version: int = 1
    tenant_id: UUID
    user_id: UUID
    role_key: Literal["viewer", "analyst", "editor", "approver", "admin", "owner"]
    authentication_level: Literal["primary", "mfa"]
    expires_at: datetime
    csrf_token: str = Field(min_length=43, max_length=43)


class CurrentTenantSessionResponse(Contract):
    schema_version: int = 2
    tenant_id: UUID
    user_id: UUID
    role_key: Literal["viewer", "analyst", "editor", "approver", "admin", "owner"]
    authentication_level: Literal["primary", "mfa"]
    expires_at: datetime
    session_version: StrictInt = Field(ge=1)
    active_site_id: UUID | None


class SiteSelectionRequest(Contract):
    site_id: UUID
    expected_session_version: StrictInt = Field(ge=1)


class SiteSelectionResponse(Contract):
    schema_version: int = 1
    tenant_id: UUID
    user_id: UUID
    site_id: UUID
    session_version: StrictInt = Field(ge=2)
    changed: bool


class SiteOnboardingRequest(Contract):
    idempotency_key: UUID4
    name: str = Field(min_length=1, max_length=200)
    primary_origin: str = Field(min_length=1, max_length=2048)
    timezone: str = Field(min_length=1, max_length=128)
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    expected_session_version: StrictInt = Field(ge=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _display_text(value)

    @field_validator("primary_origin")
    @classmethod
    def validate_primary_origin(cls, value: str) -> str:
        try:
            return normalize_public_site_origin(value)
        except InvalidSiteOnboarding:
            raise ValueError("Site origin is invalid.") from None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return _timezone(value)


class SiteOnboardingResponse(Contract):
    schema_version: int = 1
    tenant_id: UUID
    user_id: UUID
    site_id: UUID
    name: str = Field(min_length=1, max_length=200)
    primary_origin: str = Field(min_length=1, max_length=2048)
    timezone: str = Field(min_length=1, max_length=128)
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    state: Literal["onboarding"] = "onboarding"
    ownership_status: Literal["unverified"] = "unverified"
    session_version: StrictInt = Field(ge=2)
    replayed: bool

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _display_text(value)

    @field_validator("primary_origin")
    @classmethod
    def validate_primary_origin(cls, value: str) -> str:
        try:
            return normalize_public_site_origin(value)
        except InvalidSiteOnboarding:
            raise ValueError("Site origin is invalid.") from None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return _timezone(value)


class OriginChallengeRequest(Contract):
    schema_version: Literal[1] = 1
    idempotency_key: UUID4
    origin: str = Field(min_length=1, max_length=2048)

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return _canonical_origin(value)


class OriginChallengeResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    challenge_id: UUID
    origin: str = Field(min_length=1, max_length=2048)
    proof_method: Literal["http_well_known"] = "http_well_known"
    proof_url: str = Field(min_length=1, max_length=2200)
    proof_content: str = Field(min_length=1, max_length=128)
    issued_at: datetime
    expires_at: datetime
    replayed: bool

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return _canonical_origin(value)


class OriginVerificationRequest(Contract):
    schema_version: Literal[1] = 1
    idempotency_key: UUID4
    challenge_id: UUID4
    origin: str = Field(min_length=1, max_length=2048)

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return _canonical_origin(value)


class OriginVerificationResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    challenge_id: UUID
    origin: str = Field(min_length=1, max_length=2048)
    ownership_status: Literal["verified"] = "verified"
    proof_method: Literal["http_well_known"] = "http_well_known"
    permitted_origins: tuple[str, ...] = Field(min_length=1, max_length=1)
    verified_at: datetime
    recheck_at: datetime
    replayed: bool

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return _canonical_origin(value)

    @field_validator("permitted_origins")
    @classmethod
    def validate_permitted_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != 1:
            raise ValueError("One exact permitted origin is required.")
        return (_canonical_origin(value[0]),)


class SiteSummary(Contract):
    id: UUID
    name: str = Field(min_length=1, max_length=200)
    primary_origin: str = Field(min_length=1, max_length=2048)
    timezone: str = Field(min_length=1, max_length=128)
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    state: Literal["onboarding", "active"]
    ownership_status: Literal["unverified", "verified", "reverification_required"]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _display_text(value)

    @field_validator("primary_origin")
    @classmethod
    def validate_primary_origin(cls, value: str) -> str:
        try:
            normalized = normalize_crawl_url(value)
        except CrawlUrlRejected:
            raise ValueError("Site origin is invalid.") from None
        if value != normalized.origin:
            raise ValueError("Site origin is invalid.")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return _timezone(value)


class SitesResponse(Contract):
    schema_version: int = 1
    tenant_id: UUID
    tenant_name: str = Field(min_length=1, max_length=200)
    sites: tuple[SiteSummary, ...] = Field(max_length=100)

    @field_validator("tenant_name")
    @classmethod
    def validate_tenant_name(cls, value: str) -> str:
        return _display_text(value)

    @field_validator("sites")
    @classmethod
    def validate_unique_sites(cls, value: tuple[SiteSummary, ...]) -> tuple[SiteSummary, ...]:
        identifiers = {site.id for site in value}
        if len(identifiers) != len(value):
            raise ValueError("Site directory contains duplicate sites.")
        return value


class InvitationAcceptanceRequest(Contract):
    invitation_id: UUID4
    token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")
    display_name: str = Field(min_length=1, max_length=200)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 0x20 or ord(character) == 0x7F for character in value
        ):
            raise ValueError("Display name is invalid.")
        return value


def _display_text(value: str) -> str:
    if value != value.strip() or any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in value
    ):
        raise ValueError("Site display text is invalid.")
    return value


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Site timezone is invalid.") from None
    return value


def _canonical_origin(value: str) -> str:
    try:
        return normalize_public_site_origin(value)
    except InvalidSiteOnboarding:
        raise ValueError("Site origin is invalid.") from None


class InvitationAcceptanceResponse(Contract):
    schema_version: int = 1
    invitation_id: UUID
    tenant_id: UUID
    site_id: UUID
    user_id: UUID
    role_key: Literal["viewer", "analyst", "editor", "approver", "admin", "owner"]
    accepted_at: datetime
