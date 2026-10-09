"""Current-owner preferences and the existing shared-egress chat send boundary."""

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from psycopg import Connection

from signal_core.chat_messages import render_chat
from signal_core.database import _clean_transaction
from signal_core.email_messages import dashboard_origin
from signal_core.recovery_authority import RecoveryGeneration
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider
from signal_core.slack_protocol import slack_id, slack_json
from signal_core.slack_secrets import OpenBaoSlackSecrets
from signal_core.telegram_protocol import telegram_id, telegram_json
from signal_core.telegram_secrets import OpenBaoTelegramSecrets


def configure_chat_reports(
    connection: Connection, *, provider: str, origin: str, daily_cap: int
) -> None:
    if (
        provider not in {"slack", "telegram"}
        or type(daily_cap) is not int
        or not 1 <= daily_cap <= 100
    ):
        raise ValueError("A closed provider and bounded daily cap are required.")
    with _clean_transaction(connection):
        connection.execute(
            "SELECT control.configure_chat_reports(%s,%s,%s)",
            (provider, dashboard_origin(origin), daily_cap),
        )


@dataclass(frozen=True, repr=False)
class ChatReports:
    connection: Connection = field(repr=False)
    recovery_generation: str

    def _one(self, function: str, *args):
        with _clean_transaction(self.connection):
            return self.connection.execute(
                f"SELECT control.{function}(" + ",".join(["%s"] * len(args)) + ")", args
            ).fetchone()[0]

    def read(self, session_token: str, site_id: UUID) -> dict:
        state = self._one(
            "read_chat_reports",
            hash_session_token(session_token),
            site_id,
            validate_recovery_generation(self.recovery_generation),
        )
        if state is None:
            raise PermissionError("CHAT_REPORT_AUTHORITY_DENIED")
        return state

    def preference(self, session_token: str, site_id: UUID, *, channel: str, enabled: bool) -> str:
        if channel not in {"slack_channel", "slack_dm", "telegram"} or type(enabled) is not bool:
            raise ValueError("A closed channel preference is required.")
        state = self._one(
            "set_chat_report_preference",
            hash_session_token(session_token),
            site_id,
            validate_recovery_generation(self.recovery_generation),
            channel,
            enabled,
            uuid4(),
        )
        if state == "denied":
            raise PermissionError("CHAT_REPORT_AUTHORITY_DENIED")
        return state


@dataclass(frozen=True, repr=False)
class ChatReportSender(ChatReports):
    slack_secrets: OpenBaoSlackSecrets | None = field(default=None, repr=False)
    telegram_secrets: OpenBaoTelegramSecrets | None = field(default=None, repr=False)
    slack_options: dict = field(default_factory=dict, repr=False)
    telegram_options: dict = field(default_factory=dict, repr=False)

    def inspect(self, identifier: UUID) -> dict | None:
        return self._one(
            "chat_report_item",
            identifier,
            validate_recovery_generation(self.recovery_generation),
            None,
        )

    async def credentials(self, item: dict) -> str:
        if item["channel"] == "telegram":
            if self.telegram_secrets is None:
                raise RuntimeError("CHAT_PROVIDER_UNAVAILABLE")
            return (
                await self.telegram_secrets.bot(item["secret_reference"], **self.telegram_options)
            )["bot_token"]
        if self.slack_secrets is None:
            raise RuntimeError("CHAT_PROVIDER_UNAVAILABLE")
        return await self.slack_secrets.bot(item["secret_reference"], **self.slack_options)

    async def deliver(self, identifier: UUID, *, egress: SharedEgressProvider) -> str:
        item = self.inspect(identifier)
        if item is None:
            return "unavailable"
        if item["state"] not in {"queued", "retry"}:
            return item["state"]
        try:
            rendered = render_chat(
                channel=item["channel"],
                destination=item["destination"],
                site_id=UUID(item["site_id"]),
                origin=item["origin"],
                category=item["category"],
                projection=item["projection"],
            )
        except (ValueError, TypeError, KeyError):
            return self._one("finish_chat_report", identifier, None, "render_rejected")
        try:
            token = await self.credentials(item)
        except Exception:
            return self._one("finish_chat_report", identifier, None, "provider_unavailable")
        # The claim rechecks authority, opt-out, exact binding and site-wide caps
        # after the credential read. No database transaction spans provider I/O.
        claimed = self._one(
            "chat_report_item", identifier, self.recovery_generation, rendered.sha256
        )
        if claimed is None:
            return "unavailable"
        if claimed["state"] != "claimed":
            return claimed["state"]
        outcome = "unknown"
        try:
            if item["channel"] == "telegram":
                response = telegram_json(
                    egress,
                    method="sendMessage",
                    document=rendered.payload,
                    operation_id=identifier,
                    bot_token=token,
                )
                if (
                    response["chat"]["type"] != "private"
                    or telegram_id(response["chat"]["id"]) != item["destination"]
                ):
                    raise ValueError("CHAT_RESPONSE_REJECTED")
                telegram_id(response["message_id"])
            else:
                response = slack_json(
                    egress,
                    method="chat.postMessage",
                    document=rendered.payload,
                    operation_id=identifier,
                    bot_token=token,
                )
                channel = slack_id(response.get("channel"), "CDG")
                if (
                    item["channel"] == "slack_channel"
                    and channel != item["destination"]
                    or item["channel"] == "slack_dm"
                    and not channel.startswith("D")
                    or not isinstance(response.get("ts"), str)
                ):
                    raise ValueError("CHAT_RESPONSE_REJECTED")
            outcome = "accepted"
        except ProviderEgressUnavailable as error:
            if error.code == "EGRESS_DEFERRED":
                outcome = "deferred"
        except Exception:
            # A timeout, malformed reply or provider rejection is not proof that
            # a message was never sent. There is no blind retry of external writes.
            pass
        return self._one("finish_chat_report", identifier, rendered.sha256, outcome)

    async def deliver_due(self, egress_factory, *, limit: int = 20) -> list[str]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("A bounded batch is required.")
        with _clean_transaction(self.connection):
            identifiers = self.connection.execute(
                "SELECT control.due_chat_reports(%s)", (limit,)
            ).fetchall()
        results = []
        for (identifier,) in identifiers:
            item = self.inspect(identifier)
            if item is None or item["state"] not in {"queued", "retry"}:
                results.append("unavailable" if item is None else item["state"])
                continue
            # The host composes the existing site/provider-scoped gateway, not a
            # general HTTP client. No send or queue route is exposed to browsers.
            gateway = egress_factory(UUID(item["site_id"]), item["channel"])
            results.append(await self.deliver(identifier, egress=gateway))
        return results


