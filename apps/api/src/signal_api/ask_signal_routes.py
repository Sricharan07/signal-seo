"""Private, site-scoped assistant ingress; no action execution endpoints."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol, runtime_checkable
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from signal_core.ask_signal import bounded_text
from signal_core.ask_signal_service import AssistantConflict, AssistantUnavailable
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.content_writer import ContentWriterUnavailable
from signal_core.decision_contracts import canonical_json
from signal_core.model_budget import ModelBudgetUnavailable

from signal_api.authentication import BrowserAuthenticationUnavailable
from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    BrowserRequestRejected,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class NewConversation(Contract):
    schema_version: Literal[1]
    request_id: UUID


class NewMessage(NewConversation):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def valid_text(cls, value):
        if not bounded_text(value, 2000):
            raise ValueError("Invalid message text.")
        return value


class NewMemory(NewMessage):
    kind: Literal["preference", "context"]
    text: str = Field(min_length=1, max_length=500)


class ForgetMemory(Contract):
    schema_version: Literal[1]


@runtime_checkable
class BrowserAssistant(Protocol):
    async def assistant(
        self, *, session_token: str, site_id: UUID, action: str, values: dict
    ) -> dict: ...


def response(data, status=200):
    def utc(value):
        if isinstance(value, dict):
            return {
                k: (
                    datetime.fromisoformat(v).astimezone(UTC).isoformat().replace("+00:00", "Z")
                    if k in {"created_at", "updated_at"} and isinstance(v, str)
                    else utc(v)
                )
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [utc(v) for v in value]
        return value

    document = utc({"schema_version": 1, **data})
    if len(canonical_json(document)) > 256 * 1024:
        raise AssistantUnavailable("Assistant response exceeds its bound.")
    return JSONResponse(document, status_code=status, headers={"Cache-Control": "no-store"})


def register_ask_signal_routes(application: FastAPI):
    from signal_api.main import _error, _strict_json_request

    async def invoke(request, site_id, action, values, token, status=200):
        try:
            port = request.app.state.browser_assistant or request.app.state.browser_login
            if not isinstance(port, BrowserAssistant):
                raise AssistantUnavailable()
            return response(
                await port.assistant(
                    session_token=token, site_id=site_id, action=action, values=values
                ),
                status,
            )
        except (AuthorizationDenied, InvalidSession):
            return _error(
                request,
                status=403,
                code="ASSISTANT_ACCESS_DENIED",
                message="This conversation or site is not accessible.",
                retryable=False,
            )
        except AssistantConflict:
            return _error(
                request,
                status=409,
                code="ASSISTANT_CONFLICT",
                message="The request conflicts or exceeds a conversation or memory limit.",
                retryable=False,
            )
        except (
            AssistantUnavailable,
            BrowserAuthenticationUnavailable,
            ContentWriterUnavailable,
            ModelBudgetUnavailable,
            psycopg.Error,
            OSError,
        ):
            return _error(
                request,
                status=503,
                code="ASSISTANT_UNAVAILABLE",
                message="Ask Signal is unavailable. No answer was simulated.",
                retryable=False,
            )

    async def read(request, site_id, action, values=None):
        try:
            token = exact_cookie(request, SESSION_COOKIE_NAME)
        except BrowserRequestRejected:
            return _error(
                request,
                status=403,
                code="ASSISTANT_ACCESS_DENIED",
                message="Sign in to access Ask Signal.",
                retryable=False,
            )
        return await invoke(request, site_id, action, values or {}, token)

    async def mutate(request, site_id, proof, action, contract, values=None, status=200):
        try:
            body = (await _strict_json_request(request, contract, maximum=12288)).model_dump(
                mode="json", exclude={"schema_version"}
            )
        except ValueError:
            return _error(
                request,
                status=422,
                code="ASSISTANT_REQUEST_INVALID",
                message="The assistant request is invalid or too large.",
                retryable=False,
            )
        return await invoke(
            request, site_id, action, {**body, **(values or {})}, proof.session_token, status
        )

    @application.get("/v1/sites/{site_id}/assistant")
    async def overview(request: Request, site_id: UUID):
        return await read(request, site_id, "overview")

    @application.post("/v1/sites/{site_id}/assistant/conversations")
    async def create(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "create", NewConversation, status=201)

    @application.get("/v1/sites/{site_id}/assistant/conversations/{conversation_id}")
    async def conversation(request: Request, site_id: UUID, conversation_id: UUID):
        return await read(request, site_id, "read", {"conversation_id": str(conversation_id)})

    @application.post("/v1/sites/{site_id}/assistant/conversations/{conversation_id}/messages")
    async def message(
        request: Request,
        site_id: UUID,
        conversation_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(
            request,
            site_id,
            proof,
            "message",
            NewMessage,
            {"conversation_id": str(conversation_id)},
        )

    @application.get("/v1/sites/{site_id}/assistant/memory")
    async def memories(request: Request, site_id: UUID):
        return await read(request, site_id, "memory")

    @application.post("/v1/sites/{site_id}/assistant/memory")
    async def remember(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(request, site_id, proof, "add_memory", NewMemory, status=201)

    @application.post("/v1/sites/{site_id}/assistant/memory/{memory_id}/forget")
    async def forget(
        request: Request,
        site_id: UUID,
        memory_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        return await mutate(
            request, site_id, proof, "forget", ForgetMemory, {"memory_id": str(memory_id)}
        )
