"""Webflow draft Inbox. No update, publish, or live-write HTTP surface."""

from typing import Annotated, Literal, Protocol, runtime_checkable
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from pydantic import Field, model_validator
from signal_core.content_writer import ContentWriterUnavailable
from signal_core.recovery_authority import RecoveryAuthorityError
from signal_core.webflow import CAPABILITIES, WebflowUnavailable

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class WebflowReview(Contract):
    schema_version: Literal[1]
    id: UUID
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "rejected", "changes_requested"]


class WebflowBegin(Contract):
    schema_version: Literal[1]
    provider_site: str = Field(pattern=r"^[0-9a-f]{24}$")
    collection_id: str = Field(pattern=r"^[0-9a-f]{24}$")


class WebflowMapping(Contract):
    title: Literal["name"]
    description: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    body: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")

    @model_validator(mode="after")
    def distinct_fields(self):
        if self.description in {"name", "slug"} or self.body in {"name", "slug", self.description}:
            raise ValueError("Distinct editable collection fields required.")
        return self


class WebflowComplete(Contract):
    schema_version: Literal[1]
    attempt_id: UUID
    state: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    code: str = Field(pattern=r"^[\x21-\x7e]{1,2048}$")
    field_mapping: WebflowMapping


class WebflowSeal(Contract):
    schema_version: Literal[1]
    binding_id: UUID
    candidate_id: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WebflowRevoke(Contract):
    schema_version: Literal[1]
    binding_id: UUID


@runtime_checkable
class BrowserWebflowOwner(Protocol):
    async def webflow_options(self, *, session_token: str, site_id: UUID) -> dict: ...
    async def mutate_webflow(
        self, *, session_token: str, site_id: UUID, operation: str, values: dict
    ) -> dict: ...


@runtime_checkable
class BrowserWebflowInbox(Protocol):
    async def read_webflow(self, *, session_token: str, site_id: UUID) -> dict: ...
    async def review_webflow(self, *, session_token: str, site_id: UUID, values: dict) -> dict: ...


def register_webflow_routes(application: FastAPI):
    from signal_api.main import _error, _strict_json_request

    def port(request):
        result = request.app.state.browser_webflow or request.app.state.browser_writer
        if not isinstance(result, BrowserWebflowInbox):
            raise WebflowUnavailable("WEBFLOW_CONNECTOR_UNCONFIGURED")
        return result

    def error(request, exception):
        denied = exception.code in {
            "WEBFLOW_OWNER_ACCESS_OR_SOURCE_UNAVAILABLE",
            "WEBFLOW_STEP_UP_REQUIRED",
        }
        return _error(
            request,
            status=403 if denied else 503,
            code=exception.code,
            message="The Webflow request could not be completed.",
            retryable=False,
        )

    def owner_port(request):
        result = request.app.state.browser_webflow
        if not isinstance(result, BrowserWebflowOwner):
            raise WebflowUnavailable("WEBFLOW_CONNECTOR_UNCONFIGURED")
        return result

    @application.get("/v1/sites/{site_id}/webflow/options")
    async def options(request: Request, site_id: UUID):
        try:
            return {
                "schema_version": 1,
                **await owner_port(request).webflow_options(
                    session_token=exact_cookie(request, SESSION_COOKIE_NAME), site_id=site_id
                ),
            }
        except WebflowUnavailable as exception:
            return error(request, exception)
        except Exception:
            return error(request, WebflowUnavailable("WEBFLOW_CONNECTOR_UNAVAILABLE"))

    def register_command(operation, contract):
        async def command(
            request: Request,
            site_id: UUID,
            proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
        ):
            try:
                values = (await _strict_json_request(request, contract, maximum=4096)).model_dump(
                    mode="json", exclude={"schema_version"}
                )
                return {
                    "schema_version": 1,
                    **await owner_port(request).mutate_webflow(
                        session_token=proof.session_token,
                        site_id=site_id,
                        operation=operation,
                        values=values,
                    ),
                }
            except WebflowUnavailable as exception:
                return error(request, exception)
            except ValueError:
                return _error(
                    request,
                    status=422,
                    code="WEBFLOW_REQUEST_INVALID",
                    message="The Webflow request is invalid.",
                    retryable=False,
                )
            except (ContentWriterUnavailable, RecoveryAuthorityError):
                return error(request, WebflowUnavailable("WEBFLOW_DATABASE_UNCONFIGURED"))
            except Exception:
                return error(request, WebflowUnavailable("WEBFLOW_CONNECTOR_UNAVAILABLE"))

        application.add_api_route(
            f"/v1/sites/{{site_id}}/webflow/{operation}", command, methods=["POST"]
        )

    for operation, contract in (
        ("begin", WebflowBegin),
        ("complete", WebflowComplete),
        ("seal", WebflowSeal),
        ("revoke", WebflowRevoke),
    ):
        register_command(operation, contract)

    @application.get("/v1/sites/{site_id}/webflow")
    async def read(request: Request, site_id: UUID):
        try:
            return {
                "schema_version": 1,
                **await port(request).read_webflow(
                    session_token=exact_cookie(request, SESSION_COOKIE_NAME), site_id=site_id
                ),
                "capabilities": dict(CAPABILITIES),
            }
        except (ContentWriterUnavailable, RecoveryAuthorityError):
            return error(request, WebflowUnavailable("WEBFLOW_DATABASE_UNCONFIGURED"))
        except WebflowUnavailable as exception:
            return error(request, exception)

    @application.post("/v1/sites/{site_id}/webflow/review")
    async def review(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        try:
            values = (await _strict_json_request(request, WebflowReview, maximum=4096)).model_dump(
                mode="json", exclude={"schema_version"}
            )
            return {
                "schema_version": 1,
                **await port(request).review_webflow(
                    session_token=proof.session_token, site_id=site_id, values=values
                ),
            }
        except (ContentWriterUnavailable, RecoveryAuthorityError):
            return error(request, WebflowUnavailable("WEBFLOW_DATABASE_UNCONFIGURED"))
        except WebflowUnavailable as exception:
            return error(request, exception)
        except ValueError:
            return _error(
                request,
                status=422,
                code="WEBFLOW_REQUEST_INVALID",
                message="The Webflow review is invalid.",
                retryable=False,
            )
