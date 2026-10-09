"""Same-origin membership opt-in; no emailed login or approval credentials."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg import Connection
from pydantic import Field
from signal_core.email_notifications import EmailNotifications
from signal_core.recovery_authority import OpenBaoRecoveryAuthority
from signal_core.smtp_submission import OpenBaoSmtpCredential

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class EmailPreferenceCommand(Contract):
    enabled: bool = Field(strict=True)
    address: str | None = Field(default=None, max_length=320)


@dataclass(frozen=True, repr=False)
class ComposedEmailGateway:
    connection_factory: Callable[[], Connection]
    recovery_authority: OpenBaoRecoveryAuthority
    credentials: OpenBaoSmtpCredential | None = None
    credential_options: dict = field(default_factory=dict, repr=False)
    recovery_options: dict = field(default_factory=dict, repr=False)

    async def read(self, token: str, site: UUID) -> dict:
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        with self.connection_factory() as connection:
            state = EmailNotifications(connection, generation.value).preference(token, site)
        try:
            if self.credentials is None:
                raise RuntimeError
            await self.credentials.read(**self.credential_options)
        except Exception:
            state["availability"] = "unavailable"
            state["can_enable"] = False
        return state

    async def control(self, token: str, site: UUID, command: EmailPreferenceCommand) -> dict:
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        if command.enabled:
            if self.credentials is None:
                raise RuntimeError("EMAIL_PROVIDER_UNAVAILABLE")
            await self.credentials.read(**self.credential_options)
        with self.connection_factory() as connection:
            service = EmailNotifications(connection, generation.value)
            if command.enabled and service.preference(token, site)["availability"] == "unavailable":
                raise RuntimeError("EMAIL_PROVIDER_UNAVAILABLE")
            state = service.opt_in(token, site, enabled=command.enabled, address=command.address)
            return {"state": state}


def mount_email_http(application: FastAPI, gateway: ComposedEmailGateway | None) -> None:
    def unavailable():
        return JSONResponse(status_code=503, content={"availability": "unavailable"})

    @application.get("/v1/sites/{site_id}/email", tags=["notifications"])
    async def read(request: Request, site_id: UUID):
        if gateway is None:
            return unavailable()
        try:
            token = exact_cookie(request, SESSION_COOKIE_NAME)
            return JSONResponse(content=await gateway.read(token, site_id))
        except PermissionError:
            return JSONResponse(status_code=403, content={"code": "EMAIL_AUTHORITY_DENIED"})
        except Exception:
            return unavailable()

    @application.post("/v1/sites/{site_id}/email", tags=["notifications"])
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
            async for part in request.stream():
                body.extend(part)
                if len(body) > 1024:
                    raise ValueError
            command = EmailPreferenceCommand.model_validate_json(bytes(body), strict=True)
        except ValueError:
            return JSONResponse(status_code=422, content={"code": "EMAIL_COMMAND_REJECTED"})
        try:
            return JSONResponse(
                content=await gateway.control(proof.session_token, site_id, command)
            )
        except (PermissionError, ValueError):
            return JSONResponse(status_code=403, content={"code": "EMAIL_AUTHORITY_DENIED"})
        except Exception:
            return unavailable()
