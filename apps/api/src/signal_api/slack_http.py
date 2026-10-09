"""Same-origin Slack setup and independently signed, bounded callback ingress."""

import json
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg import Connection
from pydantic import Field, TypeAdapter
from signal_core.json_objects import unique_object
from signal_core.recovery_authority import OpenBaoRecoveryAuthority
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import SharedEgressProvider
from signal_core.slack_binding import SlackService
from signal_core.slack_protocol import SlackRejected
from signal_core.slack_secrets import OpenBaoSlackSecrets

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class SlackInstall(Contract):
    operation: Literal["install"]
    workspace_id: str = Field(pattern=r"^T[A-Z0-9]{7,63}$")
    channel_id: str = Field(pattern=r"^[CG][A-Z0-9]{7,63}$")
    max_risk: int = Field(ge=0, le=2, strict=True)


class SlackComplete(Contract):
    operation: Literal["complete"]
    attempt_id: UUID
    state: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    code: str = Field(min_length=1, max_length=2048)


class SlackLink(Contract):
    operation: Literal["link"]
    binding_id: UUID
    slack_user_id: str = Field(pattern=r"^[UW][A-Z0-9]{7,63}$")


class SlackRevoke(Contract):
    operation: Literal["revoke"]
    binding_id: UUID
    link_id: UUID | None = None


class SlackNotify(Contract):
    operation: Literal["request"]
    binding_id: UUID
    revision_id: UUID
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_id: str = Field(pattern=r"^[CGUW][A-Z0-9]{7,63}$")


_COMMAND = TypeAdapter(
    Annotated[
        SlackInstall | SlackComplete | SlackLink | SlackRevoke | SlackNotify,
        Field(discriminator="operation"),
    ]
)


