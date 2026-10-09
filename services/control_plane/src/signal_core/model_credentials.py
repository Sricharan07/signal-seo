"""Read one OpenAI API credential through a narrow OpenBao capability."""

import re
import ssl
from dataclasses import dataclass, field

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

_API_KEY = re.compile(r"sk-[A-Za-z0-9_-]{16,496}")
_TYPESAFE_API_KEY = re.compile(r"[!-~]{16,512}")


class ModelCredentialError(Exception):
    """The model credential could not be obtained without exposing secret material."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class JevCredentialError(Exception):
    """The Jev credential could not be obtained without exposing secret material."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OpenBaoModelCredential:
    """Read-only access to the single local-pilot OpenAI credential."""

    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-model"

    def __post_init__(self) -> None:
        if not valid_base_url(self.base_url):
            raise ValueError("OpenBao base URL must be an exact HTTPS origin.")
        if not valid_token(self.token):
            raise ValueError("OpenBao token is invalid.")
        if not valid_mount(self.mount):
            raise ValueError("OpenBao KV mount name is invalid.")

    async def api_key(
        self,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> str:
        """Return the validated credential without retaining provider response text."""
        try:
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method="GET",
                path=f"/{self.mount}/data/openai/default",
                transport=transport,
                verify=verify,
            )
        except OpenBaoTlsConfigurationError:
            raise ModelCredentialError("MODEL_CREDENTIAL_TLS_REJECTED") from None
        except OpenBaoTransportError:
            raise ModelCredentialError("MODEL_CREDENTIAL_UNAVAILABLE") from None
        if response.status_code != 200:
            raise ModelCredentialError("MODEL_CREDENTIAL_UNAVAILABLE")
        try:
            document = json_document(response)
        except OpenBaoDocumentError:
            raise ModelCredentialError("MODEL_CREDENTIAL_UNAVAILABLE") from None
        envelope = document.get("data")
        data = envelope.get("data") if isinstance(envelope, dict) else None
        metadata = envelope.get("metadata") if isinstance(envelope, dict) else None
        key = data.get("api_key") if isinstance(data, dict) else None
        if (
            not isinstance(data, dict)
            or set(data) != {"api_key"}
            or not isinstance(metadata, dict)
            or metadata.get("version") != 1
            or metadata.get("destroyed") is not False
            or metadata.get("deletion_time") not in {None, ""}
            or not isinstance(key, str)
            or _API_KEY.fullmatch(key) is None
        ):
            raise ModelCredentialError("MODEL_CREDENTIAL_UNAVAILABLE")
        return key


@dataclass(frozen=True)
class OpenBaoJevCredential:
    """Read-only access to the TypeSafe Jev credential for the decision boundary."""

    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-decision"

    def __post_init__(self) -> None:
        if not valid_base_url(self.base_url):
            raise ValueError("OpenBao base URL must be an exact HTTPS origin.")
        if not valid_token(self.token):
            raise ValueError("OpenBao token is invalid.")
        if not valid_mount(self.mount):
            raise ValueError("OpenBao KV mount name is invalid.")

    async def api_key(
        self,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> str:
        """Return the validated TypeSafe credential without retaining response text."""
        try:
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method="GET",
                path=f"/{self.mount}/data/typesafe/default",
                transport=transport,
                verify=verify,
            )
        except OpenBaoTlsConfigurationError:
            raise JevCredentialError("JEV_CREDENTIAL_TLS_REJECTED") from None
        except OpenBaoTransportError:
            raise JevCredentialError("JEV_CREDENTIAL_UNAVAILABLE") from None
        if response.status_code != 200:
            raise JevCredentialError("JEV_CREDENTIAL_UNAVAILABLE")
        try:
            document = json_document(response)
        except OpenBaoDocumentError:
            raise JevCredentialError("JEV_CREDENTIAL_UNAVAILABLE") from None
        envelope = document.get("data")
        data = envelope.get("data") if isinstance(envelope, dict) else None
        metadata = envelope.get("metadata") if isinstance(envelope, dict) else None
        key = data.get("api_key") if isinstance(data, dict) else None
        if (
            not isinstance(data, dict)
            or set(data) != {"api_key"}
            or not isinstance(metadata, dict)
            or metadata.get("version") != 1
            or metadata.get("destroyed") is not False
            or metadata.get("deletion_time") not in {None, ""}
            or not isinstance(key, str)
            or not key.isascii()
            or _TYPESAFE_API_KEY.fullmatch(key) is None
        ):
            raise JevCredentialError("JEV_CREDENTIAL_UNAVAILABLE")
        return key
