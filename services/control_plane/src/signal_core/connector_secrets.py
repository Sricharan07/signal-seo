"""Shared OpenBao OAuth custody; adapters retain namespaces and fixed failure codes."""

import re
import ssl
from dataclasses import dataclass, field
from hmac import compare_digest
from typing import ClassVar
from uuid import UUID

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

_OAUTH_SECRET = re.compile(r"[!-~]{20,4096}")
_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")


@dataclass(frozen=True, repr=False)
class OpenBaoOAuthSecrets:
    base_url: str
    token: str = field(repr=False)
    mount: str
    provider: ClassVar[str]
    error: ClassVar[type[Exception]]
    prefix: ClassVar[str]
    refresh_label: ClassVar[str] = "REFRESH_TOKEN"
    refresh_pattern: ClassVar[re.Pattern] = _OAUTH_SECRET
    credentials_type: ClassVar[type]
    configuration_label: ClassVar[str]

    def failure(self, suffix):
        return self.error(f"{self.prefix}_{suffix}")

    def __post_init__(self) -> None:
        if (
            not valid_base_url(self.base_url)
            or not valid_token(self.token)
            or not valid_mount(self.mount)
        ):
            raise ValueError(f"Invalid OpenBao {self.configuration_label} configuration.")

    async def client_credentials(self, *, transport=None, verify=True):
        data, version = await self._read("oauth-client", transport=transport, verify=verify)
        if version < 1 or set(data) != {"client_id", "client_secret"}:
            raise self.failure("CLIENT_UNAVAILABLE")
        try:
            return self.credentials_type(data["client_id"], data["client_secret"])
        except ValueError:
            raise self.failure("CLIENT_UNAVAILABLE") from None

    async def store_verifier(
        self,
        attempt_id: UUID,
        verifier: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> None:
        identifier = _uuid4(attempt_id)
        if not isinstance(verifier, str) or _VERIFIER.fullmatch(verifier) is None:
            raise ValueError("Invalid PKCE verifier.")
        await self._write(
            f"verifiers/{identifier}",
            {"code_verifier": verifier},
            cas=0,
            transport=transport,
            verify=verify,
        )

    async def consume_verifier(
        self,
        attempt_id: UUID,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> str:
        identifier = _uuid4(attempt_id)
        data, version = await self._read(
            f"verifiers/{identifier}", transport=transport, verify=verify
        )
        verifier = data.get("code_verifier")
        if (
            version != 1
            or set(data) != {"code_verifier"}
            or not isinstance(verifier, str)
            or _VERIFIER.fullmatch(verifier) is None
        ):
            raise self.failure("VERIFIER_UNAVAILABLE")
        await self._delete(f"verifiers/{identifier}", transport=transport, verify=verify)
        return verifier

    async def destroy_verifier(
        self,
        attempt_id: UUID,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> None:
        await self._delete(f"verifiers/{_uuid4(attempt_id)}", transport=transport, verify=verify)

    async def store_refresh_token(
        self,
        attempt_id: UUID,
        refresh_token: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> str:
        identifier = _uuid4(attempt_id)
        if (
            not isinstance(refresh_token, str)
            or self.refresh_pattern.fullmatch(refresh_token) is None
        ):
            raise self.failure(self.refresh_label + "_REJECTED")
        await self._write(
            f"refresh/{identifier}",
            {"refresh_token": refresh_token},
            cas=0,
            transport=transport,
            verify=verify,
        )
        return f"secret://{self.provider}/{identifier}"

    async def refresh_token(
        self,
        secret_reference: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> str:
        identifier = self.reference_id(secret_reference)
        data, version = await self._read(
            f"refresh/{identifier}", transport=transport, verify=verify
        )
        value = data.get("refresh_token")
        if (
            version < 1
            or set(data) != {"refresh_token"}
            or not isinstance(value, str)
            or self.refresh_pattern.fullmatch(value) is None
        ):
            raise self.failure(self.refresh_label + "_UNAVAILABLE")
        return value

    async def replace_refresh_token(
        self,
        secret_reference: str,
        previous_token: str,
        rotated_token: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> None:
        identifier = self.reference_id(secret_reference)
        if (
            not isinstance(rotated_token, str)
            or self.refresh_pattern.fullmatch(rotated_token) is None
        ):
            raise self.failure(self.refresh_label + "_REJECTED")
        data, version = await self._read(
            f"refresh/{identifier}", transport=transport, verify=verify
        )
        current = data.get("refresh_token")
        if (
            set(data) != {"refresh_token"}
            or not isinstance(current, str)
            or not compare_digest(current, previous_token)
        ):
            raise self.failure(self.refresh_label + "_CONFLICT")
        await self._write(
            f"refresh/{identifier}",
            {"refresh_token": rotated_token},
            cas=version,
            transport=transport,
            verify=verify,
        )

    async def destroy_refresh_token(
        self,
        secret_reference: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> None:
        identifier = self.reference_id(secret_reference)
        await self._delete(f"refresh/{identifier}", transport=transport, verify=verify)

    async def _read(
        self,
        path: str,
        *,
        transport: httpx2.AsyncBaseTransport | None,
        verify: ssl.SSLContext | bool,
    ) -> tuple[dict, int]:
        response = await self._request(
            "GET", f"/{self.mount}/data/{path}", transport=transport, verify=verify
        )
        if response.status_code != 200:
            raise self.failure("SECRET_UNAVAILABLE")
        try:
            return kv_document(response)
        except (OpenBaoDocumentError, ValueError):
            raise self.failure("SECRET_UNAVAILABLE") from None

    async def _write(
        self,
        path: str,
        data: dict,
        cas: int,
        *,
        transport: httpx2.AsyncBaseTransport | None,
        verify: ssl.SSLContext | bool,
    ) -> None:
        response = await self._request(
            "POST",
            f"/{self.mount}/data/{path}",
            payload={"options": {"cas": cas}, "data": data},
            transport=transport,
            verify=verify,
        )
        if response.status_code != 200:
            raise self.failure("SECRET_WRITE_FAILED")
        try:
            version = json_document(response).get("data", {}).get("version")
        except (OpenBaoDocumentError, AttributeError):
            raise self.failure("SECRET_WRITE_FAILED") from None
        if version != cas + 1:
            raise self.failure("SECRET_WRITE_FAILED")

    async def _delete(
        self,
        path: str,
        *,
        transport: httpx2.AsyncBaseTransport | None,
        verify: ssl.SSLContext | bool,
    ) -> None:
        response = await self._request(
            "DELETE", f"/{self.mount}/metadata/{path}", transport=transport, verify=verify
        )
        if response.status_code != 204:
            raise self.failure("SECRET_DELETE_UNCONFIRMED")

    async def _request(self, method: str, path: str, **kwargs):
        try:
            return await request(
                base_url=self.base_url, token=self.token, method=method, path=path, **kwargs
            )
        except (OpenBaoTlsConfigurationError, OpenBaoTransportError):
            raise self.failure("SECRET_UNAVAILABLE") from None

    def reference_id(self, value):
        return reference_id(value, self.provider, self.failure(self.refresh_label + "_UNAVAILABLE"))


def reference_id(value, provider, error, *, require_v4=True):
    if not isinstance(value, str):
        raise error
    match = re.fullmatch(r"secret://" + re.escape(provider) + r"/([0-9a-f-]{36})", value)
    if match is None:
        raise error
    try:
        identifier = UUID(match.group(1))
    except ValueError:
        raise error from None
    if (require_v4 and identifier.version != 4) or str(identifier) != match.group(1):
        raise error
    return identifier


def kv_document(response, *, minimum_version=None, exact_version=None, require_deletion=False):
    """Parse active KV-v2 metadata; adapters still validate fields and credentials."""
    envelope = json_document(response).get("data")
    data = envelope.get("data") if isinstance(envelope, dict) else None
    metadata = envelope.get("metadata") if isinstance(envelope, dict) else None
    if (
        not isinstance(data, dict)
        or not isinstance(metadata, dict)
        or metadata.get("destroyed") is not False
        or metadata.get("deletion_time") not in {None, ""}
        or (require_deletion and "deletion_time" not in metadata)
        or type(metadata.get("version")) is not int
        or (minimum_version is not None and metadata["version"] < minimum_version)
        or (exact_version is not None and metadata["version"] != exact_version)
    ):
        raise ValueError("Inactive or malformed KV-v2 document.")
    return data, metadata["version"]


def _uuid4(value):
    if not isinstance(value, UUID) or value.version != 4:
        raise ValueError("A UUIDv4 attempt ID is required.")
    return value
