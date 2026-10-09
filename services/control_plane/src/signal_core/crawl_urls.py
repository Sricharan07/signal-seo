"""Canonical URL identity and exact-origin admission for crawl work."""

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit


class CrawlUrlRejected(ValueError):
    """A URL cannot enter the configured crawl scope."""


@dataclass(frozen=True, repr=False)
class CrawlUrl:
    original_url: str
    fetch_url: str
    display_url: str
    normalized_key: str
    origin: str
    scheme: str
    host: str
    port: int
    request_target: str


@dataclass(frozen=True)
class CrawlScopePolicy:
    """Immutable exact-origin scope and bounded HTTP-fetch limits."""

    schema_version: int
    allowed_origins: tuple[str, ...]
    user_agent: str
    max_redirects: int = 5
    max_body_bytes: int = 5 * 1024 * 1024
    request_timeout_seconds: float = 10.0
    total_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise CrawlUrlRejected("Unsupported crawl scope schema version.")
        if not isinstance(self.allowed_origins, tuple) or not self.allowed_origins:
            raise CrawlUrlRejected("At least one allowed crawl origin is required.")
        normalized = tuple(_normalize_origin(origin) for origin in self.allowed_origins)
        if len(set(normalized)) != len(normalized) or len(normalized) > 16:
            raise CrawlUrlRejected("Allowed crawl origins are invalid.")
        object.__setattr__(self, "allowed_origins", normalized)
        if (
            not isinstance(self.user_agent, str)
            or not 8 <= len(self.user_agent) <= 200
            or _VISIBLE_ASCII.fullmatch(self.user_agent) is None
            or not self.user_agent.startswith("SignalBot/")
        ):
            raise CrawlUrlRejected("Crawl user agent is invalid.")
        _bounded_integer(self.max_redirects, 0, 10, "redirect limit")
        _bounded_integer(self.max_body_bytes, 1024, 5 * 1024 * 1024, "body limit")
        request_timeout = _bounded_number(
            self.request_timeout_seconds, 0.25, 30.0, "request timeout"
        )
        total_timeout = _bounded_number(self.total_timeout_seconds, 1.0, 120.0, "total timeout")
        if total_timeout < request_timeout:
            raise CrawlUrlRejected("Total timeout cannot be shorter than request timeout.")

    def admit(self, value: object) -> CrawlUrl:
        url = normalize_crawl_url(value)
        if url.origin not in self.allowed_origins:
            raise CrawlUrlRejected("URL is outside the admitted crawl origins.")
        return url

    def admit_redirect(self, current: CrawlUrl, location: object) -> CrawlUrl:
        if not isinstance(current, CrawlUrl):
            raise CrawlUrlRejected("A current crawl URL is required.")
        if not isinstance(location, str) or not location or len(location) > _MAX_URL_LENGTH:
            raise CrawlUrlRejected("Redirect location is invalid.")
        _reject_hidden_characters(location)
        return self.admit(urljoin(current.fetch_url, location))


