"""Webflow OAuth material lives in OpenBao KV-v2, never in application rows."""

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
from signal_core.webflow import WebflowUnavailable, secret


def reference_id(reference: str) -> UUID:
    try:
        prefix, suffix = reference.rsplit("/", 1)
        identifier = UUID(suffix)
        if prefix != "secret://webflow" or str(identifier) != suffix:
            raise ValueError
        return identifier
    except (ValueError, AttributeError):
        raise WebflowUnavailable("WEBFLOW_SECRET_REFERENCE_REJECTED") from None


@dataclass(frozen=True, repr=False)
class OpenBaoWebflowSecrets:
    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-webflow"

    def __post_init__(self):
        if (
            not valid_base_url(self.base_url)
            or not valid_token(self.token)
            or not valid_mount(self.mount)
        ):
            raise ValueError("Invalid OpenBao Webflow configuration.")

    async def client(self, **options) -> dict:
        data = await self._read("client", **options)
        if set(data) != {"client_id", "client_secret"}:
            raise WebflowUnavailable("WEBFLOW_CLIENT_UNAVAILABLE")
        return data

    async def store(self, binding_id: UUID, token: str, **options) -> str:
        reference = f"secret://webflow/{binding_id}"
        await self._request(
            "POST",
            f"/{self.mount}/data/tokens/{reference_id(reference)}",
            payload={"options": {"cas": 0}, "data": {"access_token": secret(token)}},
            **options,
        )
        return reference

    async def read(self, reference: str, **options) -> str:
        data = await self._read(f"tokens/{reference_id(reference)}", **options)
        if set(data) != {"access_token"}:
            raise WebflowUnavailable("WEBFLOW_SECRET_UNAVAILABLE")
        return secret(data["access_token"])

    async def destroy(self, reference: str, **options):
        await self._request(
            "DELETE", f"/{self.mount}/metadata/tokens/{reference_id(reference)}", **options
        )

    async def _read(self, path: str, **options):
        response = await self._request("GET", f"/{self.mount}/data/{path}", **options)
        try:
            data, _ = kv_document(response, minimum_version=1, require_deletion=True)
            for value in data.values():
                secret(value)
            return data
        except (KeyError, TypeError, ValueError, OpenBaoDocumentError):
            raise WebflowUnavailable("WEBFLOW_SECRET_UNAVAILABLE") from None

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
            if response.status_code != (204 if method == "DELETE" else 200):
                raise WebflowUnavailable("WEBFLOW_SECRET_UNAVAILABLE")
            if method == "POST" and json_document(response)["data"]["version"] != 1:
                raise WebflowUnavailable("WEBFLOW_SECRET_WRITE_UNCONFIRMED")
            return response
        except (
            OpenBaoTlsConfigurationError,
            OpenBaoTransportError,
            OpenBaoDocumentError,
            KeyError,
            TypeError,
            ValueError,
        ):
            raise WebflowUnavailable("WEBFLOW_SECRET_UNAVAILABLE") from None
