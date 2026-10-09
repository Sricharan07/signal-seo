"""Secret-safe same-origin DataForSEO setup and evidence read API."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg import Connection
from pydantic import Field, RootModel, SecretStr
from signal_core.dataforseo_credentials import DataForSeoUnavailable, OpenBaoDataForSeoCredentials
from signal_core.dataforseo_service import DataForSeoSettingsService
from signal_core.recovery_authority import OpenBaoRecoveryAuthority

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class CredentialCommand(Contract):
    operation: Literal["credential"]
    login: SecretStr = Field(min_length=1, max_length=254)
    password: SecretStr = Field(min_length=8, max_length=512)


class RemoveCommand(Contract):
    operation: Literal["remove"]


class CapCommand(Contract):
    operation: Literal["cap"]
    cap_micros: int = Field(strict=True, ge=0, le=1000000000)


class Command(
    RootModel[
        Annotated[CredentialCommand | RemoveCommand | CapCommand, Field(discriminator="operation")]
    ]
):
    pass


@dataclass(frozen=True, repr=False)
class ComposedDataForSeoGateway:
    connection_factory: Callable[[], Connection] = field(repr=False)
    credentials: OpenBaoDataForSeoCredentials
    recovery_authority: OpenBaoRecoveryAuthority
    secret_options: dict = field(default_factory=dict, repr=False)
    recovery_options: dict = field(default_factory=dict, repr=False)
    execution_configured: bool = False

    async def service(self, connection):
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        return DataForSeoSettingsService(
            connection,
            self.credentials,
            generation.value,
            self.secret_options,
            self.execution_configured,
        )

    async def read(self, token, site):
        with self.connection_factory() as connection:
            return await (await self.service(connection)).read(token, site)

    async def control(self, token, site, command):
        with self.connection_factory() as connection:
            return await (await self.service(connection)).control(token, site, command)


def mount_dataforseo_http(application: FastAPI, gateway: ComposedDataForSeoGateway | None) -> None:
    def failed(status=503, code="DATAFORSEO_UNAVAILABLE"):
        return JSONResponse(
            status_code=status,
            content={
                "availability": "unavailable",
                "code": code,
                "features": {
                    key: "unavailable"
                    for key in ("competitor_gap", "search_volume", "competitor_backlinks")
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    def failure(error):
        return (
            failed(403, "DATAFORSEO_ACCESS_DENIED")
            if isinstance(error, DataForSeoUnavailable) and error.code == "DATAFORSEO_ACCESS_DENIED"
            else failed()
        )

    @application.get("/v1/sites/{site_id}/dataforseo", tags=["connectors"])
    async def read(request: Request, site_id: UUID):
        if gateway is None:
            return failed()
        try:
            result = await gateway.read(exact_cookie(request, SESSION_COOKIE_NAME), site_id)
            return JSONResponse(content=result, headers={"Cache-Control": "no-store"})
        except Exception as error:
            return failure(error)

    @application.post("/v1/sites/{site_id}/dataforseo", tags=["connectors"])
    async def control(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        from signal_api.main import _strict_json_request

        try:
            # Parse manually: framework validation errors must never echo credential fields.
            command = (await _strict_json_request(request, Command, maximum=2048)).root
        except ValueError:
            return failed(422, "DATAFORSEO_COMMAND_REJECTED")
        if gateway is None:
            return failed()
        try:
            return JSONResponse(
                content=await gateway.control(proof.session_token, site_id, command),
                headers={"Cache-Control": "no-store"},
            )
        except Exception as error:
            return failure(error)