def normalize_crawl_url(value: object) -> CrawlUrl:
    """Return separate original, display, fetch, origin, and dedup identities."""
    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_URL_LENGTH:
        raise CrawlUrlRejected("Crawl URL is invalid.")
    _reject_hidden_characters(value)
    if "\\" in value:
        raise CrawlUrlRejected("Crawl URL is invalid.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise CrawlUrlRejected("Crawl URL is invalid.") from None
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        raise CrawlUrlRejected("Crawl URL is invalid.")
    if parsed.username is not None or parsed.password is not None:
        raise CrawlUrlRejected("Crawl URL cannot contain user information.")
    if parsed.hostname is None:
        raise CrawlUrlRejected("Crawl URL host is invalid.")
    host = _canonical_host(parsed.hostname)
    effective_port = port or {"http": 80, "https": 443}[scheme]
    if effective_port not in {80, 443}:
        raise CrawlUrlRejected("Crawl URL port is not allowed.")
    if (scheme == "http" and effective_port != 80) or (scheme == "https" and effective_port != 443):
        raise CrawlUrlRejected("Crawl URL port is not allowed.")

    path = parsed.path or "/"
    _validate_component(path)
    _validate_component(parsed.query)
    encoded_path = quote(path, safe="/%:@!$&'()*+,;=-._~")
    encoded_query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    has_query_marker = "?" in value.partition("#")[0]
    host_text = f"[{host}]" if ":" in host else host
    origin = f"{scheme}://{host_text}"
    request_target = encoded_path
    if encoded_query or has_query_marker:
        request_target += f"?{encoded_query}"
    fetch_url = origin + request_target
    if len(fetch_url) > _MAX_URL_LENGTH:
        raise CrawlUrlRejected("Crawl URL is too long after normalization.")
    return CrawlUrl(
        original_url=value,
        fetch_url=fetch_url,
        display_url=fetch_url,
        normalized_key=fetch_url,
        origin=origin,
        scheme=scheme,
        host=host,
        port=effective_port,
        request_target=request_target,
    )


def validate_public_addresses(addresses: object) -> tuple[str, ...]:
    """Validate every resolver answer and return stable canonical addresses."""
    if not isinstance(addresses, (tuple, list)) or not addresses or len(addresses) > 16:
        raise CrawlUrlRejected("Resolver answer is invalid.")
    validated = []
    for value in addresses:
        if not isinstance(value, str) or not value or len(value) > 64:
            raise CrawlUrlRejected("Resolver answer is invalid.")
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise CrawlUrlRejected("Resolver answer is invalid.") from None
        effective = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
        checked = effective or address
        if (
            not checked.is_global
            or checked.is_multicast
            or checked.is_unspecified
            or checked.is_reserved
            or checked.is_loopback
            or checked.is_link_local
            or checked.is_private
        ):
            raise CrawlUrlRejected("Resolver answer is not a public destination.")
        validated.append(address.compressed)
    return tuple(
        str(address)
        for address in sorted(
            {ipaddress.ip_address(value) for value in validated},
            key=lambda item: (item.version, item.packed),
        )
    )


def _normalize_origin(value: object) -> str:
    url = normalize_crawl_url(value)
    raw_without_fragment = value.partition("#")[0] if isinstance(value, str) else ""
    if url.request_target != "/" or "?" in raw_without_fragment or "#" in value:
        raise CrawlUrlRejected("Allowed crawl origin must not contain a path, query, or fragment.")
    return url.origin


def _canonical_host(value: str) -> str:
    if not value or len(value) > 253 or any(character.isspace() for character in value):
        raise CrawlUrlRejected("Crawl URL host is invalid.")
    candidate = value[:-1] if value.endswith(".") else value
    if not candidate or "%" in candidate:
        raise CrawlUrlRejected("Crawl URL host is invalid.")
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        pass
    try:
        ascii_host = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise CrawlUrlRejected("Crawl URL host is invalid.") from None
    if len(ascii_host) > 253 or any(
        not label or len(label) > 63 or _HOST_LABEL.fullmatch(label) is None
        for label in ascii_host.split(".")
    ):
        raise CrawlUrlRejected("Crawl URL host is invalid.")
    return ascii_host


def _validate_component(value: str) -> None:
    if len(value) > _MAX_URL_LENGTH or _BAD_PERCENT.search(value):
        raise CrawlUrlRejected("Crawl URL encoding is invalid.")
    index = 0
    while index < len(value):
        if value[index] == "%":
            decoded = int(value[index + 1 : index + 3], 16)
            if decoded < 32 or decoded == 127:
                raise CrawlUrlRejected("Crawl URL contains hidden control characters.")
            index += 3
        else:
            index += 1


def _reject_hidden_characters(value: str) -> None:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise CrawlUrlRejected("Crawl URL contains hidden control characters.")


def _bounded_integer(value: object, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CrawlUrlRejected(f"Crawl {name} is invalid.")
    return value


def _bounded_number(value: object, minimum: float, maximum: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CrawlUrlRejected(f"Crawl {name} is invalid.")
    converted = float(value)
    if not minimum <= converted <= maximum:
        raise CrawlUrlRejected(f"Crawl {name} is invalid.")
    return converted


_MAX_URL_LENGTH = 2048
_VISIBLE_ASCII = re.compile(r"[\x20-\x7e]+")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