@dataclass(frozen=True, repr=False)
class ComposedSlackGateway:
    connection_factory: Callable[[], Connection] = field(repr=False)
    secrets_store: OpenBaoSlackSecrets
    recovery_authority: OpenBaoRecoveryAuthority
    dashboard_origin: str
    egress_factory: Callable[[str, UUID, str], AbstractContextManager[SharedEgressProvider]] = (
        field(repr=False)
    )
    secret_options: dict = field(default_factory=dict, repr=False)
    recovery_options: dict = field(default_factory=dict, repr=False)

    async def availability(self) -> bool:
        try:
            await self.secrets_store.client(**self.secret_options)
            return True
        except SlackRejected:
            return False

    async def service(self, connection: Connection) -> SlackService:
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        return SlackService(
            connection,
            self.secrets_store,
            self.dashboard_origin,
            generation.value,
            self.secret_options,
        )

    async def read(self, session_token: str, site_id: UUID) -> dict:
        with self.connection_factory() as connection:
            service = await self.service(connection)
            row = service._one(
                "read_slack_binding",
                hash_session_token(session_token),
                site_id,
                service.recovery_generation,
            )
            if row is None:
                return {"availability": "unbound"}
            if row[6] == "denied":
                raise SlackRejected("SLACK_AUTHORITY_DENIED")
            return dict(
                zip(
                    (
                        "binding_id",
                        "workspace_id",
                        "channel_id",
                        "max_risk",
                        "link_id",
                        "slack_user_id",
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
            if isinstance(command, SlackInstall):
                return await service.begin_install(
                    **args,
                    workspace_id=command.workspace_id,
                    channel_id=command.channel_id,
                    max_risk=command.max_risk,
                )
            needs_egress = not isinstance(command, SlackRevoke) or command.link_id is None
            with (
                self.egress_factory(session_token, site_id, service.recovery_generation)
                if needs_egress
                else nullcontext(None)
            ) as egress:
                return await self._connected_control(service, args, command, egress)

    async def _connected_control(self, service, args, command, egress):
        if isinstance(command, SlackComplete):
            return {
                "binding_id": await service.complete_install(
                    **args,
                    attempt_id=command.attempt_id,
                    state=command.state,
                    code=command.code,
                    egress=egress,
                )
            }
        if isinstance(command, SlackRevoke):
            return await service.revoke(
                **args,
                binding_id=command.binding_id,
                link_id=command.link_id,
                egress=egress,
            )
        if isinstance(command, SlackLink):
            outbox_id = service.begin_link(
                **args, binding_id=command.binding_id, slack_user_id=command.slack_user_id
            )
            return {
                "outbox_id": outbox_id,
                "state": await service.deliver(
                    binding_id=command.binding_id,
                    outbox_id=outbox_id,
                    egress=egress,
                ),
            }
        outbox_id = service.queue_approval(
            **args,
            binding_id=command.binding_id,
            revision_id=command.revision_id,
            revision_sha256=command.revision_sha256,
            channel_id=command.channel_id,
        )
        return {
            "outbox_id": outbox_id,
            "state": await service.deliver(
                binding_id=command.binding_id,
                outbox_id=outbox_id,
                egress=egress,
            ),
        }

    async def interact(self, binding_id: UUID, body: bytes, timestamp: str, signature: str) -> dict:
        with self.connection_factory() as connection:
            service = await self.service(connection)
            return await service.interact(
                binding_id=binding_id, body=body, timestamp=timestamp, signature=signature
            )


async def _body(request: Request, maximum: int) -> bytes:
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > maximum:
            raise SlackRejected("SLACK_BODY_REJECTED")
    return bytes(body)


def mount_slack_http(application: FastAPI, gateway: ComposedSlackGateway | None) -> None:
    application.state.browser_slack = gateway

    def unavailable():
        return JSONResponse(
            status_code=503, content={"availability": "unavailable", "code": "SLACK_UNAVAILABLE"}
        )

    @application.get("/v1/sites/{site_id}/slack", tags=["connectors"])
    async def read(request: Request, site_id: UUID):
        gateway = application.state.browser_slack
        if gateway is None or not await gateway.availability():
            return unavailable()
        try:
            token = exact_cookie(request, SESSION_COOKIE_NAME)
            return JSONResponse(content=jsonable_encoder(await gateway.read(token, site_id)))
        except Exception:
            return JSONResponse(status_code=401, content={"code": "SLACK_READ_DENIED"})

    @application.post("/v1/sites/{site_id}/slack", tags=["connectors"])
    async def control(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        gateway = application.state.browser_slack
        if gateway is None or not await gateway.availability():
            return unavailable()
        try:
            if request.headers.getlist("content-type") != [
                "application/json"
            ] or request.headers.getlist("content-encoding"):
                raise ValueError

            document = json.loads(await _body(request, 4096), object_pairs_hook=unique_object)
            command = _COMMAND.validate_json(json.dumps(document), strict=True)
        except (ValueError, SlackRejected):
            return JSONResponse(status_code=422, content={"code": "SLACK_COMMAND_REJECTED"})
        try:
            return JSONResponse(
                content=jsonable_encoder(
                    await gateway.control(proof.session_token, site_id, command)
                )
            )
        except Exception:
            return JSONResponse(status_code=403, content={"code": "SLACK_COMMAND_DENIED"})

    @application.post("/v1/slack/{binding_id}/interactivity", tags=["connectors"])
    async def interact(request: Request, binding_id: UUID):
        gateway = application.state.browser_slack
        if gateway is None or not await gateway.availability():
            return unavailable()
        try:
            if (
                request.headers.get("content-type", "").split(";", 1)[0]
                != "application/x-www-form-urlencoded"
            ):
                raise SlackRejected("SLACK_PAYLOAD_REJECTED")
            if any(
                len(request.headers.getlist(key)) != 1
                for key in ("x-slack-signature", "x-slack-request-timestamp")
            ):
                raise SlackRejected("SLACK_SIGNATURE_REJECTED")
            body = await _body(request, 32768)
            result = await gateway.interact(
                binding_id,
                body,
                request.headers["x-slack-request-timestamp"],
                request.headers["x-slack-signature"],
            )
            return JSONResponse(content=jsonable_encoder(result))
        except SlackRejected:
            return JSONResponse(status_code=401, content={"code": "SLACK_CALLBACK_REJECTED"})
        except Exception:
            return unavailable()
