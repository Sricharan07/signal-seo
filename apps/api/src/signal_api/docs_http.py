"""Strict owner commands for an optional Google Docs composition."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg import Connection
from pydantic import Field, TypeAdapter, ValidationError
from signal_core.business_brain_extraction import BusinessBrainExtractor
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.docs_protocol import NOTION_UNAVAILABLE_REASON, DocsRejected
from signal_core.docs_secrets import OpenBaoDocsSecrets
from signal_core.docs_sync import DocsService
from signal_core.recovery_authority import OpenBaoRecoveryAuthority
from signal_core.shared_egress import SharedEgressProvider

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class DocsConnect(Contract):
    operation: Literal["connect"]


class DocsComplete(Contract):
    operation: Literal["complete"]
    attempt_id: UUID
    state: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    code: str = Field(min_length=16, max_length=4096)
    picked_file_ids: str = Field(min_length=10, max_length=4020)


class DocsSync(Contract):
    operation: Literal["sync"]


class DocsDisconnect(Contract):
    operation: Literal["disconnect"]
    binding_id: UUID


_COMMAND = TypeAdapter(
    Annotated[
        DocsConnect | DocsComplete | DocsSync | DocsDisconnect, Field(discriminator="operation")
    ]
)


@dataclass(frozen=True, repr=False)
class ComposedDocsGateway:
    connection_factory: Callable[[], Connection] = field(repr=False)
    egress_factory: Callable[[UUID], SharedEgressProvider] = field(repr=False)
    secrets_store: OpenBaoDocsSecrets
    recovery_authority: OpenBaoRecoveryAuthority
    store: EncryptedLocalArtifactStore
    key: ArtifactEncryptionKey
    dashboard_origin: str
    extractor: BusinessBrainExtractor | None = field(default=None, repr=False)

    async def service(self, connection: Connection) -> DocsService:
        generation = await self.recovery_authority.current_generation()

        async def current():
            return (await self.recovery_authority.current_generation()).value

        return DocsService(
            connection,
            self.secrets_store,
            self.store,
            self.key,
            generation.value,
            self.dashboard_origin + "/auth/google-docs/callback",
            current,
            self.extractor,
        )

    async def read(self, token: str, site_id: UUID) -> dict:
        with self.connection_factory() as connection:
            service = await self.service(connection)
            packet = service.status(token, site_id)
            await self.secrets_store.client_credentials()
            return packet

    async def control(self, token: str, site_id: UUID, command) -> dict:
        with self.connection_factory() as connection:
            service = await self.service(connection)
            if isinstance(command, DocsConnect):
                return await service.begin(token, site_id)
            if isinstance(command, DocsComplete):
                return await service.complete(
                    token,
                    site_id,
                    attempt_id=command.attempt_id,
                    state=command.state,
                    code=command.code,
                    picked_file_ids=command.picked_file_ids,
                    egress=self.egress_factory(site_id),
                )
            if isinstance(command, DocsDisconnect):
                return await service.disconnect(token, site_id, command.binding_id)
            return await service.sync(token, site_id, self.egress_factory(site_id))


def mount_docs_http(application: FastAPI, gateway: ComposedDocsGateway | None) -> None:
    def unavailable():
        return JSONResponse(
            status_code=503, content={"availability": "unavailable", "code": "DOCS_UNCONFIGURED"}
        )

    @application.get("/v1/sites/{site_id}/google-docs", tags=["connectors"])
    async def read(request: Request, site_id: UUID):
        if gateway is None:
            return unavailable()
        try:
            token = exact_cookie(request, SESSION_COOKIE_NAME)
            if token is None:
                raise DocsRejected()
            return JSONResponse(content=jsonable_encoder(await gateway.read(token, site_id)))
        except DocsRejected:
            return JSONResponse(status_code=403, content={"code": "DOCS_OWNER_DENIED"})
        except Exception:
            return unavailable()

    @application.post("/v1/sites/{site_id}/google-docs", tags=["connectors"])
    async def command(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        if gateway is None:
            return unavailable()
        try:
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                raise DocsRejected()
            raw = bytearray()
            async for part in request.stream():
                if len(raw) + len(part) > 12288:
                    raise DocsRejected()
                raw.extend(part)
            value = _COMMAND.validate_json(bytes(raw))
            return JSONResponse(
                content=jsonable_encoder(await gateway.control(proof.session_token, site_id, value))
            )
        except DocsRejected as error:
            return JSONResponse(status_code=409, content={"code": error.code})
        except ValidationError:
            return JSONResponse(status_code=422, content={"code": "DOCS_COMMAND_REJECTED"})
        except Exception:
            return unavailable()

    @application.get("/v1/sites/{site_id}/notion", tags=["connectors"])
    async def notion(site_id: UUID):
        return JSONResponse(
            status_code=503,
            content={"availability": "unavailable", "reason": NOTION_UNAVAILABLE_REASON},
        )
