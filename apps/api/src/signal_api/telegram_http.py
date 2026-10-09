"""Dashboard-owned bot setup and bounded secret-authenticated Telegram ingress."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg import Connection
from pydantic import Field, TypeAdapter
from signal_core.recovery_authority import OpenBaoRecoveryAuthority
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import SharedEgressProvider
from signal_core.telegram_binding import TelegramService
from signal_core.telegram_protocol import TelegramRejected
from signal_core.telegram_secrets import OpenBaoTelegramSecrets

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class TelegramInstall(Contract):
    operation: Literal["install"]
    bot_token: str = Field(pattern=r"^[A-Za-z0-9_:-]{16,256}$", repr=False)
    max_risk: int = Field(ge=0, le=2, strict=True)


class TelegramLink(Contract):
    operation: Literal["link"]
    binding_id: UUID
    telegram_user_id: str = Field(pattern=r"^[1-9][0-9]{0,15}$")


class TelegramRevoke(Contract):
    operation: Literal["revoke"]
    binding_id: UUID
    link_id: UUID | None = None


class TelegramNotify(Contract):
    operation: Literal["request"]
    binding_id: UUID
    revision_id: UUID
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


_COMMAND = TypeAdapter(
    Annotated[
        TelegramInstall | TelegramLink | TelegramRevoke | TelegramNotify,
        Field(discriminator="operation"),
    ]
)


@dataclass(frozen=True, repr=False)
class ComposedTelegramGateway:
    connection_factory: Callable[[], Connection] = field(repr=False)
    secrets_store: OpenBaoTelegramSecrets
    recovery_authority: OpenBaoRecoveryAuthority
    dashboard_origin: str
    api_origin: str
    egress_factory: Callable[[UUID], SharedEgressProvider] = field(repr=False)
    secret_options: dict = field(default_factory=dict, repr=False)
    recovery_options: dict = field(default_factory=dict, repr=False)

    async def availability(self) -> bool:
        try:
            await self.recovery_authority.current_generation(**self.recovery_options)
            return True
        except Exception:
            return False

    async def service(self, connection: Connection) -> TelegramService:
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        return TelegramService(
            connection,
            self.secrets_store,
            self.dashboard_origin,
            self.api_origin,
            generation.value,
            self.secret_options,
        )

    async def read(self, session_token: str, site_id: UUID) -> dict:
        with self.connection_factory() as connection:
            service = await self.service(connection)
            row = service._one(
                "read_telegram_binding",
                hash_session_token(session_token),
                site_id,
                service.recovery_generation,
            )
            if row is None:
                return {"availability": "unbound"}
            if row[5] == "denied":
                raise TelegramRejected("TELEGRAM_AUTHORITY_DENIED")
            if row[5] == "unavailable":
                row = (*row[:5], "failed")
            return dict(
                zip(
                    (
                        "binding_id",
                        "bot_username",
                        "max_risk",
                        "link_id",
                        "telegram_user_id",
                        "availability",
                    ),
                    row,
                    strict=True,
                )
            )

    async def control(self, session_token: str, site_id: UUID, command) -> dict:
        with self.connection_factory() as connection:
            service = await self.service(connection)
            args = {"session_token": session_token, "site_id": site_id}
            if isinstance(command, TelegramInstall):
                return {
                    "binding_id": await service.install(
                        **args,
                        bot_token=command.bot_token,
                        max_risk=command.max_risk,
                        egress=self.egress_factory(site_id),
                    )
                }
            if isinstance(command, TelegramLink):
                return service.begin_link(
                    **args, binding_id=command.binding_id, telegram_user_id=command.telegram_user_id
                )
            if isinstance(command, TelegramRevoke):
                return await service.revoke(
                    **args,
                    binding_id=command.binding_id,
                    link_id=command.link_id,
                    egress=self.egress_factory(site_id) if command.link_id is None else None,
                )
            outbox_id = service.queue_approval(
                **args,
                binding_id=command.binding_id,
                revision_id=command.revision_id,
                revision_sha256=command.revision_sha256,
            )
            return {
                "outbox_id": outbox_id,
                "state": await service.deliver(
                    binding_id=command.binding_id,
                    outbox_id=outbox_id,
                    egress=self.egress_factory(site_id),
                ),
            }

    async def interact(self, binding_id: UUID, body: bytes, secret_header: str) -> dict:
        with self.connection_factory() as connection:
            service = await self.service(connection)
            result = await service.interact(
                binding_id=binding_id, body=body, secret_header=secret_header
            )
            if result["reply_outbox_id"] is not None:
                context = service._one("telegram_ingress_secret", binding_id)
                result["reply_state"] = await service.deliver(
                    binding_id=binding_id,
                    outbox_id=result["reply_outbox_id"],
                    egress=self.egress_factory(context[1]),
                )
            return result


async def _body(request: Request, maximum: int) -> bytes:
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > maximum:
            raise TelegramRejected("TELEGRAM_BODY_REJECTED")
    return bytes(body)


def mount_telegram_http(application: FastAPI, gateway: ComposedTelegramGateway | None) -> None:
    def unavailable():
        return JSONResponse(
            status_code=503, content={"availability": "unavailable", "code": "TELEGRAM_UNAVAILABLE"}
        )

    @application.get("/v1/sites/{site_id}/telegram", tags=["connectors"])
    async def read(request: Request, site_id: UUID):
        if gateway is None or not await gateway.availability():
            return unavailable()
        try:
            return JSONResponse(
                content=jsonable_encoder(
                    await gateway.read(exact_cookie(request, SESSION_COOKIE_NAME), site_id)
                )
            )
        except Exception:
            return JSONResponse(status_code=401, content={"code": "TELEGRAM_READ_DENIED"})

    @application.post("/v1/sites/{site_id}/telegram", tags=["connectors"])
    async def control(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        if gateway is None or not await gateway.availability():
            return unavailable()
        try:
            if request.headers.get("content-type") != "application/json":
                raise ValueError
            command = _COMMAND.validate_json(await _body(request, 4096), strict=True)
        except (ValueError, TelegramRejected):
            return JSONResponse(status_code=422, content={"code": "TELEGRAM_COMMAND_REJECTED"})
        try:
            return JSONResponse(
                content=jsonable_encoder(
                    await gateway.control(proof.session_token, site_id, command)
                )
            )
        except Exception:
            return JSONResponse(status_code=403, content={"code": "TELEGRAM_COMMAND_DENIED"})

    @application.post("/v1/telegram/{binding_id}/webhook", tags=["connectors"])
    async def interact(request: Request, binding_id: UUID):
        if gateway is None or not await gateway.availability():
            return unavailable()
        try:
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                raise TelegramRejected("TELEGRAM_PAYLOAD_REJECTED")
            result = await gateway.interact(
                binding_id,
                await _body(request, 32768),
                request.headers.get("x-telegram-bot-api-secret-token", "")
                if len(request.headers.getlist("x-telegram-bot-api-secret-token")) == 1
                else "",
            )
            return JSONResponse(content=jsonable_encoder(result))
        except TelegramRejected:
            return JSONResponse(status_code=401, content={"code": "TELEGRAM_CALLBACK_REJECTED"})
        except Exception:
            return unavailable()
