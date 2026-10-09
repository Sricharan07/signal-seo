"""Owner-only same-origin report preferences and bounded delivery history."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg import Connection
from pydantic import Field
from signal_core.chat_reports import ChatReports
from signal_core.recovery_authority import OpenBaoRecoveryAuthority
from signal_core.slack_secrets import OpenBaoSlackSecrets
from signal_core.telegram_secrets import OpenBaoTelegramSecrets

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class ChatReportPreferenceCommand(Contract):
    channel: Literal["slack_channel", "slack_dm", "telegram"]
    enabled: bool = Field(strict=True)


@dataclass(frozen=True, repr=False)
class ComposedChatReportsGateway:
    connection_factory: Callable[[], Connection] = field(repr=False)
    recovery_authority: OpenBaoRecoveryAuthority
    slack_secrets: OpenBaoSlackSecrets | None = field(default=None, repr=False)
    telegram_secrets: OpenBaoTelegramSecrets | None = field(default=None, repr=False)
    slack_options: dict = field(default_factory=dict, repr=False)
    telegram_options: dict = field(default_factory=dict, repr=False)
    recovery_options: dict = field(default_factory=dict, repr=False)

    async def read(self, token: str, site: UUID) -> dict:
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        with self.connection_factory() as connection:
            state = ChatReports(connection, generation.value).read(token, site)
        for channel in state["channels"]:
            binding = channel.pop("binding_id")
            if channel["availability"] != "available":
                continue
            try:
                if channel["channel"] == "telegram":
                    if self.telegram_secrets is None:
                        raise RuntimeError
                    await self.telegram_secrets.bot(
                        "secret://telegram/" + binding, **self.telegram_options
                    )
                else:
                    if self.slack_secrets is None:
                        raise RuntimeError
                    await self.slack_secrets.bot("secret://slack/" + binding, **self.slack_options)
            except Exception:
                channel["availability"] = "unavailable"
        return state

    async def control(self, token: str, site: UUID, command: ChatReportPreferenceCommand) -> dict:
        if command.enabled:
            state = await self.read(token, site)
            if not any(
                item["channel"] == command.channel and item["availability"] == "available"
                for item in state["channels"]
            ):
                return {"state": "unavailable"}
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        with self.connection_factory() as connection:
            result = ChatReports(connection, generation.value).preference(
                token, site, channel=command.channel, enabled=command.enabled
            )
        return {"state": result}


def mount_chat_reports_http(
    application: FastAPI, gateway: ComposedChatReportsGateway | None
) -> None:
    def unavailable():
        return JSONResponse(status_code=503, content={"availability": "unavailable"})

    @application.get("/v1/sites/{site_id}/chat-reports", tags=["notifications"])
    async def read(request: Request, site_id: UUID):
        if gateway is None:
            return unavailable()
        try:
            return JSONResponse(
                content=await gateway.read(exact_cookie(request, SESSION_COOKIE_NAME), site_id)
            )
        except PermissionError:
            return JSONResponse(status_code=403, content={"code": "CHAT_REPORT_AUTHORITY_DENIED"})
        except Exception:
            return unavailable()

    @application.post("/v1/sites/{site_id}/chat-reports", tags=["notifications"])
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
            command = ChatReportPreferenceCommand.model_validate_json(bytes(body), strict=True)
        except ValueError:
            return JSONResponse(status_code=422, content={"code": "CHAT_REPORT_COMMAND_REJECTED"})
        try:
            return JSONResponse(
                content=await gateway.control(proof.session_token, site_id, command)
            )
        except PermissionError:
            return JSONResponse(status_code=403, content={"code": "CHAT_REPORT_AUTHORITY_DENIED"})
        except Exception:
            return unavailable()
