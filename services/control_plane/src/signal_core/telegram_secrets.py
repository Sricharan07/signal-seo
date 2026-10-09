"""OpenBao-only bot credentials and webhook secrets, never a token-bearing URL."""

import re
from dataclasses import dataclass, field
from uuid import UUID

from signal_core.connector_secrets import kv_document
from signal_core.crawl_http import TelegramBotCredential
from signal_core.openbao_http import (
    json_document,
    request,
    valid_base_url,
    valid_mount,
    valid_token,
)
from signal_core.telegram_protocol import TelegramRejected


@dataclass(frozen=True, repr=False)
class OpenBaoTelegramSecrets:
    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-telegram"

    def __post_init__(self) -> None:
        if (
            not valid_base_url(self.base_url)
            or not valid_token(self.token)
            or not valid_mount(self.mount)
        ):
            raise ValueError("Invalid OpenBao Telegram configuration.")

    async def store_bot(
        self, binding_id: UUID, bot_token: str, webhook_secret: str, **options
    ) -> str:
        TelegramBotCredential(bot_token)
        if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", webhook_secret) is None:
            raise TelegramRejected("TELEGRAM_SECRET_REJECTED")
        reference = "secret://telegram/" + str(binding_id)
        response = await self._request(
            "POST",
            "/data/bots/" + str(_reference(reference)),
            payload={
                "options": {"cas": 0},
                "data": {"bot_token": bot_token, "webhook_secret": webhook_secret},
            },
            **options,
        )
        try:
            if json_document(response)["data"]["version"] != 1:
                raise ValueError
        except Exception:
            raise TelegramRejected("TELEGRAM_SECRET_WRITE_UNCONFIRMED") from None
        return reference

    async def bot(self, reference: str, **options) -> dict:
        response = await self._request("GET", "/data/bots/" + str(_reference(reference)), **options)
        try:
            data, _ = kv_document(response, minimum_version=1, require_deletion=True)
            if (
                set(data) != {"bot_token", "webhook_secret"}
                or re.fullmatch(r"[A-Za-z0-9_-]{32,256}", data["webhook_secret"]) is None
            ):
                raise ValueError
            TelegramBotCredential(data["bot_token"])
            return data
        except Exception:
            raise TelegramRejected("TELEGRAM_UNAVAILABLE") from None

    async def destroy_bot(self, reference: str, **options) -> None:
        await self._request("DELETE", "/metadata/bots/" + str(_reference(reference)), **options)

    async def _request(self, method: str, path: str, *, payload=None, **options):
        try:
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method=method,
                path="/" + self.mount + path,
                payload=payload,
                **options,
            )
            if response.status_code != (204 if method == "DELETE" else 200):
                raise ValueError
            return response
        except Exception:
            raise TelegramRejected("TELEGRAM_UNAVAILABLE") from None


def _reference(value: str) -> UUID:
    try:
        prefix, identifier = value.rsplit("/", 1)
        result = UUID(identifier)
        if prefix != "secret://telegram" or str(result) != identifier or result.version != 4:
            raise ValueError
        return result
    except Exception:
        raise TelegramRejected("TELEGRAM_REFERENCE_REJECTED") from None
