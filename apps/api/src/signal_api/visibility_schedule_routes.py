"""Owner-only schedule settings and immutable assistant-call history."""

from datetime import date, datetime
from typing import Annotated, Literal, Protocol, runtime_checkable
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from pydantic import Field, StrictBool, StrictInt
from signal_core.ai_visibility_schedule import VisibilityScheduleUnavailable

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class ScheduleUpdateRequest(Contract):
    schema_version: Literal[1]
    request_id: UUID
    cadence_days: StrictInt = Field(ge=1, le=30)
    monthly_cap_micros: StrictInt = Field(ge=0, le=100_000_000)
    enabled: StrictBool


class ScheduledCall(Contract):
    operation_id: UUID
    provider: Literal["openai", "perplexity", "gemini"]
    reserved_micros: int = Field(ge=0, le=100_000_000)
    status: Literal["complete", "incomplete", "unavailable", "outcome_unknown"]
    reason: str | None = Field(max_length=128)
    observation_id: UUID | None


class ScheduledRun(Contract):
    run_id: UUID
    started_at: datetime
    question_set_id: UUID | None
    reason: str | None = Field(max_length=128)
    observed_changes: tuple[UUID, ...]
    calls: tuple[ScheduledCall, ...] = Field(max_length=75)


class ScheduleResponse(Contract):
    schema_version: Literal[1]
    site_id: UUID
    settings_id: UUID | None
    cadence_days: int = Field(ge=1, le=30)
    monthly_cap_micros: int = Field(ge=0, le=100_000_000)
    enabled: bool
    held_micros: int = Field(ge=0)
    spent_micros: None
    currency: Literal["USD"]
    month_start: date
    cap_reached: bool
    authority_current: bool
    runtime_state: Literal["available", "unavailable"]
    runs: tuple[ScheduledRun, ...] = Field(max_length=30)


@runtime_checkable
class BrowserVisibilityGateway(Protocol):
    async def read_visibility_schedule(self, *, session_token: str, site_id: UUID) -> dict: ...
    async def update_visibility_schedule(
        self, *, session_token: str, site_id: UUID, values: dict
    ) -> dict: ...


def register_visibility_schedule_routes(application: FastAPI):
    from signal_api.authentication import BrowserAuthenticationUnavailable
    from signal_api.main import _error, _strict_json_request

    def gateway(request):
        value = request.app.state.browser_visibility
        if not isinstance(value, BrowserVisibilityGateway):
            raise BrowserAuthenticationUnavailable()
        return value

    def failure(request, error):
        denied = str(error) == "owner_access_denied"
        return _error(
            request,
            status=403 if denied else 503,
            code="VISIBILITY_ACCESS_DENIED" if denied else "VISIBILITY_SCHEDULE_UNAVAILABLE",
            message="The visibility schedule is unavailable.",
            retryable=False,
        )

    def project(value, site):
        result = ScheduleResponse.model_validate(value)
        if result.site_id != site:
            raise ValueError("Schedule scope differs from the request.")
        return result

    @application.get("/v1/sites/{site_id}/ai-visibility/schedule", response_model=ScheduleResponse)
    async def read(request: Request, site_id: UUID):
        try:
            value = await gateway(request).read_visibility_schedule(
                session_token=exact_cookie(request, SESSION_COOKIE_NAME), site_id=site_id
            )
            return project(value, site_id)
        except VisibilityScheduleUnavailable as error:
            return failure(request, error)

    @application.post("/v1/sites/{site_id}/ai-visibility/schedule", response_model=ScheduleResponse)
    async def update(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        try:
            values = (
                await _strict_json_request(request, ScheduleUpdateRequest, maximum=2048)
            ).model_dump(exclude={"schema_version"})
            value = await gateway(request).update_visibility_schedule(
                session_token=proof.session_token, site_id=site_id, values=values
            )
            return project(value, site_id)
        except VisibilityScheduleUnavailable as error:
            return failure(request, error)
        except ValueError:
            return _error(
                request,
                status=422,
                code="VISIBILITY_SCHEDULE_INVALID",
                message="The visibility schedule settings are invalid.",
                retryable=False,
            )
