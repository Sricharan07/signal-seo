"""Current-owner, CSRF-protected content drafting and editorial Inbox endpoints."""

from typing import Annotated, Literal, Protocol, runtime_checkable
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from pydantic import Field
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.candidate_build import CandidatePolicyRejected
from signal_core.candidate_build_service import CandidateBuildConflict, CandidateBuildUnavailable
from signal_core.content_writer import ContentWriterRejected, ContentWriterUnavailable
from signal_core.github_app import GitHubAppProtocolError
from signal_core.github_pr_extension import GitHubPrExtensionUnavailable
from signal_core.github_read_binding import GitHubBindingUnavailable

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract

WRITER_ERRORS = (
    ContentWriterRejected,
    ContentWriterUnavailable,
    AuthorizationDenied,
    InvalidSession,
    CandidatePolicyRejected,
    CandidateBuildConflict,
    CandidateBuildUnavailable,
    GitHubAppProtocolError,
    GitHubPrExtensionUnavailable,
    GitHubBindingUnavailable,
)


class BriefPayload(Contract):
    topic: str = Field(min_length=1, max_length=200)
    intent: Literal["informational", "commercial", "navigational", "transactional"]
    query: str = Field(min_length=1, max_length=200)
    source_ids: list[UUID] = Field(min_length=1, max_length=8)
    fact_ids: list[UUID] = Field(min_length=1, max_length=20)
    internal_links: list[str] = Field(max_length=8)
    kind: Literal["new_article", "content_refresh"]


class CreateBrief(Contract):
    schema_version: Literal[1]
    payload: BriefPayload
    proposal: bool = False
    supersedes_id: UUID | None = None


class BriefAction(Contract):
    schema_version: Literal[1]
    brief_id: UUID


class CapAction(Contract):
    schema_version: Literal[1]
    cap: int = Field(ge=1, le=5, strict=True)


class CandidateAction(Contract):
    schema_version: Literal[1]
    draft_id: UUID
    extension_id: UUID
    destination: str = Field(min_length=1, max_length=1024)


class ReviewAction(Contract):
    schema_version: Literal[1]
    candidate_id: UUID
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "rejected", "changes_requested"]


class DeliveryApprovalAction(Contract):
    schema_version: Literal[1]
    candidate_id: UUID
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledged_sentences: list[str] = Field(max_length=120)


@runtime_checkable
class BrowserContentWriter(Protocol):
    @property
    def content_writer_configured(self) -> bool: ...
    @property
    def content_candidate_configured(self) -> bool: ...
    async def read_content_writer(self, *, session_token: str, site_id: UUID) -> dict: ...
    async def mutate_content_writer(
        self, *, session_token: str, site_id: UUID, action: str, values: dict
    ) -> dict: ...


def register_content_writer_routes(application: FastAPI):
    from signal_api.main import _error, _strict_json_request

    def port(request):
        result = request.app.state.browser_writer
        if not isinstance(result, BrowserContentWriter):
            raise ContentWriterUnavailable("WRITER_UNCONFIGURED")
        return result

    def error(request, exception):
        denied = (
            isinstance(exception, (AuthorizationDenied, InvalidSession))
            or str(exception) == "owner_access_denied"
        )
        return _error(
            request,
            status=403 if denied else 409 if isinstance(exception, ContentWriterRejected) else 503,
            code="WRITER_ACCESS_DENIED" if denied else "WRITER_UNAVAILABLE",
            message="The articles request could not be completed.",
            retryable=False,
        )

    @application.get("/v1/sites/{site_id}/content-writer")
    async def read(request: Request, site_id: UUID):
        try:
            service = port(request)
            result = await service.read_content_writer(
                session_token=exact_cookie(request, SESSION_COOKIE_NAME), site_id=site_id
            )
            return {
                "schema_version": 1,
                **result,
                "model_state": "available"
                if service.content_writer_configured
                and result.get("monthly_model_budget", {}).get("state") != "unavailable"
                else "unavailable",
                "candidate_state": "available"
                if service.content_candidate_configured
                else "unavailable",
            }
        except WRITER_ERRORS as exception:
            return error(request, exception)

    contracts = {
        "briefs": CreateBrief,
        "accept-brief": BriefAction,
        "caps": CapAction,
        "drafts": BriefAction,
        "candidates": CandidateAction,
        "review": ReviewAction,
        "approve-delivery": DeliveryApprovalAction,
    }

    async def mutate(request, site_id, proof, action):
        try:
            values = (
                await _strict_json_request(request, contracts[action], maximum=20000)
            ).model_dump(mode="json", exclude={"schema_version"})
            return {
                "schema_version": 1,
                **await port(request).mutate_content_writer(
                    session_token=proof.session_token, site_id=site_id, action=action, values=values
                ),
            }
        except WRITER_ERRORS as exception:
            return error(request, exception)
        except ValueError:
            return _error(
                request,
                status=422,
                code="WRITER_REQUEST_INVALID",
                message="The articles request is invalid.",
                retryable=False,
            )

    @application.post("/v1/sites/{site_id}/content-writer/briefs")
    async def brief(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "briefs")

    @application.post("/v1/sites/{site_id}/content-writer/accept-brief")
    async def accept(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "accept-brief")

    @application.post("/v1/sites/{site_id}/content-writer/caps")
    async def caps(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "caps")

    @application.post("/v1/sites/{site_id}/content-writer/drafts")
    async def draft(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "drafts")

    @application.post("/v1/sites/{site_id}/content-writer/candidates")
    async def candidate(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "candidates")

    @application.post("/v1/sites/{site_id}/content-writer/review")
    async def review(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "review")

    @application.post("/v1/sites/{site_id}/content-writer/approve-delivery")
    async def approve_delivery(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "approve-delivery")
