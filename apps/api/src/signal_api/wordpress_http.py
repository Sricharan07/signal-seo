"""Owner-only WordPress drafts; credentials never cross browser ingress."""

from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol, runtime_checkable
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from psycopg import Error
from pydantic import Field, TypeAdapter
from signal_core.content_writer import ContentWriterRejected, ContentWriterUnavailable
from signal_core.recovery_authority import RecoveryAuthorityError
from signal_core.wordpress_protocol import WordPressUnavailable
from signal_core.wordpress_service import WordPressService
from signal_core.write_intent_journal import (
    WriteIntentJournalConflict,
    WriteIntentJournalUnavailable,
)

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class Connect(Contract):
    operation: Literal["connect"]
    binding_id: UUID
    origin: str = Field(min_length=1, max_length=2048)


class Seal(Contract):
    operation: Literal["seal"]
    binding_id: UUID
    draft_id: UUID


class Review(Contract):
    operation: Literal["review"]
    candidate_id: UUID
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "rejected"]


class Create(Contract):
    operation: Literal["create"]
    candidate_id: UUID
    intent_id: UUID
    prior_intent_id: UUID | None = None


class ReadIntent(Contract):
    operation: Literal["reconcile", "observe"]
    intent_id: UUID


class Revoke(Contract):
    operation: Literal["revoke"]
    binding_id: UUID


_COMMAND = TypeAdapter(
    Annotated[
        Connect | Seal | Review | Create | ReadIntent | Revoke, Field(discriminator="operation")
    ]
)


@dataclass(frozen=True, repr=False)
class ComposedWordPressGateway:
    connection_factory: object = field(repr=False)
    identity_connection_factory: object = field(repr=False)
    recovery_authority: object = field(repr=False)
    service: WordPressService

    async def read_wordpress(self, *, session_token, site_id):
        generation = await self.recovery_authority.current_generation()
        with self.connection_factory() as connection:
            return self.service.read(
                connection, session_token=session_token, generation=generation, site_id=site_id
            )

    async def mutate_wordpress(self, *, session_token, site_id, command):
        generation = await self.recovery_authority.current_generation()
        values = command.model_dump(exclude={"operation"})
        factory = (
            self.identity_connection_factory
            if command.operation in {"create", "reconcile", "observe"}
            else self.connection_factory
        )
        with factory() as connection:
            common = dict(session_token=session_token, generation=generation, site_id=site_id)
            self.service.read(connection, **common)
            if (
                command.operation not in {"review", "seal", "revoke"}
                and not self.service.configured
            ):
                return {"state": "unavailable", "reason": "WORDPRESS_UNCONFIGURED"}
            action = getattr(self.service, command.operation)
            if command.operation in {"review", "seal", "revoke"}:
                return action(connection, **common, **values)
            return await action(connection, **common, **values)


@runtime_checkable
class BrowserWordPress(Protocol):
    async def read_wordpress(self, *, session_token: str, site_id: UUID) -> dict: ...
    async def mutate_wordpress(
        self, *, session_token: str, site_id: UUID, command: object
    ) -> dict: ...


def mount_wordpress_http(application: FastAPI, gateway):
    from signal_api.main import _error, _strict_json_request

    def port():
        if not isinstance(gateway, BrowserWordPress):
            raise WordPressUnavailable("WORDPRESS_UNCONFIGURED")
        return gateway

    def failed(request, exception):
        denied = str(exception) == "owner_access_denied"
        return _error(
            request,
            status=403 if denied else 503,
            code="WORDPRESS_ACCESS_DENIED" if denied else "WORDPRESS_UNAVAILABLE",
            message="WordPress delivery is unavailable.",
            retryable=False,
        )

    errors = (
        WordPressUnavailable,
        RecoveryAuthorityError,
        ContentWriterUnavailable,
        ContentWriterRejected,
        WriteIntentJournalUnavailable,
        WriteIntentJournalConflict,
        Error,
    )

    @application.get("/v1/sites/{site_id}/wordpress")
    async def read(request: Request, site_id: UUID):
        try:
            return {
                "schema_version": 1,
                **await port().read_wordpress(
                    session_token=exact_cookie(request, SESSION_COOKIE_NAME), site_id=site_id
                ),
            }
        except errors as exception:
            return failed(request, exception)

    @application.post("/v1/sites/{site_id}/wordpress")
    async def mutate(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        try:
            value = await _strict_json_request(request, WordPressCommand, maximum=4096)
            command = _COMMAND.validate_python(value.command)
            return {
                "schema_version": 1,
                **await port().mutate_wordpress(
                    session_token=proof.session_token, site_id=site_id, command=command
                ),
            }
        except errors as exception:
            return failed(request, exception)
        except ValueError:
            return _error(
                request,
                status=422,
                code="WORDPRESS_REQUEST_INVALID",
                message="The WordPress request is invalid.",
                retryable=False,
            )


class WordPressCommand(Contract):
    schema_version: Literal[1]
    command: dict
