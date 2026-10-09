"""Closed IndexNow payload and exact, public root-key verification contract."""

import json
import re
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from signal_core.crawl_urls import normalize_crawl_url

INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
KEY_RECIPE = "technical_indexnow_key"


def valid_indexnow_key(key: object) -> bool:
    return isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9-]{8,128}", key) is not None


def generate_indexnow_key() -> str:
    return secrets.token_hex(32)


@dataclass(frozen=True, repr=False)
class IndexNowSubmitScope:
    verified_origin: str
    key: str = field(repr=False)
    urls: tuple[str, ...]

    def __post_init__(self) -> None:
        origin = normalize_crawl_url(self.verified_origin)
        if (
            origin.origin != self.verified_origin
            or not self.verified_origin.startswith("https://")
            or urlsplit(self.verified_origin).port is not None
            or not valid_indexnow_key(self.key)
            or not isinstance(self.urls, tuple)
            or not 1 <= len(self.urls) <= 32
            or len(set(self.urls)) != len(self.urls)
        ):
            raise ValueError("An exact verified HTTPS site and bounded changed URLs are required.")
        for url in self.urls:
            normalized = normalize_crawl_url(url)
            if (
                normalized.origin != self.verified_origin
                or normalized.fetch_url != url
                or len(url) > 2048
                or urlsplit(url).fragment
                or any(part in {".", ".."} for part in urlsplit(url).path.split("/"))
            ):
                raise ValueError("Changed URLs must belong to the exact verified site.")

    @property
    def key_location(self) -> str:
        return f"{self.verified_origin}/{self.key}.txt"

    def document(self) -> dict:
        return {
            "host": urlsplit(self.verified_origin).hostname,
            "key": self.key,
            "keyLocation": self.key_location,
            "urlList": list(self.urls),
        }

    def body(self) -> bytes:
        return json.dumps(self.document(), separators=(",", ":"), allow_nan=False).encode("utf-8")


def validate_indexnow_request(url: str, body: bytes, scope: object) -> None:
    if not isinstance(scope, IndexNowSubmitScope) or url != INDEXNOW_ENDPOINT:
        raise ValueError("IndexNow requires an exact site-bound submission scope.")
    try:
        document = json.loads(body)
    except (ValueError, UnicodeError):
        raise ValueError("IndexNow requires JSON.") from None
    if document != scope.document():
        raise ValueError("IndexNow payload differs from its verified changed-URL scope.")


def key_file_outcome(
    scope: IndexNowSubmitScope,
    *,
    status: int | None,
    media_type: str | None,
    body: bytes,
    final_url: str,
    outcome: str = "fetched",
) -> str:
    if outcome != "fetched" or status is None:
        if outcome == "redirect_rejected":
            return "EC_142_KEY_REDIRECT"
        if outcome == "unsupported_media_type":
            return "EC_142_KEY_CONTENT_TYPE"
        return "EC_142_KEY_UNREACHABLE"
    if final_url != scope.key_location:
        return "EC_142_KEY_REDIRECT"
    if status != 200:
        return "EC_142_KEY_MISSING" if status in {404, 410} else "EC_142_KEY_UNREACHABLE"
    if media_type != "text/plain":
        return "EC_142_KEY_CONTENT_TYPE"
    if body != scope.key.encode("utf-8"):
        return "EC_142_KEY_MISMATCH"
    return "KEY_DEPLOYED"
