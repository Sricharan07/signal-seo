"""Bounded read-only Google Search Console property discovery."""

import json
import re
import ssl
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx2

from signal_core.egress_profiles import EgressProfile
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider
from signal_core.site_onboarding import InvalidSiteOnboarding, normalize_public_site_origin

GSC_READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GSC_SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"

_ACCESS_TOKEN = re.compile(r"[\x21-\x7e]{16,4096}")
_DOMAIN_LABEL = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)")
_PERMISSION_LEVELS = frozenset(
    {"siteOwner", "siteFullUser", "siteRestrictedUser", "siteUnverifiedUser"}
)
_READ_PERMISSION_LEVELS = frozenset({"siteOwner", "siteFullUser", "siteRestrictedUser"})
_MAX_PROPERTIES = 1000
_MAX_RESPONSE_BYTES = 128 * 1024


class GscProtocolError(Exception):
    """A GSC request or response failed one closed protocol check."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GscProperty:
    resource_name: str
    property_type: Literal["domain", "url_prefix"]
    permission_level: Literal[
        "siteOwner", "siteFullUser", "siteRestrictedUser", "siteUnverifiedUser"
    ]
    matches_verified_origin: bool
    readable: bool

    @property
    def eligible(self) -> bool:
        return self.matches_verified_origin and self.readable


async def discover_gsc_properties(
    *,
    access_token: object,
    verified_origin: object,
    transport: httpx2.AsyncBaseTransport | None = None,
    verify: ssl.SSLContext | bool = True,
) -> tuple[GscProperty, ...]:
    """Test-only transport path retained for synthetic protocol fixtures."""
    token = _validated_access_token(access_token)
    origin = _validated_origin(verified_origin)
    verifier = _trusted_tls_verifier(verify)
    if transport is None:
        raise GscProtocolError("GSC_EGRESS_REQUIRED")
    try:
        async with httpx2.AsyncClient(
            timeout=httpx2.Timeout(5.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            verify=verifier,
        ) as client:
            async with client.stream(
                "GET",
                GSC_SITES_URL,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            ) as response:
                if response.status_code in {401, 403}:
                    raise GscProtocolError("GSC_AUTHORIZATION_REJECTED")
                if response.status_code == 429 or response.status_code >= 500:
                    raise GscProtocolError("GSC_PROVIDER_UNAVAILABLE")
                if response.status_code != 200:
                    raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED")
                body = await _bounded_json_body(response)
    except GscProtocolError:
        raise
    except httpx2.HTTPError:
        raise GscProtocolError("GSC_PROVIDER_UNAVAILABLE") from None
    return _parse_property_list(body, origin)


def discover_gsc_properties_via_egress(
    *,
    access_token: object,
    verified_origin: object,
    egress: SharedEgressProvider,
    operation_id: UUID,
) -> tuple[GscProperty, ...]:
    """Discover exact resources through the durable shared provider gateway."""
    token = _validated_access_token(access_token)
    origin = _validated_origin(verified_origin)
    if not isinstance(egress, SharedEgressProvider) or egress.purpose != "connector":
        raise GscProtocolError("GSC_EGRESS_REQUIRED")
    try:
        response = egress.request_json(
            method="GET",
            url=GSC_SITES_URL,
            profile=EgressProfile.GSC_API,
            authorization=f"Bearer {token}",
            operation_id=operation_id,
            timeout_seconds=5,
            max_response_bytes=_MAX_RESPONSE_BYTES,
        )
    except ProviderEgressUnavailable:
        raise GscProtocolError("GSC_PROVIDER_UNAVAILABLE") from None
    if response.status_code in {401, 403}:
        raise GscProtocolError("GSC_AUTHORIZATION_REJECTED")
    if response.status_code == 429 or response.status_code >= 500:
        raise GscProtocolError("GSC_PROVIDER_UNAVAILABLE")
    if response.status_code != 200 or response.media_type != "application/json":
        raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED")
    return _parse_property_list(response.body, origin)


def _parse_property_list(body: bytes, origin: str) -> tuple[GscProperty, ...]:
    try:
        document = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED") from None
    if not isinstance(document, dict) or not set(document).issubset({"siteEntry"}):
        raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED")
    entries = document.get("siteEntry", [])
    if not isinstance(entries, list) or len(entries) > _MAX_PROPERTIES:
        raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED")

    observed: set[str] = set()
    properties = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"permissionLevel", "siteUrl"}:
            raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED")
        resource_name = entry.get("siteUrl")
        permission_level = entry.get("permissionLevel")
        if (
            not isinstance(resource_name, str)
            or not resource_name.isascii()
            or not 1 <= len(resource_name) <= 2048
            or resource_name in observed
            or permission_level not in _PERMISSION_LEVELS
        ):
            raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED")
        property_type, matches = _classify_property(resource_name, origin)
        observed.add(resource_name)
        properties.append(
            GscProperty(
                resource_name=resource_name,
                property_type=property_type,
                permission_level=permission_level,
                matches_verified_origin=matches,
                readable=permission_level in _READ_PERMISSION_LEVELS,
            )
        )
    return tuple(properties)


def _classify_property(
    resource_name: str, origin: str
) -> tuple[Literal["domain", "url_prefix"], bool]:
    if resource_name.startswith("sc-domain:"):
        domain = resource_name.removeprefix("sc-domain:")
        if not _valid_domain(domain):
            raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED")
        origin_host = urlsplit(origin).hostname
        if origin_host is None:
            raise GscProtocolError("GSC_SITE_ORIGIN_REJECTED")
        return "domain", origin_host == domain or origin_host.endswith(f".{domain}")

    try:
        parsed = urlsplit(resource_name)
        _ = parsed.port
    except ValueError:
        raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED") from None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or parsed.query
        or parsed.fragment
        or "\\" in parsed.netloc
        or any(ord(character) < 33 or ord(character) == 127 for character in resource_name)
    ):
        raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED")
    return "url_prefix", resource_name == f"{origin}/"


async def _bounded_json_body(response: httpx2.Response) -> bytes:
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED")
    declared_length = response.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > _MAX_RESPONSE_BYTES or int(declared_length) < 0:
                raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED")
        except ValueError:
            raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED") from None
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > _MAX_RESPONSE_BYTES:
            raise GscProtocolError("GSC_PROVIDER_RESPONSE_REJECTED")
    return bytes(content)


def _validated_access_token(value: object) -> str:
    if not isinstance(value, str) or _ACCESS_TOKEN.fullmatch(value) is None:
        raise GscProtocolError("GSC_ACCESS_TOKEN_REJECTED")
    return value


def _validated_origin(value: object) -> str:
    try:
        return normalize_public_site_origin(value)
    except InvalidSiteOnboarding:
        raise GscProtocolError("GSC_SITE_ORIGIN_REJECTED") from None


def _valid_domain(value: str) -> bool:
    if value != value.lower() or len(value) > 253 or "." not in value:
        return False
    labels = value.split(".")
    return all(_DOMAIN_LABEL.fullmatch(label) is not None for label in labels)


def _trusted_tls_verifier(value: ssl.SSLContext | bool) -> ssl.SSLContext | bool:
    if value is not True and not isinstance(value, ssl.SSLContext):
        raise GscProtocolError("GSC_TLS_CONFIGURATION_REJECTED")
    return value
