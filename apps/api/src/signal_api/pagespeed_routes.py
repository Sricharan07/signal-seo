"""Owner-only, read-only Core Web Vitals projection."""

from datetime import date, datetime
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import Field, model_validator
from signal_core.pagespeed_collection import PageSpeedUnavailable

from signal_api.browser_security import SESSION_COOKIE_NAME, exact_cookie
from signal_api.contracts import Contract


class Metric(Contract):
    state: Literal["available", "unavailable"]
    reason: str | None = Field(max_length=64)
    value: float | None = Field(ge=0, allow_inf_nan=False)
    unit: Literal["ms", "score"]

    @model_validator(mode="after")
    def availability(self):
        if (self.state == "available") != (self.value is not None and self.reason is None):
            raise ValueError("Metric availability differs from its value.")
        if self.state == "unavailable" and (self.value is not None or self.reason is None):
            raise ValueError("Unavailable metrics require a reason and no estimate.")
        return self


class FieldMetric(Metric):
    rating: Literal["good", "needs_improvement", "poor"] | None


class Period(Contract):
    state: Literal["available", "unavailable"]
    reason: Literal["collection_period_not_reported"] | None
    first_date: date | None
    last_date: date | None


class FieldMetrics(Contract):
    lcp: FieldMetric
    inp: FieldMetric
    cls: FieldMetric


class FieldExperience(Contract):
    source: Literal["url", "origin"]
    locator: str = Field(max_length=2048)
    percentile: Literal[75]
    collection_period: Period
    metrics: FieldMetrics
    status: Literal["good", "needs_improvement", "poor", "unavailable"]


class LabMetrics(Contract):
    lcp: Metric
    fcp: Metric
    tbt: Metric
    speed_index: Metric
    cls: Metric


class LabExperience(Contract):
    source: Literal["psi_lighthouse"]
    status: Literal["available", "unavailable"]
    reason: Literal["lighthouse_runtime_error"] | None
    run_at: datetime
    performance_score: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    metrics: LabMetrics


class PerformanceFinding(Contract):
    id: UUID
    key: str = Field(pattern=r"^performance\.cwv\.(url|origin)\.(lcp|inp|cls)\.poor$")
    title: str = Field(max_length=160)
    summary: str = Field(max_length=500)
    severity: Literal["medium"]
    resource_locator: str = Field(max_length=2048)
    source_kind: Literal["pagespeed_observation"]
    source_id: UUID


class Observation(Contract):
    evidence_id: UUID
    url: str = Field(max_length=2048)
    strategy: Literal["mobile", "desktop"]
    lighthouse_version: str = Field(pattern=r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")
    lab: LabExperience
    field_url: FieldExperience
    field_origin: FieldExperience
    fetched_at: datetime
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    findings: list[PerformanceFinding] = Field(max_length=6)


class Sample(Contract):
    sample_id: UUID
    url: str = Field(max_length=2048)
    strategy: Literal["mobile", "desktop"]
    week_start: date
    selection_source: Literal["gsc_clicks", "crawl_order"]
    observation: Observation | None
    reason: (
        Literal[
            "PSI_RESPONSE_REJECTED",
            "PSI_RATE_LIMITED",
            "PSI_PROVIDER_UNAVAILABLE",
            "PSI_CREDENTIAL_REJECTED",
            "PSI_TRANSPORT_UNAVAILABLE",
        ]
        | None
    )
    state: Literal["observed", "scheduled", "unavailable", "outcome_unknown"]


class PageSpeedResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    outcome: Literal["found"]
    samples: list[Sample] = Field(max_length=10)
    daily_request_cap: Literal[4]
    weekly_page_cap: Literal[5]
    local_lighthouse: Literal["unavailable"]


@runtime_checkable
class PageSpeedGateway(Protocol):
    async def read_pagespeed(self, *, session_token: str, site_id: UUID) -> dict: ...


def register_pagespeed_routes(application: FastAPI) -> None:
    from signal_api.main import _error

    @application.get("/v1/sites/{site_id}/performance", response_model=PageSpeedResponse)
    async def read(request: Request, site_id: UUID):
        gateway = request.app.state.browser_pagespeed
        if not isinstance(gateway, PageSpeedGateway):
            return _error(
                request,
                status=503,
                code="PSI_UNAVAILABLE",
                message="PageSpeed observations are unavailable.",
                retryable=False,
            )
        try:
            value = await gateway.read_pagespeed(
                session_token=exact_cookie(request, SESSION_COOKIE_NAME), site_id=site_id
            )
            return PageSpeedResponse(site_id=site_id, **value)
        except PageSpeedUnavailable as error:
            denied = error.code == "PSI_ACCESS_DENIED"
            return _error(
                request,
                status=403 if denied else 503,
                code="PSI_ACCESS_DENIED" if denied else "PSI_UNAVAILABLE",
                message="Only the current verified-site owner can read performance observations."
                if denied
                else "PageSpeed observations are unavailable.",
                retryable=False,
            )
        except ValueError:
            return _error(
                request,
                status=503,
                code="PSI_UNAVAILABLE",
                message="PageSpeed observations are unavailable.",
                retryable=False,
            )
