"""Owner-scoped evidence reads and CSRF-protected proposal-only planning commands."""

from typing import Annotated, Literal, Protocol, runtime_checkable
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.content_writer import ContentWriterUnavailable
from signal_core.seo_strategy_service import StrategyConflict, StrategyUnavailable

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class RefreshStrategy(Contract):
    schema_version: Literal[1]


class DecideStrategy(Contract):
    schema_version: Literal[1]
    snapshot_id: UUID
    item_id: UUID
    decision: Literal["accepted", "dismissed"]


class ExpandIdeas(Contract):
    schema_version: Literal[1]
    snapshot_id: UUID


@runtime_checkable
class BrowserStrategy(Protocol):
    async def read_seo_strategy(
        self, *, session_token: str, site_id: UUID, snapshot_id: UUID | None = None
    ) -> dict: ...
    async def mutate_seo_strategy(
        self, *, session_token: str, site_id: UUID, action: str, values: dict
    ) -> dict: ...


def register_seo_strategy_routes(application: FastAPI):
    from signal_api.main import _error, _strict_json_request

    def port(request):
        service = request.app.state.browser_strategy
        if not isinstance(service, BrowserStrategy):
            raise StrategyUnavailable("Strategy projection unconfigured.")
        return service

    def error(request, exception):
        denied = isinstance(exception, (AuthorizationDenied, InvalidSession))
        return _error(
            request,
            status=403 if denied else 409 if isinstance(exception, StrategyConflict) else 503,
            code="STRATEGY_ACCESS_DENIED" if denied else "STRATEGY_UNAVAILABLE",
            message="The strategy request could not be completed.",
            retryable=False,
        )

    errors = (
        AuthorizationDenied,
        InvalidSession,
        StrategyUnavailable,
        StrategyConflict,
        ContentWriterUnavailable,
    )

    @application.get("/v1/sites/{site_id}/seo-strategy")
    async def read(request: Request, site_id: UUID, snapshot_id: UUID | None = None):
        try:
            return {
                "schema_version": 1,
                **await port(request).read_seo_strategy(
                    session_token=exact_cookie(request, SESSION_COOKIE_NAME),
                    site_id=site_id,
                    snapshot_id=snapshot_id,
                ),
            }
        except errors as exception:
            return error(request, exception)

    @application.get("/v1/sites/{site_id}/seo-strategy/{snapshot_id}/evidence/{evidence_id}")
    async def evidence(request: Request, site_id: UUID, snapshot_id: UUID, evidence_id: UUID):
        try:
            result = await port(request).read_seo_strategy(
                session_token=exact_cookie(request, SESSION_COOKIE_NAME),
                site_id=site_id,
                snapshot_id=snapshot_id,
            )
            record = (
                (result.get("snapshot") or {})
                .get("payload", {})
                .get("evidence", {})
                .get(str(evidence_id))
            )
            if record is None:
                return _error(
                    request,
                    status=404,
                    code="EVIDENCE_NOT_FOUND",
                    message="Evidence is unavailable.",
                    retryable=False,
                )
            return {
                "schema_version": 1,
                "snapshot_id": str(snapshot_id),
                "evidence_id": str(evidence_id),
                **record,
            }
        except errors as exception:
            return error(request, exception)

    async def mutate(request, site_id, proof, action, contract):
        try:
            values = (await _strict_json_request(request, contract, maximum=2048)).model_dump(
                mode="json", exclude={"schema_version"}
            )
            return {
                "schema_version": 1,
                **await port(request).mutate_seo_strategy(
                    session_token=proof.session_token, site_id=site_id, action=action, values=values
                ),
            }
        except errors as exception:
            return error(request, exception)
        except ValueError:
            return _error(
                request,
                status=422,
                code="STRATEGY_REQUEST_INVALID",
                message="The strategy request is invalid.",
                retryable=False,
            )

    @application.post("/v1/sites/{site_id}/seo-strategy/refresh")
    async def refresh(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "refresh", RefreshStrategy)

    @application.post("/v1/sites/{site_id}/seo-strategy/decide")
    async def decide(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "decide", DecideStrategy)

    @application.post("/v1/sites/{site_id}/seo-strategy/ideas")
    async def ideas(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "ideas", ExpandIdeas)
