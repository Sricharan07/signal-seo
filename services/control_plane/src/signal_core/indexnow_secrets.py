"""Authoritative per-site IndexNow key generations in immutable OpenBao KV v2."""

import ssl
from dataclasses import dataclass, field
from uuid import UUID

import httpx2

from signal_core.indexnow_protocol import generate_indexnow_key, valid_indexnow_key
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


class IndexNowSecretUnavailable(Exception):
    def __init__(self) -> None:
        super().__init__("INDEXNOW_SECRET_UNAVAILABLE")


@dataclass(frozen=True)
class OpenBaoIndexNowKeys:
    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-indexnow"

    def __post_init__(self) -> None:
        if (
            not valid_base_url(self.base_url)
            or not valid_token(self.token)
            or not valid_mount(self.mount)
        ):
            raise ValueError("A trusted OpenBao origin, token, and KV mount are required.")

    async def key(
        self,
        *,
        tenant_id: UUID,
        site_id: UUID,
        key_id: UUID,
        create: bool = False,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> str:
        if not all(isinstance(value, UUID) for value in (tenant_id, site_id, key_id)):
            raise ValueError("An exact site key identity is required.")
        path = f"/{self.mount}/data/{tenant_id}/{site_id}/{key_id}"
        try:
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method="GET",
                path=path + "?version=1",
                transport=transport,
                verify=verify,
            )
            if response.status_code == 404 and create:
                generated = generate_indexnow_key()
                written = await request(
                    base_url=self.base_url,
                    token=self.token,
                    method="POST",
                    path=path,
                    payload={"options": {"cas": 0}, "data": {"key": generated}},
                    transport=transport,
                    verify=verify,
                )
                if written.status_code not in {200, 400}:
                    raise IndexNowSecretUnavailable()
                # Read back after CAS, including a concurrent creator or response replay.
                response = await request(
                    base_url=self.base_url,
                    token=self.token,
                    method="GET",
                    path=path + "?version=1",
                    transport=transport,
                    verify=verify,
                )
            if response.status_code != 200:
                raise IndexNowSecretUnavailable()
            document = json_document(response)
            envelope = document.get("data", {})
            metadata, data = envelope.get("metadata", {}), envelope.get("data", {})
            if (
                set(data) != {"key"}
                or not valid_indexnow_key(data["key"])
                or type(metadata.get("version")) is not int
                or metadata["version"] != 1
                or metadata.get("destroyed") is not False
                or metadata.get("deletion_time") not in {None, ""}
            ):
                raise IndexNowSecretUnavailable()
            return data["key"]
        except (
            OpenBaoDocumentError,
            OpenBaoTransportError,
            OpenBaoTlsConfigurationError,
            KeyError,
            TypeError,
            AttributeError,
        ):
            raise IndexNowSecretUnavailable() from None
