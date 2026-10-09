"""Dedicated cookie/CSRF owner API for the optional read-only GA4 connector."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg import Connection
from pydantic import Field, TypeAdapter
from signal_core.ga4_binding import Ga4Service
from signal_core.ga4_secrets import OpenBaoGa4Secrets
from signal_core.recovery_authority import OpenBaoRecoveryAuthority
from signal_core.shared_egress import SharedEgressProvider

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class Ga4Begin(Contract):
    operation: Literal["connect"]


class Ga4Complete(Contract):
    operation: Literal["complete"]
    attempt_id: UUID
    state: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    code: str = Field(min_length=16, max_length=4096)


class Ga4Confirm(Contract):
    operation: Literal["select"]
    attempt_id: UUID
    property_resource_name: str = Field(pattern=r"^properties/[1-9][0-9]{0,19}$")


class Ga4Import(Contract):
    operation: Literal["import"]
    start_date: date
    end_date: date


class Ga4Disconnect(Contract):
    operation: Literal["disconnect"]
    binding_id: UUID


_COMMAND = TypeAdapter(
    Annotated[
        Ga4Begin | Ga4Complete | Ga4Confirm | Ga4Import | Ga4Disconnect,
        Field(discriminator="operation"),
    ]
)


@dataclass(frozen=True, repr=False)
class ComposedGa4Gateway:
    connection_factory: Callable[[], Connection] = field(repr=False)
    secrets_store: OpenBaoGa4Secrets
    recovery_authority: OpenBaoRecoveryAuthority
    redirect_uri: str
    egress_factory: Callable[[UUID], SharedEgressProvider] = field(repr=False)

    async def _service(self, connection):
        generation = await self.recovery_authority.current_generation()

        async def current():
            return (await self.recovery_authority.current_generation()).value

        return Ga4Service(
            connection, self.secrets_store, generation.value, self.redirect_uri, current
        )

    async def read(self, token: str, site: UUID):
        with self.connection_factory() as connection:
            return (await self._service(connection)).read(token, site)

    async def control(self, token: str, site: UUID, command):
        with self.connection_factory() as connection:
            service = await self._service(connection)
            if isinstance(command, Ga4Begin):
                return await service.begin(token, site)
            if isinstance(command, Ga4Complete):
                return await service.complete(
                    token,
                    site,
                    attempt_id=command.attempt_id,
                    state=command.state,
                    code=command.code,
                    egress=self.egress_factory(site),
                )
            if isinstance(command, Ga4Confirm):
                return await service.confirm(
                    token,
                    site,
                    attempt_id=command.attempt_id,
                    property_resource_name=command.property_resource_name,
                    egress=self.egress_factory(site),
                )
            if isinstance(command, Ga4Import):
                return await service.import_report(
                    token,
                    site,
                    start_date=command.start_date,
                    end_date=command.end_date,
                    egress=self.egress_factory(site),
                )
            return await service.disconnect(
                token, site, binding_id=command.binding_id, egress=self.egress_factory(site)
            )


def mount_ga4_http(application: FastAPI, gateway: ComposedGa4Gateway | None) -> None:
    def unavailable():
        return JSONResponse(
            status_code=503, content={"state": "unavailable", "code": "GA4_UNCONFIGURED"}
        )

    @application.get("/v1/sites/{site_id}/ga4", tags=["connectors"])
    async def read(request: Request, site_id: UUID):
        if gateway is None:
            return unavailable()
        try:
            return JSONResponse(
                content=jsonable_encoder(
                    await gateway.read(exact_cookie(request, SESSION_COOKIE_NAME), site_id)
                )
            )
        except Exception:
            return JSONResponse(status_code=403, content={"code": "GA4_READ_DENIED"})

    @application.post("/v1/sites/{site_id}/ga4", tags=["connectors"])
    async def control(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        if gateway is None:
            return unavailable()
        try:
            if request.headers.get("content-type") != "application/json":
                raise ValueError
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 8192:
                    raise ValueError
                body.extend(chunk)
            command = _COMMAND.validate_json(bytes(body), strict=True)
        except ValueError:
            return JSONResponse(status_code=422, content={"code": "GA4_COMMAND_REJECTED"})
        try:
            return JSONResponse(
                content=jsonable_encoder(
                    await gateway.control(proof.session_token, site_id, command)
                )
            )
        except Exception:
            return JSONResponse(status_code=403, content={"code": "GA4_COMMAND_DENIED"})
