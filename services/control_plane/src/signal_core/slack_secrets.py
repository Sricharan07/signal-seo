"""OpenBao KV-v2 Slack credentials; PostgreSQL receives opaque references only."""

import re
import ssl
from dataclasses import dataclass, field
from uuid import UUID

import httpx2

from signal_core.connector_secrets import kv_document
from signal_core.openbao_http import (
    OpenBaoDocumentError,
    OpenBaoTlsConfigurationError,
    OpenBaoTransportError,
    json_document,
    request,
    valid_base_url,
    valid_mount,
    valid_token,
)
from signal_core.slack_protocol import SlackRejected


@dataclass(frozen=True, repr=False)
class OpenBaoSlackSecrets:
    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-slack"

    def __post_init__(self) -> None:
        if (
            not valid_base_url(self.base_url)
            or not valid_token(self.token)
            or not valid_mount(self.mount)
        ):
            raise ValueError("Invalid OpenBao Slack configuration.")

    async def client(self, **options) -> dict:
        data = await self._read("client", **options)
        if set(data) != {"client_id", "client_secret", "signing_secret"}:
            raise SlackRejected("SLACK_UNAVAILABLE")
        if (
            not isinstance(data["client_id"], str)
            or re.fullmatch(r"[0-9]{1,32}\.[0-9]{1,32}", data["client_id"]) is None
        ):
            raise SlackRejected("SLACK_UNAVAILABLE")
        return data

    async def store_bot(self, binding_id: UUID, value: str, **options) -> str:
        reference = f"secret://slack/{binding_id}"
        await self._request(
            "POST",
            f"/{self.mount}/data/bots/{_reference(reference)}",
            payload={"options": {"cas": 0}, "data": {"bot_token": _secret(value)}},
            **options,
        )
        return reference

    async def bot(self, reference: str, **options) -> str:
        data = await self._read(f"bots/{_reference(reference)}", **options)
        if set(data) != {"bot_token"}:
            raise SlackRejected("SLACK_UNAVAILABLE")
        return data["bot_token"]

    async def destroy_bot(self, reference: str, **options) -> None:
        await self._request(
            "DELETE", f"/{self.mount}/metadata/bots/{_reference(reference)}", **options
        )

    async def _read(self, path: str, **options) -> dict:
        response = await self._request("GET", f"/{self.mount}/data/{path}", **options)
        try:
            data, _ = kv_document(response, minimum_version=1, require_deletion=True)
            for key, value in data.items():
                if path == "client" and key == "client_id":
                    continue
                _secret(value)
            return data
        except (KeyError, TypeError, ValueError, OpenBaoDocumentError):
            raise SlackRejected("SLACK_UNAVAILABLE") from None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ):
        try:
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method=method,
                path=path,
                payload=payload,
                transport=transport,
                verify=verify,
            )
        except (OpenBaoTlsConfigurationError, OpenBaoTransportError):
            raise SlackRejected("SLACK_UNAVAILABLE") from None
        if response.status_code != (204 if method == "DELETE" else 200):
            raise SlackRejected("SLACK_UNAVAILABLE")
        if method == "POST":
            try:
                if json_document(response)["data"]["version"] != 1:
                    raise ValueError
            except (KeyError, TypeError, ValueError, OpenBaoDocumentError):
                raise SlackRejected("SLACK_SECRET_WRITE_UNCONFIRMED") from None
        return response


def _secret(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[!-~]{16,4096}", value) is None:
        raise SlackRejected("SLACK_SECRET_REJECTED")
    return value


def _reference(value: str) -> UUID:
    try:
        prefix, identifier = value.rsplit("/", 1)
        result = UUID(identifier)
        if prefix != "secret://slack" or str(result) != identifier or result.version != 4:
            raise ValueError
        return result
    except (ValueError, AttributeError):
        raise SlackRejected("SLACK_REFERENCE_REJECTED") from None
