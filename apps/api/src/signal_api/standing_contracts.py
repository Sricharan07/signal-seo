"""Exact owner standing-grant ingress and visible durability contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt

from signal_api.contracts import Contract


class RecipeRangeRequest(Contract):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    minimum_inclusive: str = Field(max_length=32)
    maximum_exclusive: str = Field(max_length=32)


class StandingGrantRequestContract(Contract):
    schema_version: Literal[1] = 1
    recipe_ranges: tuple[RecipeRangeRequest, ...] = Field(min_length=1, max_length=16)
    thresholds: dict[str, float] = Field(min_length=1, max_length=16)
    weekly_volume_caps: dict[str, StrictInt] = Field(min_length=1, max_length=16)
    weekly_total_cap: StrictInt = Field(ge=1, le=1000)
    weekly_spend_cents: StrictInt = Field(ge=0, le=100000000)
    excluded_paths: tuple[str, ...] = Field(max_length=64)
    starts_at: datetime
    ends_at: datetime
    recovery_window_hours: StrictInt = Field(ge=1, le=720)


class StandingGrantResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    grant_id: UUID
    recipe_release_ids: tuple[UUID, ...]
    state: Literal["recorded"] = "recorded"


class StandingGrantRevokeRequest(Contract):
    schema_version: Literal[1] = 1


class StandingGrantRevokeResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    grant_id: UUID
    restriction_event_id: UUID
    durability: Literal["ACKNOWLEDGED", "AUTHORITY_DURABILITY_PENDING"]


class StandingGrantViewResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    state: Literal["no_grant", "active", "not_started", "revoked", "expired", "recovery_stale"]
    grant_id: UUID | None
    recipe_release_ids: tuple[UUID, ...]
    work_types: tuple[str, ...]
    thresholds: dict[str, float]
    weekly_volume_caps: dict[str, int]
    weekly_total_cap: int | None
    weekly_spend_cents: int | None
    excluded_paths: tuple[str, ...]
    starts_at: datetime | None
    ends_at: datetime | None
    recovery_window_hours: int | None
    restriction_event_id: UUID | None
    durability: Literal["ACKNOWLEDGED", "AUTHORITY_DURABILITY_PENDING"] | None
