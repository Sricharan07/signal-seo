"""Same-session, same-origin owner Business Brain endpoints."""

from typing import Annotated, Protocol, runtime_checkable
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from signal_core.business_brain import BusinessBrainRejected, BusinessBrainUnavailable
from signal_core.business_brain_extraction import ExtractionState

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.business_brain_contracts import (
    BrainVoiceResponse,
    ExtractionReceipt,
    ExtractionRequest,
    ExtractionResult,
    ExtractionStatus,
    FactCorrection,
    FactMutation,
    FactResponse,
    FactsResponse,
    FactStatus,
    MutationResponse,
    OwnerFactRequest,
    VoiceResponse,
    VoiceUpdate,
)


@runtime_checkable
class BrowserBusinessBrainGateway(Protocol):
    @property
    def business_brain_configured(self) -> bool: ...
    async def read_business_brain(self, *, session_token: str, site_id: UUID) -> dict: ...
    async def mutate_business_brain(
        self, *, session_token: str, site_id: UUID, action: str, values: dict
    ) -> str: ...
    async def extract_business_brain(
        self, *, session_token: str, site_id: UUID, values: dict
    ) -> ExtractionState: ...


def register_business_brain_routes(application: FastAPI) -> None:
    from signal_api.authentication import BrowserAuthenticationUnavailable
    from signal_api.main import _error, _strict_json_request

    def gateway(request):
        value = request.app.state.browser_brain
        if not isinstance(value, BrowserBusinessBrainGateway):
            raise BrowserAuthenticationUnavailable()
        return value

    def error(request, exception):
        denied = str(exception) == "owner_access_denied"
        return _error(
            request,
            status=403 if denied else 409 if isinstance(exception, BusinessBrainRejected) else 503,
            code="BRAIN_ACCESS_DENIED" if denied else "BRAIN_UNAVAILABLE",
            message="Only the current site owner can access Business facts."
            if denied
            else "The Business Brain request could not be completed.",
            retryable=False,
        )

    def fact(value, site_id):
        return FactResponse(
            **value,
            provenance_url=f"/v1/sites/{site_id}/business-brain/facts/{value['fact_id']}/provenance",
        )

    @application.get("/v1/sites/{site_id}/business-brain/facts", response_model=FactsResponse)
    async def facts(request: Request, site_id: UUID, status: FactStatus | None = None):
        try:
            brain = await gateway(request).read_business_brain(
                session_token=exact_cookie(request, SESSION_COOKIE_NAME), site_id=site_id
            )
            return FactsResponse(
                facts=tuple(
                    fact(value, site_id)
                    for value in brain["facts"]
                    if status is None or value["status"] == status
                )
            )
        except (BusinessBrainUnavailable, BusinessBrainRejected) as exception:
            return error(request, exception)

    @application.get(
        "/v1/sites/{site_id}/business-brain/approved-facts", response_model=FactsResponse
    )
    async def approved(request: Request, site_id: UUID):
        return await facts(request, site_id, "approved")

    @application.get(
        "/v1/sites/{site_id}/business-brain/facts/{fact_id}/provenance", response_model=FactResponse
    )
    async def provenance(request: Request, site_id: UUID, fact_id: UUID):
        try:
            brain = await gateway(request).read_business_brain(
                session_token=exact_cookie(request, SESSION_COOKIE_NAME), site_id=site_id
            )
            value = next((item for item in brain["facts"] if item["fact_id"] == str(fact_id)), None)
            if value is None:
                return _error(
                    request,
                    status=404,
                    code="BRAIN_FACT_UNAVAILABLE",
                    message="The fact is unavailable.",
                    retryable=False,
                )
            return fact(value, site_id)
        except (BusinessBrainUnavailable, BusinessBrainRejected) as exception:
            return error(request, exception)

    async def mutation(request, site_id, proof, action, contract, fact_id=None):
        try:
            values = (await _strict_json_request(request, contract, maximum=20000)).model_dump(
                exclude={"schema_version"}
            )
            if fact_id is not None:
                values["fact_id"] = fact_id
            outcome = await gateway(request).mutate_business_brain(
                session_token=proof.session_token, site_id=site_id, action=action, values=values
            )
            return MutationResponse(outcome=outcome)
        except (BusinessBrainUnavailable, BusinessBrainRejected) as exception:
            return error(request, exception)
        except ValueError:
            return _error(
                request,
                status=422,
                code="BRAIN_REQUEST_INVALID",
                message="The Business facts request is invalid.",
                retryable=False,
            )

    @application.post("/v1/sites/{site_id}/business-brain/facts", response_model=MutationResponse)
    async def manual(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutation(request, site_id, proof, "propose", OwnerFactRequest)

    @application.post(
        "/v1/sites/{site_id}/business-brain/facts/{fact_id}/approve",
        response_model=MutationResponse,
    )
    async def approve(
        request: Request,
        site_id: UUID,
        fact_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutation(request, site_id, proof, "approve", FactMutation, fact_id)

    @application.post(
        "/v1/sites/{site_id}/business-brain/facts/{fact_id}/correct",
        response_model=MutationResponse,
    )
    async def correct(
        request: Request,
        site_id: UUID,
        fact_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutation(request, site_id, proof, "correct", FactCorrection, fact_id)

    @application.post(
        "/v1/sites/{site_id}/business-brain/facts/{fact_id}/remove", response_model=MutationResponse
    )
    async def remove(
        request: Request,
        site_id: UUID,
        fact_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutation(request, site_id, proof, "remove", FactMutation, fact_id)

    @application.get("/v1/sites/{site_id}/business-brain/voice", response_model=BrainVoiceResponse)
    async def voice(request: Request, site_id: UUID):
        try:
            brain = await gateway(request).read_business_brain(
                session_token=exact_cookie(request, SESSION_COOKIE_NAME), site_id=site_id
            )
            return BrainVoiceResponse(
                voice=VoiceResponse(**brain["voice"]) if brain["voice"] else None
            )
        except (BusinessBrainUnavailable, BusinessBrainRejected) as exception:
            return error(request, exception)

    @application.put("/v1/sites/{site_id}/business-brain/voice", response_model=MutationResponse)
    async def update_voice(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutation(request, site_id, proof, "voice", VoiceUpdate)

    @application.get(
        "/v1/sites/{site_id}/business-brain/extraction", response_model=ExtractionStatus
    )
    async def status(request: Request, site_id: UUID):
        try:
            port = gateway(request)
            brain = await port.read_business_brain(
                session_token=exact_cookie(request, SESSION_COOKIE_NAME), site_id=site_id
            )
            return ExtractionStatus(
                state="available" if port.business_brain_configured else "unavailable",
                reason=None if port.business_brain_configured else "MODEL_UNCONFIGURED",
                extractions=tuple(ExtractionReceipt(**value) for value in brain["extractions"]),
            )
        except (BusinessBrainUnavailable, BusinessBrainRejected) as exception:
            return error(request, exception)

    @application.post(
        "/v1/sites/{site_id}/business-brain/extraction", response_model=ExtractionResult
    )
    async def extract(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        try:
            values = (
                await _strict_json_request(request, ExtractionRequest, maximum=2000)
            ).model_dump(exclude={"schema_version"})
            result = await gateway(request).extract_business_brain(
                session_token=proof.session_token, site_id=site_id, values=values
            )
            return ExtractionResult(**result.__dict__)
        except (BusinessBrainUnavailable, BusinessBrainRejected) as exception:
            return error(request, exception)
        except ValueError:
            return _error(
                request,
                status=422,
                code="BRAIN_REQUEST_INVALID",
                message="The extraction request is invalid.",
                retryable=False,
            )
