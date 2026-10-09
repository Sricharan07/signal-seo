"""Telegram setup, pairing, durable delivery, and shared Inbox decision ingress."""

import asyncio
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.connector_framework import connector_row, external_revocation, require_restriction
from signal_core.crawl_http import TelegramBotCredential
from signal_core.database import _clean_transaction
from signal_core.session_tokens import hash_session_token
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider
from signal_core.telegram_protocol import (
    TelegramRejected,
    approval_message,
    code_hash,
    parse_update,
    telegram_id,
    telegram_json,
    verify_webhook_secret,
)
from signal_core.telegram_secrets import OpenBaoTelegramSecrets


@dataclass(frozen=True, repr=False)
class TelegramService:
    connection: Connection = field(repr=False)
    secrets_store: OpenBaoTelegramSecrets = field(repr=False)
    dashboard_origin: str
    api_origin: str
    recovery_generation: str
    openbao_options: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for origin in (self.dashboard_origin, self.api_origin):
            endpoint = urlsplit(origin)
            if (
                endpoint.scheme != "https"
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.path
                or endpoint.query
                or endpoint.fragment
            ):
                raise ValueError("Telegram requires exact HTTPS application origins.")

    def _one(self, function, *args):
        return connector_row(
            self.connection, function, *args, scalar=False, transaction=_clean_transaction
        )

    async def _setup_call(
        self, egress: SharedEgressProvider, *, method: str, document: dict, bot_token: str
    ):
        operation = uuid4()
        for attempt in range(7):
            try:
                return telegram_json(
                    egress,
                    method=method,
                    document=document,
                    operation_id=operation,
                    bot_token=bot_token,
                )
            except ProviderEgressUnavailable as error:
                # Only pre-dispatch deferral can retry. An unknown external write never does.
                if error.code != "EGRESS_DEFERRED" or attempt == 6:
                    raise
                await asyncio.sleep(0.25)

    async def install(
        self,
        *,
        session_token: str,
        site_id: UUID,
        bot_token: str,
        egress: SharedEgressProvider,
        max_risk: int = 2,
    ) -> UUID:
        TelegramBotCredential(bot_token)
        if type(max_risk) is not int or not 0 <= max_risk <= 2:
            raise TelegramRejected("TELEGRAM_RISK_REJECTED")
        binding, secret = uuid4(), secrets.token_urlsafe(32)
        token_hash = hash_session_token(session_token)
        if self._one(
            "prepare_telegram_binding",
            token_hash,
            site_id,
            self.recovery_generation,
            binding,
            max_risk,
        ) != ("prepared",):
            raise TelegramRejected("TELEGRAM_INSTALL_DENIED")
        try:
            await self.secrets_store.store_bot(binding, bot_token, secret, **self.openbao_options)
            bot = await self._setup_call(egress, method="getMe", document={}, bot_token=bot_token)
            if (
                not isinstance(bot, dict)
                or bot.get("is_bot") is not True
                or bot.get("can_join_groups") is not False
                or not isinstance(bot.get("username"), str)
                or re.fullmatch(r"[A-Za-z0-9_]{5,32}", bot["username"]) is None
            ):
                raise TelegramRejected("TELEGRAM_GROUP_JOINING_NOT_DISABLED")
            identifier = telegram_id(bot["id"])
            args = (
                token_hash,
                site_id,
                self.recovery_generation,
                binding,
                identifier,
                bot["username"],
            )
            if self._one("confirm_telegram_binding", *args, False) != ("confirmed",):
                raise TelegramRejected("TELEGRAM_INSTALL_DENIED")
            result = await self._setup_call(
                egress,
                method="setWebhook",
                bot_token=bot_token,
                document={
                    "url": self.api_origin + "/v1/telegram/" + str(binding) + "/webhook",
                    "secret_token": secret,
                    "allowed_updates": ["message", "callback_query"],
                    "drop_pending_updates": True,
                },
            )
            if result is not True or self._one("confirm_telegram_binding", *args, True) != (
                "confirmed",
            ):
                raise TelegramRejected("TELEGRAM_WEBHOOK_UNCONFIRMED")
            return binding
        except Exception:
            self._one("fail_telegram_binding", binding)
            # Keep the secret for explicit owner revocation of a possibly registered webhook.
            raise TelegramRejected("TELEGRAM_INSTALL_FAILED") from None

    def begin_link(
        self, *, session_token: str, site_id: UUID, binding_id: UUID, telegram_user_id: str
    ) -> dict:
        user = telegram_id(telegram_user_id)
        identifier, code = uuid4(), secrets.token_urlsafe(32)
        row = self._one(
            "begin_telegram_link",
            hash_session_token(session_token),
            site_id,
            self.recovery_generation,
            binding_id,
            identifier,
            user,
            code_hash(code),
        )
        if row is None or row[0] in {None, "denied"}:
            raise TelegramRejected("TELEGRAM_PAIRING_DENIED")
        return {
            "pairing_id": identifier,
            "pairing_url": "https://t.me/" + row[0] + "?start=" + code,
            "expires_in_seconds": 300,
        }

    def queue_approval(
        self,
        *,
        session_token: str,
        site_id: UUID,
        binding_id: UUID,
        revision_id: UUID,
        revision_sha256: str,
    ) -> UUID:
        row = self._one(
            "read_telegram_binding",
            hash_session_token(session_token),
            site_id,
            self.recovery_generation,
        )
        if row is None or row[0] != binding_id or row[4] is None or row[5] != "bound":
            raise TelegramRejected("TELEGRAM_REQUEST_DENIED")
        approve, reject = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        payload = approval_message(
            chat_id=row[4],
            revision_id=revision_id,
            revision_sha256=revision_sha256,
            summary="An exact revision needs your decision.",
            evidence_url=self.evidence_link(revision_id),
            approve_code=approve,
            reject_code=reject,
        )
        row = self._one(
            "enqueue_telegram_approval",
            hash_session_token(session_token),
            site_id,
            self.recovery_generation,
            binding_id,
            uuid4(),
            revision_id,
            bytes.fromhex(revision_sha256),
            code_hash(approve),
            code_hash(reject),
            Jsonb(payload),
        )
        if row is None or row[1] != "queued":
            raise TelegramRejected("TELEGRAM_REQUEST_DENIED")
        return row[0]

    def evidence_link(self, revision_id: UUID) -> str:
        return self.dashboard_origin + "/approvals?revision=" + str(revision_id)

    async def deliver(
        self, *, binding_id: UUID, outbox_id: UUID, egress: SharedEgressProvider
    ) -> str:
        item = self._one(
            "telegram_outbox_item", binding_id, outbox_id, self.recovery_generation, False
        )
        if item is None:
            raise TelegramRejected("TELEGRAM_DELIVERY_UNAVAILABLE")
        if item[2] != "queued":
            return item[2]
        credentials = await self.secrets_store.bot(item[1], **self.openbao_options)
        claimed = self._one(
            "telegram_outbox_item", binding_id, outbox_id, self.recovery_generation, True
        )
        if claimed is None:
            raise TelegramRejected("TELEGRAM_DELIVERY_UNAVAILABLE")
        if claimed[2] != "claimed":
            return claimed[2]
        try:
            response = telegram_json(
                egress,
                method=claimed[3],
                document=claimed[0],
                operation_id=outbox_id,
                bot_token=credentials["bot_token"],
            )
            if claimed[3] == "answerCallbackQuery":
                if response is not True:
                    raise ValueError
                # An acknowledgement has no provider message ID.
                chat, message = None, "ack"
            else:
                if response["chat"]["type"] != "private":
                    raise ValueError
                chat, message = (
                    telegram_id(response["chat"]["id"]),
                    telegram_id(response["message_id"]),
                )
            return self._one("finish_telegram_outbox", binding_id, outbox_id, chat, message)[0]
        except ProviderEgressUnavailable as error:
            if error.code == "EGRESS_DEFERRED":
                return self._one("defer_telegram_outbox", binding_id, outbox_id)[0]
            return self._one("finish_telegram_outbox", binding_id, outbox_id, None, None)[0]
        except Exception:
            return self._one("finish_telegram_outbox", binding_id, outbox_id, None, None)[0]

    async def interact(self, *, binding_id: UUID, body: bytes, secret_header: str) -> dict:
        body_hash = hashlib.sha256(body).digest()
        try:
            reference = self._one("telegram_ingress_secret", binding_id)
            if reference is None or reference[0] is None:
                raise TelegramRejected("TELEGRAM_UNAVAILABLE")
            credentials = await self.secrets_store.bot(reference[0], **self.openbao_options)
            verify_webhook_secret(credentials["webhook_secret"], secret_header)
        except TelegramRejected:
            self._one("audit_telegram_rejection", binding_id, body_hash, "secret_rejected")
            raise TelegramRejected("TELEGRAM_SECRET_REJECTED") from None
        try:
            update = parse_update(body)
        except TelegramRejected:
            self._one("audit_telegram_rejection", binding_id, body_hash, "payload_rejected")
            raise
        row = self._one(
            "handle_telegram_update",
            binding_id,
            self.recovery_generation,
            update.update_id,
            body_hash,
            update.kind,
            update.user_id,
            update.chat_id,
            code_hash(update.code) if update.code else None,
            update.message_id,
            update.callback_id,
            uuid4(),
            self.dashboard_origin + "/approvals",
        )
        if row is None:
            raise TelegramRejected("TELEGRAM_UPDATE_REJECTED")
        return {"outcome": row[0], "decision_id": row[1], "reply_outbox_id": row[2]}

    async def revoke(
        self,
        *,
        session_token: str,
        site_id: UUID,
        binding_id: UUID,
        egress: SharedEgressProvider | None = None,
        link_id: UUID | None = None,
    ) -> dict:
        row = self._one(
            "revoke_telegram_authority",
            hash_session_token(session_token),
            site_id,
            self.recovery_generation,
            binding_id,
            link_id,
            uuid4(),
        )
        require_restriction(
            row,
            {"revoked", "AUTHORITY_DURABILITY_PENDING"},
            TelegramRejected("TELEGRAM_REVOKE_DENIED"),
        )
        upstream = "not_required" if link_id else "not_executed"
        if link_id is None:

            async def revoke():
                if egress is not None and self._one("claim_telegram_revocation", binding_id) == (
                    True,
                ):
                    credentials = await self.secrets_store.bot(row[1], **self.openbao_options)
                    result = await self._setup_call(
                        egress,
                        method="deleteWebhook",
                        document={"drop_pending_updates": True},
                        bot_token=credentials["bot_token"],
                    )
                    if result is True:
                        self._one("finish_telegram_revocation", binding_id)
                    return "accepted" if result is True else "unknown"
                return "not_executed"

            upstream = await external_revocation(
                revoke,
                lambda: self.secrets_store.destroy_bot(row[1], **self.openbao_options),
                caught=(Exception,),
                failure="unknown",
            )
        return {"outcome": row[0], "restriction_event_id": row[2], "upstream": upstream}
