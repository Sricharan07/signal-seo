"""Shared bounded HTTPS transport for narrowly scoped OpenBao clients."""

import json
import re
import ssl
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx2

_MOUNT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_TOKEN = re.compile(r"[A-Za-z0-9._-]{16,4096}")
_MAX_RESPONSE_BYTES = 16 * 1024


class OpenBaoTransportError(Exception):
    """A response could not be obtained within the bounded transport contract."""


class OpenBaoDocumentError(Exception):
    """An OpenBao response was not a bounded JSON object."""


class OpenBaoTlsConfigurationError(Exception):
    """TLS verification was explicitly disabled or incorrectly configured."""


@dataclass(frozen=True)
class OpenBaoResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes = field(repr=False)


async def request(
    *,
    base_url: str,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    transport: httpx2.AsyncBaseTransport | None = None,
    verify: ssl.SSLContext | bool = True,
) -> OpenBaoResponse:
    """Execute one fixed-origin request without redirects, proxies, or large bodies."""
    verifier = trusted_tls_verifier(verify)
    try:
        async with httpx2.AsyncClient(
            base_url=base_url,
            timeout=httpx2.Timeout(5.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            verify=verifier,
        ) as client:
            async with client.stream(
                method,
                f"/v1{path}",
                headers={"Accept": "application/json", "X-Vault-Token": token},
                json=payload,
            ) as response:
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        if int(content_length) > _MAX_RESPONSE_BYTES:
                            raise OpenBaoTransportError()
                    except ValueError:
                        raise OpenBaoTransportError() from None
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > _MAX_RESPONSE_BYTES:
                        raise OpenBaoTransportError()
                return OpenBaoResponse(
                    status_code=response.status_code,
                    headers={key.lower(): value for key, value in response.headers.items()},
                    content=bytes(content),
                )
    except (httpx2.HTTPError, OpenBaoTransportError):
        raise OpenBaoTransportError() from None


def json_document(response: OpenBaoResponse) -> dict[str, Any]:
    """Parse only a JSON object without retaining provider error text."""
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise OpenBaoDocumentError()
    try:
        document = json.loads(response.content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise OpenBaoDocumentError() from None
    if not isinstance(document, dict):
        raise OpenBaoDocumentError()
    return document


def valid_base_url(value: object) -> bool:
    if not isinstance(value, str) or not value.isascii() or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and "\\" not in parsed.netloc
    )


def valid_token(value: object) -> bool:
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def valid_mount(value: object) -> bool:
    return isinstance(value, str) and _MOUNT.fullmatch(value) is not None


def trusted_tls_verifier(value: ssl.SSLContext | bool) -> ssl.SSLContext | bool:
    if value is not True and not isinstance(value, ssl.SSLContext):
        raise OpenBaoTlsConfigurationError()
    return value
