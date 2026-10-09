"""Independent OpenBao-only credentials for assistant search providers."""

import re
import ssl
from dataclasses import dataclass, field
from typing import Literal

import httpx2

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

AssistantProvider = Literal["openai", "perplexity", "gemini"]
PROVIDERS = ("openai", "perplexity", "gemini")
_KEY = re.compile(r"[!-~]{16,512}")


class AssistantCredentialUnavailable(Exception):
    def __init__(self, code: str = "ASSISTANT_PROVIDER_UNAVAILABLE") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class OpenBaoAssistantCredentials:
    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-assistants"

    def __post_init__(self) -> None:
        if (
            not valid_base_url(self.base_url)
            or not valid_token(self.token)
            or not valid_mount(self.mount)
        ):
            raise ValueError("Invalid OpenBao assistant configuration.")

    async def api_key(
        self,
        provider: AssistantProvider,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> str:
        if provider not in PROVIDERS:
            raise ValueError("Unsupported assistant provider.")
        try:
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method="GET",
                path=f"/{self.mount}/data/{provider}/default",
                transport=transport,
                verify=verify,
            )
        except (OpenBaoTlsConfigurationError, OpenBaoTransportError):
            raise AssistantCredentialUnavailable() from None
        if response.status_code != 200:
            raise AssistantCredentialUnavailable()
        try:
            envelope = json_document(response).get("data")
        except OpenBaoDocumentError:
            raise AssistantCredentialUnavailable() from None
        data = envelope.get("data") if isinstance(envelope, dict) else None
        metadata = envelope.get("metadata") if isinstance(envelope, dict) else None
        key = data.get("api_key") if isinstance(data, dict) else None
        if (
            not isinstance(data, dict)
            or set(data) != {"api_key"}
            or not isinstance(metadata, dict)
            or type(metadata.get("version")) is not int
            or metadata["version"] < 1
            or metadata.get("destroyed") is not False
            or metadata.get("deletion_time") not in {None, ""}
            or not isinstance(key, str)
            or _KEY.fullmatch(key) is None
        ):
            raise AssistantCredentialUnavailable()
        return key

    async def availability(
        self,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> dict[str, str]:
        result = {}
        for provider in PROVIDERS:
            try:
                await self.api_key(provider, transport=transport, verify=verify)
            except AssistantCredentialUnavailable:
                result[provider] = "unavailable"
            else:
                result[provider] = "configured_internal_only"
        return result
