"""Bounded extraction of SEO evidence from one encrypted HTML observation."""

import hashlib
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_TITLE_CHARS = 512
MAX_DESCRIPTION_CHARS = 2048
MAX_HEADING_CHARS = 1024
MAX_HEADINGS = 512
MAX_HREFLANG = 256
MAX_STRUCTURED_TYPES = 256
MAX_LINKS = 4096
MAX_JSON_LD_BYTES = 256 * 1024
MAX_MISSING_ALT_IMAGES = 128


@dataclass(frozen=True)
class CrawlHeading:
    level: int
    text: str


@dataclass(frozen=True)
class CrawlHreflang:
    language: str
    url: str


@dataclass(frozen=True, repr=False)
class ParsedCrawlPage:
    schema_version: int
    body_sha256: str
    title: str | None
    meta_description: str | None
    robots_meta: tuple[str, ...]
    canonical_url: str | None
    headings: tuple[CrawlHeading, ...]
    hreflang: tuple[CrawlHreflang, ...]
    structured_data_types: tuple[str, ...]
    internal_links: tuple[str, ...]
    external_links: tuple[str, ...]
    parse_error_count: int
    output_truncated: bool
    missing_alt_images: tuple[str, ...] = ()


class _SeoParser(HTMLParser):
    def __init__(self, *, page_url: str, exact_origin: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.exact_origin = exact_origin
        self.title_parts: list[str] = []
        self.description: str | None = None
        self.robots: list[str] = []
        self.canonical: str | None = None
        self.headings: list[CrawlHeading] = []
        self.hreflang: list[CrawlHreflang] = []
        self.internal_links: list[str] = []
        self.external_links: list[str] = []
        self.structured_types: list[str] = []
        self.missing_alt_images: list[str] = []
        self.parse_errors = 0
        self.truncated = False
        self._title = False
        self._heading_level: int | None = None
        self._heading_parts: list[str] = []
        self._json_ld = False
        self._json_ld_parts: list[str] = []
        self._json_ld_bytes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value for name, value in attrs if value is not None}
        lowered = tag.lower()
        if lowered == "title":
            self._title = True
        elif lowered in {"h1", "h2", "h3"}:
            self._heading_level = int(lowered[1])
            self._heading_parts = []
        elif lowered == "meta":
            name = (values.get("name") or "").strip().lower()
            content = _clean(values.get("content"), MAX_DESCRIPTION_CHARS)
            if name == "description" and self.description is None:
                self.description = content
            elif name in {"robots", "googlebot", "bingbot"} and content:
                _append_unique(self.robots, content.lower(), 32, self)
        elif lowered == "link":
            relations = {item.lower() for item in (values.get("rel") or "").split()}
            href = _absolute_http_url(self.page_url, values.get("href"))
            if "canonical" in relations and self.canonical is None:
                self.canonical = href
            language = _clean(values.get("hreflang"), 64)
            if "alternate" in relations and language and href:
                item = CrawlHreflang(language.lower(), href)
                if item not in self.hreflang:
                    if len(self.hreflang) < MAX_HREFLANG:
                        self.hreflang.append(item)
                    else:
                        self.truncated = True
        elif lowered == "a":
            href = _absolute_http_url(self.page_url, values.get("href"))
            if href:
                target = (
                    self.internal_links
                    if _origin(href) == self.exact_origin
                    else self.external_links
                )
                _append_unique(target, href, MAX_LINKS, self)
        elif lowered == "img" and "alt" not in {name.lower() for name, _ in attrs}:
            if (
                values.get("role", "").lower() != "presentation"
                and values.get("aria-hidden", "").lower() != "true"
            ):
                src = _clean(values.get("src"), 2048)
                if src:
                    _append_unique(self.missing_alt_images, src, MAX_MISSING_ALT_IMAGES, self)
        elif lowered == "script" and (values.get("type") or "").lower() == "application/ld+json":
            self._json_ld = True
            self._json_ld_parts = []
            self._json_ld_bytes = 0

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._title = False
        elif lowered in {"h1", "h2", "h3"} and self._heading_level is not None:
            text = _clean(" ".join(self._heading_parts), MAX_HEADING_CHARS)
            if text:
                if len(self.headings) < MAX_HEADINGS:
                    self.headings.append(CrawlHeading(self._heading_level, text))
                else:
                    self.truncated = True
            self._heading_level = None
            self._heading_parts = []
        elif lowered == "script" and self._json_ld:
            self._consume_json_ld("".join(self._json_ld_parts))
            self._json_ld = False
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self._title:
            self.title_parts.append(data)
        if self._heading_level is not None:
            self._heading_parts.append(data)
        if self._json_ld:
            encoded = data.encode("utf-8", errors="ignore")
            if self._json_ld_bytes + len(encoded) <= MAX_JSON_LD_BYTES:
                self._json_ld_parts.append(data)
                self._json_ld_bytes += len(encoded)
            else:
                self.truncated = True

    def _consume_json_ld(self, value: str) -> None:
        if self._json_ld_bytes > MAX_JSON_LD_BYTES:
            return
        try:
            payload = json.loads(value)
        except (json.JSONDecodeError, RecursionError):
            self.parse_errors += 1
            return
        for item in _json_ld_types(payload):
            _append_unique(self.structured_types, item, MAX_STRUCTURED_TYPES, self)

    def finish_open_elements(self) -> None:
        """Finalize useful text from tolerated, unclosed HTML elements."""
        if self._heading_level is not None:
            self.handle_endtag(f"h{self._heading_level}")
        if self._json_ld:
            self.handle_endtag("script")
        self._title = False


