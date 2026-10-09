"""Store and consume one-login PKCE verifiers through a narrow OpenBao KV v2 API."""

import re
import ssl
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx2

from signal_core.openbao_http import (
    OpenBaoDocumentError,
    OpenBaoResponse,
    OpenBaoTlsConfigurationError,
    OpenBaoTransportError,
    json_document,
    request,
    valid_base_url,
    valid_mount,
    valid_token,
)

_PKCE_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_REFERENCE = re.compile(
    r"secret://oidc-login/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/1"
)
_DELETE_ATTEMPTS = 2


class PkceSecretError(Exception):
    """An OpenBao PKCE operation failed without exposing secret or provider text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PkceSecretConflict(PkceSecretError):
    """A supposedly new login-attempt path already has a secret version."""


class PkceSecretUnavailable(PkceSecretError):
    """The referenced verifier does not exist or is no longer readable."""


@dataclass(frozen=True)
class OpenBaoPkceClient:
    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-ephemeral"

    def __post_init__(self) -> None:
        if not valid_base_url(self.base_url):
            raise ValueError("OpenBao base URL must be an exact HTTPS origin.")
        if not valid_token(self.token):
            raise ValueError("OpenBao token is invalid.")
        if not valid_mount(self.mount):
            raise ValueError("OpenBao KV mount name is invalid.")

    async def store_verifier(
        self,
        *,
        attempt_id: object,
        code_verifier: object,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> str:
        """Create immutable KV version 1 at a UUID-derived, non-secret reference."""
        identifier = _validate_attempt_id(attempt_id)
        verifier = _validate_verifier(code_verifier)
        try:
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method="POST",
                path=f"/{self.mount}/data/oidc-login/{identifier}",
                payload={"options": {"cas": 0}, "data": {"code_verifier": verifier}},
                transport=transport,
                verify=verify,
            )
        except OpenBaoTlsConfigurationError:
            raise PkceSecretError("OPENBAO_TLS_CONFIGURATION_REJECTED") from None
        except OpenBaoTransportError:
            raise PkceSecretError("PKCE_SECRET_WRITE_FAILED") from None
        if response.status_code == 400:
            raise PkceSecretConflict("PKCE_SECRET_CONFLICT")
        if response.status_code != 200:
            raise PkceSecretError("PKCE_SECRET_WRITE_FAILED")
        document = _json_document(response, "PKCE_SECRET_WRITE_FAILED")
        data = document.get("data")
        if not isinstance(data, dict) or not _is_version_one(data.get("version")):
            raise PkceSecretError("PKCE_SECRET_WRITE_FAILED")
        return f"secret://oidc-login/{identifier}/1"

    async def consume_verifier(
        self,
        *,
        secret_reference: object,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> str:
        """Read version 1 and return it only after permanent metadata deletion."""
        identifier = _parse_reference(secret_reference)
        try:
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method="GET",
                path=f"/{self.mount}/data/oidc-login/{identifier}?version=1",
                transport=transport,
                verify=verify,
            )
        except OpenBaoTlsConfigurationError:
            raise PkceSecretError("OPENBAO_TLS_CONFIGURATION_REJECTED") from None
        except OpenBaoTransportError:
            raise PkceSecretError("PKCE_SECRET_READ_FAILED") from None
        if response.status_code == 404:
            raise PkceSecretUnavailable("PKCE_SECRET_UNAVAILABLE")
        if response.status_code != 200:
            raise PkceSecretError("PKCE_SECRET_READ_FAILED")
        document = _json_document(response, "PKCE_SECRET_READ_FAILED")
        envelope = document.get("data")
        if not isinstance(envelope, dict):
            raise PkceSecretError("PKCE_SECRET_READ_FAILED")
        metadata = envelope.get("metadata")
        secret = envelope.get("data")
        if (
            not isinstance(metadata, dict)
            or not _is_version_one(metadata.get("version"))
            or metadata.get("destroyed") is not False
            or not _valid_deletion_time(metadata.get("deletion_time"))
            or not isinstance(secret, dict)
            or set(secret) != {"code_verifier"}
        ):
            raise PkceSecretError("PKCE_SECRET_READ_FAILED")
        try:
            verifier = _validate_verifier(secret["code_verifier"])
        except ValueError:
            raise PkceSecretError("PKCE_SECRET_READ_FAILED") from None
        await _confirm_permanent_delete(
            self,
            identifier,
            transport=transport,
            verify=verify,
        )
        return verifier


async def _confirm_permanent_delete(
    client_config: OpenBaoPkceClient,
    identifier: UUID,
    *,
    transport: httpx2.AsyncBaseTransport | None,
    verify: ssl.SSLContext | bool,
) -> None:
    for attempt_number in range(_DELETE_ATTEMPTS):
        try:
            response = await request(
                base_url=client_config.base_url,
                token=client_config.token,
                method="DELETE",
                path=f"/{client_config.mount}/metadata/oidc-login/{identifier}",
                transport=transport,
                verify=verify,
            )
        except OpenBaoTlsConfigurationError:
            raise PkceSecretError("OPENBAO_TLS_CONFIGURATION_REJECTED") from None
        except OpenBaoTransportError:
            if attempt_number < _DELETE_ATTEMPTS - 1:
                continue
            raise PkceSecretError("PKCE_SECRET_DELETE_UNCONFIRMED") from None
        if response.status_code == 204:
            return
        if response.status_code >= 500 and attempt_number < _DELETE_ATTEMPTS - 1:
            continue
        raise PkceSecretError("PKCE_SECRET_DELETE_UNCONFIRMED")
    raise PkceSecretError("PKCE_SECRET_DELETE_UNCONFIRMED")


def _json_document(response: OpenBaoResponse, error_code: str) -> dict[str, Any]:
    try:
        return json_document(response)
    except OpenBaoDocumentError:
        raise PkceSecretError(error_code) from None


def _validate_attempt_id(value: object) -> UUID:
    if not isinstance(value, UUID) or value.version != 4:
        raise ValueError("PKCE secret paths require a UUIDv4 login-attempt ID.")
    return value


def _is_version_one(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value == 1


def _valid_deletion_time(value: object) -> bool:
    return value is None or (isinstance(value, str) and len(value) <= 64)


def _parse_reference(value: object) -> UUID:
    if not isinstance(value, str):
        raise PkceSecretUnavailable("PKCE_SECRET_UNAVAILABLE")
    match = _REFERENCE.fullmatch(value)
    if match is None:
        raise PkceSecretUnavailable("PKCE_SECRET_UNAVAILABLE")
    try:
        identifier = UUID(match.group(1))
    except ValueError:
        raise PkceSecretUnavailable("PKCE_SECRET_UNAVAILABLE") from None
    if identifier.version != 4 or str(identifier) != match.group(1):
        raise PkceSecretUnavailable("PKCE_SECRET_UNAVAILABLE")
    return identifier


def _validate_verifier(value: object) -> str:
    if not isinstance(value, str) or _PKCE_VERIFIER.fullmatch(value) is None:
        raise ValueError("PKCE verifier must satisfy the RFC 7636 syntax and length.")
    return value