@dataclass(frozen=True, repr=False)
class ChatDelivery:
    connection_factory: object
    recovery_source: object
    scope: object
    egress_factory: object
    slack_secrets: object | None = None
    telegram_secrets: object | None = None

    @property
    def configured(self):
        return self.egress_factory is not None and (
            self.slack_secrets is not None or self.telegram_secrets is not None
        )

    async def queue_health(self, scope, generation):
        if scope != self.scope:
            raise PermissionError("Health chat scope mismatch.")
        current = await self.recovery_source.current_generation()
        if not isinstance(current, RecoveryGeneration) or current.value != generation:
            raise PermissionError("Health chat recovery changed.")

        def queue():
            with self.connection_factory() as connection, _clean_transaction(connection):
                return connection.execute(
                    "SELECT control.queue_health_chat_alerts(%s,%s,%s)",
                    (scope.tenant_id, scope.site_id, generation),
                ).fetchone()[0]

        await asyncio.to_thread(queue)

    async def deliver(self, identifiers):
        results = []
        for identifier in identifiers:
            generation = await self.recovery_source.current_generation()
            if not isinstance(generation, RecoveryGeneration):
                raise PermissionError("Chat recovery unavailable.")
            with self.connection_factory() as connection:
                sender = ChatReportSender(
                    connection, generation.value, self.slack_secrets, self.telegram_secrets
                )
                item = sender.inspect(identifier)
                if item is None or item["state"] not in {"queued", "retry"}:
                    results.append("unavailable" if item is None else item["state"])
                    continue
                if UUID(item["site_id"]) != self.scope.site_id:
                    raise PermissionError("Chat delivery site mismatch.")
                gateway = self.egress_factory(self.scope.site_id, item["channel"])
                if gateway is not None and (
                    not isinstance(gateway, SharedEgressProvider)
                    or (gateway.run.tenant_id, gateway.run.site_id)
                    != (self.scope.tenant_id, self.scope.site_id)
                ):
                    raise PermissionError("Chat egress scope mismatch.")
                results.append(await sender.deliver(identifier, egress=gateway))
        return results

    async def pump(self):
        def due():
            with self.connection_factory() as connection, _clean_transaction(connection):
                return [
                    row[0]
                    for row in connection.execute(
                        "SELECT control.due_site_chat_reports(%s,%s,%s)",
                        (self.scope.tenant_id, self.scope.site_id, 20),
                    ).fetchall()
                ]

        return await self.deliver(await asyncio.to_thread(due))