def parse_crawl_page(body: object, *, page_url: object, exact_origin: object) -> ParsedCrawlPage:
    """Extract bounded metadata; page strings are returned only as inert evidence."""
    if not isinstance(body, bytes) or len(body) > MAX_HTML_BYTES:
        raise ValueError("Crawl parser input exceeds its byte limit.")
    if not isinstance(page_url, str) or not isinstance(exact_origin, str):
        raise ValueError("Canonical page and origin strings are required.")
    if _origin(page_url) != exact_origin or _origin(exact_origin + "/") != exact_origin:
        raise ValueError("Crawl parser input is outside the exact origin.")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        text = body.decode("utf-8", errors="replace")
    parser = _SeoParser(page_url=page_url, exact_origin=exact_origin)
    try:
        parser.feed(text)
        parser.close()
        parser.finish_open_elements()
    except (RecursionError, ValueError):
        parser.parse_errors += 1
    title = _clean(" ".join(parser.title_parts), MAX_TITLE_CHARS)
    return ParsedCrawlPage(
        schema_version=1,
        body_sha256=hashlib.sha256(body).hexdigest(),
        title=title,
        meta_description=parser.description,
        robots_meta=tuple(parser.robots),
        canonical_url=parser.canonical,
        headings=tuple(parser.headings),
        hreflang=tuple(parser.hreflang),
        structured_data_types=tuple(parser.structured_types),
        internal_links=tuple(parser.internal_links),
        external_links=tuple(parser.external_links),
        parse_error_count=min(parser.parse_errors, 1_000_000),
        output_truncated=parser.truncated,
        missing_alt_images=tuple(parser.missing_alt_images),
    )


def _json_ld_types(value: object, *, depth: int = 0):
    if depth > 32:
        return
    if isinstance(value, dict):
        typed = value.get("@type")
        if isinstance(typed, str) and _TYPE.fullmatch(typed):
            yield typed
        elif isinstance(typed, list):
            for item in typed[:MAX_STRUCTURED_TYPES]:
                if isinstance(item, str) and _TYPE.fullmatch(item):
                    yield item
        for child in list(value.values())[:2048]:
            yield from _json_ld_types(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value[:2048]:
            yield from _json_ld_types(child, depth=depth + 1)


def _absolute_http_url(base: str, value: str | None) -> str | None:
    cleaned = _clean(value, 2048)
    if not cleaned:
        return None
    try:
        parsed = urlsplit(urljoin(base, cleaned))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            return None
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname.lower()
    authority = f"[{host}]" if ":" in host else host
    if port is not None and not (
        (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    ):
        authority = f"{authority}:{port}"
    return urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, ""))


def _origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname.lower()
    authority = f"[{host}]" if ":" in host else host
    if port is not None and not (
        (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    ):
        authority = f"{authority}:{port}"
    return f"{parsed.scheme}://{authority}"


def _clean(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _SPACE.sub(" ", value).strip()
    return cleaned[:limit] or None


def _append_unique(values: list[str], value: str, limit: int, parser: _SeoParser) -> None:
    if value in values:
        return
    if len(values) < limit:
        values.append(value)
    else:
        parser.truncated = True


_SPACE = re.compile(r"\s+")
_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}")
