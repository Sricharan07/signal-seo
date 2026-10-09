"""Owner-only citation gaps and proposal decisions, with no observation/write port."""

from typing import Annotated, Literal, Protocol, runtime_checkable
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from pydantic import Field
from signal_core.ai_visibility import AiVisibilityUnavailable
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.candidate_build_service import CandidateBuildUnavailable
from signal_core.content_writer import ContentWriterRejected, ContentWriterUnavailable
from signal_core.recipe_releases import RecipeReleaseUnavailable
from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class PrepareVisibility(Contract):
    schema_version: Literal[1]


class DecideVisibility(PrepareVisibility):
    proposal_id: UUID
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["accepted", "dismissed"]


class ApproveQuestions(PrepareVisibility):
    request_id: UUID
    crawl_manifest_id: UUID
    supersedes_id: UUID | None
    questions: list[str] = Field(min_length=1, max_length=25)


class SealVisibility(PrepareVisibility):
    proposal_id: UUID
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    fact_fields: dict[str, UUID] = Field(min_length=1, max_length=2)


@runtime_checkable
class BrowserAiVisibility(Protocol):
    async def read_ai_visibility(self, *, session_token: str, site_id: UUID) -> dict: ...
    async def mutate_ai_visibility(
        self, *, session_token: str, site_id: UUID, action: str, values: dict
    ) -> dict: ...


def register_ai_visibility_routes(application: FastAPI):
    from signal_api.main import _error, _strict_json_request

    def port(request):
        service = request.app.state.browser_visibility
        if not isinstance(service, BrowserAiVisibility):
            raise ContentWriterUnavailable("VISIBILITY_UNCONFIGURED")
        return service

    def error(request, exception):
        denied = (
            isinstance(exception, (AuthorizationDenied, InvalidSession))
            or str(exception) == "owner_access_denied"
        )
        return _error(
            request,
            status=403 if denied else 409 if isinstance(exception, ContentWriterRejected) else 503,
            code="VISIBILITY_ACCESS_DENIED" if denied else "VISIBILITY_UNAVAILABLE",
            message="The AI visibility request could not be completed.",
            retryable=False,
        )

    @application.get("/v1/sites/{site_id}/ai-visibility")
    async def read(request: Request, site_id: UUID):
        try:
            return {
                "schema_version": 1,
                **await port(request).read_ai_visibility(
                    session_token=exact_cookie(request, SESSION_COOKIE_NAME), site_id=site_id
                ),
            }
        except (
            ContentWriterRejected,
            ContentWriterUnavailable,
            AuthorizationDenied,
            InvalidSession,
        ) as exception:
            return error(request, exception)

    async def mutate(request, site_id, proof, action, contract):
        try:
            values = (await _strict_json_request(request, contract, maximum=32768)).model_dump(
                mode="json", exclude={"schema_version"}
            )
            return {
                "schema_version": 1,
                **await port(request).mutate_ai_visibility(
                    session_token=proof.session_token, site_id=site_id, action=action, values=values
                ),
            }
        except (
            ContentWriterRejected,
            ContentWriterUnavailable,
            AuthorizationDenied,
            InvalidSession,
            AiVisibilityUnavailable,
            TechnicalRecipeUnavailable,
            CandidateBuildUnavailable,
            RecipeReleaseUnavailable,
        ) as exception:
            return error(request, exception)
        except ValueError:
            return _error(
                request,
                status=422,
                code="VISIBILITY_REQUEST_INVALID",
                message="The AI visibility request is invalid.",
                retryable=False,
            )

    @application.post("/v1/sites/{site_id}/ai-visibility/prepare")
    async def prepare(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "prepare", PrepareVisibility)

    @application.post("/v1/sites/{site_id}/ai-visibility/decide")
    async def decide(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "decide", DecideVisibility)

    @application.post("/v1/sites/{site_id}/ai-visibility/questions-propose")
    async def questions_propose(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "questions-propose", PrepareVisibility)

    @application.post("/v1/sites/{site_id}/ai-visibility/questions-approve")
    async def questions_approve(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "questions-approve", ApproveQuestions)

    @application.post("/v1/sites/{site_id}/ai-visibility/seal")
    async def seal(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "seal", SealVisibility)
