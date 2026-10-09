"""Closed outbound request shapes; origin admission remains a separate gate."""

import base64
import binascii
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import parse_qs, urlencode, urlsplit

from signal_core.crawl_http import TELEGRAM_METHODS, EgressHttpRequest
from signal_core.crawl_urls import normalize_crawl_url
from signal_core.indexnow_protocol import IndexNowSubmitScope, validate_indexnow_request
from signal_core.json_objects import unique_object


class EgressProfile(StrEnum):
    CRAWL_PAGE = "crawl_page"
    CRAWL_ROBOTS = "crawl_robots"
    OWNER_CONNECTOR_ROBOTS = "owner_connector_robots"
    DATAFORSEO_ROBOTS = "dataforseo_robots"
    CRAWL_KEY_FILE = "crawl_key_file"
    INDEXNOW_SUBMIT = "indexnow_submit"
    BROWSER_READ = "browser_read"
    BROWSER_WORKER_READ = "browser_worker_read"
    GITHUB_REST = "github_rest"
    GITHUB_REPOSITORY_WRITE = "github_repository_write"
    GOOGLE_OAUTH_TOKEN = "google_oauth_token"
    GOOGLE_OAUTH_REVOKE = "google_oauth_revoke"
    GSC_API = "gsc_api"
    GA4_ADMIN = "ga4_admin"
    GA4_DATA = "ga4_data"
    DRIVE_METADATA = "drive_metadata"
    DRIVE_EXPORT = "drive_export"

    PAGESPEED = "pagespeed"
    BING_OAUTH_TOKEN = "bing_oauth_token"
    BING_API = "bing_api"
    JEV = "jev"
    MODEL_JSON = "model_json"
    OPENAI_MODEL = "openai_model"
    OPENAI_ASSISTANT = "openai_assistant"
    PERPLEXITY_ASSISTANT = "perplexity_assistant"
    GEMINI_ASSISTANT = "gemini_assistant"
    SLACK_BOT = "slack_bot"
    SLACK_OAUTH = "slack_oauth"
    DATAFORSEO = "dataforseo"
    SMTP_SUBMIT = "smtp_submit"
    TELEGRAM_BOT = "telegram_bot"
    WORDPRESS_REST = "wordpress_rest"
    WEBFLOW = "webflow"
    WEBFLOW_OAUTH = "webflow_oauth"
    WEBFLOW_REVOKE = "webflow_revoke"
    NPM_REGISTRY = "npm_registry"


@dataclass(frozen=True)
class RouteRule:
    paths: tuple[str, ...] = ()
    exact_urls: tuple[str, ...] = ()
    no_query: bool = False
    no_fragment: bool = False
    exact_netloc: str | None = None

    def permits(self, request):
        url = urlsplit(request.url)
        return (
            (not self.paths or any(re.fullmatch(path, url.path) for path in self.paths))
            and (not self.exact_urls or request.url in self.exact_urls)
            and (not self.no_query or not url.query)
            and (not self.no_fragment or not url.fragment)
            and (self.exact_netloc is None or url.netloc == self.exact_netloc)
        )


@dataclass(frozen=True)
class ProfileBinding:
    function: str = "bind_shared_egress_profile"
    scope_fields: tuple[str, ...] = ()

    def arguments(self, request):
        from operator import attrgetter

        return (
            tuple(attrgetter(path)(request) for path in self.scope_fields)
            if self.scope_fields
            else (request.profile.value,)
        )


@dataclass(frozen=True)
class ProfileRules:
    purpose: str
    origin: str | None
    methods: frozenset[str]
    content_type: str | None
    accept: str
    response_media_types: tuple[str, ...]
    authorization: bool
    sensitive_body: bool
    max_request_bytes: int
    max_response_bytes: int
    max_timeout_seconds: float
    extra_headers: tuple[tuple[str, str], ...] = ()
    api_key_header: bool = False
    route: RouteRule | None = None
    validators: tuple[Callable, ...] = ()
    authorization_prefix: str = "Bearer "
    minimum_authorization_length: int = 0
    path_credential: bool = False
    owner_allowed: bool = False
    robots_profile: EgressProfile = EgressProfile.OWNER_CONNECTOR_ROBOTS
    binding: ProfileBinding = ProfileBinding()
    context_types: tuple[str, ...] = ()
    evidence_projection: Callable | None = None


_JSON = ("application/json",)
_READ = ("text/html", "application/xhtml+xml")
_BROWSER_READ = (
    *_READ,
    "text/plain",
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/json",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/svg+xml",
    "font/woff",
    "font/woff2",
    "application/font-woff",
)
_MODEL = ProfileRules(
    "model",
    None,
    frozenset({"POST"}),
    "application/json",
    "application/json",
    _JSON,
    True,
    False,
    1024 * 1024,
    128 * 1024,
    20,
)
_ASSISTANT = ProfileRules(
    "model",
    None,
    frozenset({"POST"}),
    "application/json",
    "application/json",
    _JSON,
    True,
    False,
    4096,
    128 * 1024,
    30,
)

PROFILE_RULES = MappingProxyType(
    {
        EgressProfile.PAGESPEED: ProfileRules(
            "connector",
            "https://www.googleapis.com",
            frozenset({"GET"}),
            None,
            "application/json",
            _JSON,
            False,
            False,
            0,
            512 * 1024,
            25,
        ),
        EgressProfile.WEBFLOW: ProfileRules(
            "connector",
            "https://api.webflow.com",
            frozenset({"GET", "POST"}),
            "application/json",
            "application/json",
            _JSON,
            True,
            False,
            65536,
            131072,
            5,
        ),
        EgressProfile.WEBFLOW_OAUTH: ProfileRules(
            "connector",
            "https://api.webflow.com",
            frozenset({"POST"}),
            "application/json",
            "application/json",
            _JSON,
            False,
            True,
            8192,
            16384,
            5,
        ),
        EgressProfile.WEBFLOW_REVOKE: ProfileRules(
            "connector",
            "https://webflow.com",
            frozenset({"POST"}),
            "application/json",
            "application/json",
            _JSON,
            False,
            True,
            8192,
            4096,
            5,
        ),
        EgressProfile.DRIVE_METADATA: ProfileRules(
            "connector",
            "https://www.googleapis.com",
            frozenset({"GET"}),
            None,
            "application/json",
            _JSON,
            True,
            False,
            0,
            16384,
            10,
        ),
        EgressProfile.DRIVE_EXPORT: ProfileRules(
            "connector",
            "https://www.googleapis.com",
            frozenset({"GET"}),
            None,
            "text/plain",
            ("text/plain",),
            True,
            False,
            0,
            512 * 1024,
            10,
        ),
        EgressProfile.TELEGRAM_BOT: ProfileRules(
            "connector",
            "https://api.telegram.org",
            frozenset({"POST"}),
            "application/json",
            "application/json",
            _JSON,
            False,
            True,
            16384,
            16384,
            5,
        ),
        EgressProfile.GA4_ADMIN: ProfileRules(
            "connector",
            "https://analyticsadmin.googleapis.com",
            frozenset({"GET"}),
            None,
            "application/json",
            _JSON,
            True,
            False,
            0,
            128 * 1024,
            10,
        ),
        EgressProfile.GA4_DATA: ProfileRules(
            "connector",
            "https://analyticsdata.googleapis.com",
            frozenset({"POST"}),
            "application/json",
            "application/json",
            _JSON,
            True,
            False,
            4096,
            128 * 1024,
            10,
        ),
        EgressProfile.DATAFORSEO: ProfileRules(
            "connector",
            "https://api.dataforseo.com",
            frozenset({"POST"}),
            "application/json",
            "application/json",
            _JSON,
            True,
            False,
            4096,
            128 * 1024,
            30,
        ),
        EgressProfile.CRAWL_KEY_FILE: ProfileRules(
            "crawl",
            None,
            frozenset({"GET"}),
            None,
            "text/plain",
            ("text/plain",),
            False,
            False,
            0,
            1024,
            5,
        ),
        EgressProfile.INDEXNOW_SUBMIT: ProfileRules(
            "connector",
            "https://api.indexnow.org",
            frozenset({"POST"}),
            "application/json",
            "application/json",
            ("application/json", "text/plain"),
            False,
            True,
            65536,
            4096,
            5,
        ),
        EgressProfile.WORDPRESS_REST: ProfileRules(
            "connector",
            None,
            frozenset({"GET", "POST"}),
            "application/json",
            "application/json",
            _JSON,
            True,
            False,
            65536,
            131072,
            10,
        ),
        EgressProfile.NPM_REGISTRY: ProfileRules(
            "connector",
            "https://registry.npmjs.org",
            frozenset({"GET"}),
            None,
            "application/octet-stream",
            ("application/octet-stream", "application/gzip"),
            False,
            False,
            0,
            5 * 1024 * 1024,
            5,
        ),
        EgressProfile.SLACK_BOT: ProfileRules(
            "connector",
            "https://slack.com",
            frozenset({"POST"}),
            "application/json",
            "application/json",
            _JSON,
            True,
            False,
            16384,
            16384,
            5,
        ),
        EgressProfile.SLACK_OAUTH: ProfileRules(
            "connector",
            "https://slack.com",
            frozenset({"POST"}),
            "application/x-www-form-urlencoded",
            "application/json",
            _JSON,
            False,
            True,
            8192,
            16384,
            5,
        ),
        EgressProfile.CRAWL_PAGE: ProfileRules(
            "crawl",
            None,
            frozenset({"GET"}),
            None,
            "text/html,application/xhtml+xml",
            _READ,
            False,
            False,
            0,
            5 * 1024 * 1024,
            60,
        ),
        EgressProfile.CRAWL_ROBOTS: ProfileRules(
            "crawl",
            None,
            frozenset({"GET"}),
            None,
            "text/plain,text/html;q=0.5",
            ("text/plain", "text/html", "application/octet-stream"),
            False,
            False,
            0,
            512 * 1024,
            60,
        ),
        EgressProfile.OWNER_CONNECTOR_ROBOTS: ProfileRules(
            "crawl",
            None,
            frozenset({"GET"}),
            None,
            "text/plain,text/html;q=0.5",
            ("text/plain", "text/html", "application/octet-stream", "application/json"),
            False,
            False,
            0,
            512 * 1024,
            5,
        ),
        EgressProfile.DATAFORSEO_ROBOTS: ProfileRules(
            "crawl",
            "https://api.dataforseo.com",
            frozenset({"GET"}),
            None,
            "text/plain,text/html;q=0.5",
            ("text/plain", "text/html", "application/octet-stream", "application/json"),
            False,
            False,
            0,
            512 * 1024,
            5,
        ),
        EgressProfile.BROWSER_READ: ProfileRules(
            "browser",
            None,
            frozenset({"GET"}),
            None,
            "text/html",
            _READ,
            False,
            False,
            0,
            5 * 1024 * 1024,
            30,
        ),
        EgressProfile.BROWSER_WORKER_READ: ProfileRules(
            "browser",
            None,
            frozenset({"GET", "HEAD"}),
            None,
            "text/html,text/css,application/javascript,application/json,image/*,font/*",
            _BROWSER_READ,
            False,
            False,
            0,
            5 * 1024 * 1024,
            30,
        ),
        EgressProfile.GITHUB_REST: ProfileRules(
            "connector",
            "https://api.github.com",
            frozenset({"GET", "POST"}),
            "application/json",
            "application/vnd.github+json",
            ("application/json", "application/vnd.github+json"),
            True,
            False,
            256 * 1024,
            256 * 1024,
            5,
            (("x-github-api-version", "2026-03-10"),),
        ),
        EgressProfile.GITHUB_REPOSITORY_WRITE: ProfileRules(
            "connector",
            "https://api.github.com",
            frozenset({"GET", "POST"}),
            "application/json",
            "application/vnd.github+json",
            ("application/json", "application/vnd.github+json"),
            True,
            False,
            128 * 1024,
            256 * 1024,
            5,
            (("x-github-api-version", "2026-03-10"),),
        ),
        EgressProfile.GOOGLE_OAUTH_TOKEN: ProfileRules(
            "connector",
            "https://oauth2.googleapis.com",
            frozenset({"POST"}),
            "application/x-www-form-urlencoded",
            "application/json",
            _JSON,
            False,
            True,
            16 * 1024,
            16 * 1024,
            10,
        ),
        EgressProfile.GOOGLE_OAUTH_REVOKE: ProfileRules(
            "connector",
            "https://oauth2.googleapis.com",
            frozenset({"POST"}),
            "application/x-www-form-urlencoded",
            "application/json",
            ("application/json", "text/plain", "text/html"),
            False,
            True,
            16 * 1024,
            4096,
            10,
        ),
        EgressProfile.GSC_API: ProfileRules(
            "connector",
            "https://www.googleapis.com",
            frozenset({"GET", "POST"}),
            "application/json",
            "application/json",
            _JSON,
            True,
            False,
            128 * 1024,
            128 * 1024,
            10,
        ),
        EgressProfile.BING_OAUTH_TOKEN: ProfileRules(
            "connector",
            "https://www.bing.com",
            frozenset({"POST"}),
            "application/x-www-form-urlencoded",
            "application/json",
            _JSON,
            False,
            True,
            16 * 1024,
            8192,
            10,
        ),
        EgressProfile.BING_API: ProfileRules(
            "connector",
            "https://www.bing.com",
            frozenset({"GET"}),
            None,
            "application/json",
            _JSON,
            True,
            False,
            0,
            1024 * 1024,
            15,
        ),
        EgressProfile.JEV: ProfileRules(
            "model",
            "https://api.typesafe.ai",
            frozenset({"POST"}),
            "application/json",
            "application/json",
            _JSON,
            True,
            False,
            1024 * 1024,
            128 * 1024,
            20,
        ),
        EgressProfile.MODEL_JSON: _MODEL,
        EgressProfile.OPENAI_MODEL: replace(_MODEL, origin="https://api.openai.com"),
        EgressProfile.OPENAI_ASSISTANT: replace(_ASSISTANT, origin="https://api.openai.com"),
        EgressProfile.PERPLEXITY_ASSISTANT: replace(_ASSISTANT, origin="https://api.perplexity.ai"),
        EgressProfile.GEMINI_ASSISTANT: replace(
            _ASSISTANT,
            origin="https://generativelanguage.googleapis.com",
            authorization=False,
            api_key_header=True,
        ),
    }
)

DATAFORSEO_ENDPOINTS = {
    "serp": "/v3/serp/google/organic/live/advanced",
    "volume": "/v3/keywords_data/google_ads/search_volume/live",
    "backlinks": "/v3/backlinks/summary/live",
}


@dataclass(frozen=True, repr=False)
class DataForSeoCredentialScope:
    generation: str
    authorization: str = field(repr=False)

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f-]{36}", self.generation) is None:
            raise ValueError("Invalid DataForSEO credential generation.")
        try:
            raw = base64.b64decode(self.authorization.removeprefix("Basic "), validate=True)
            login, password = raw.decode("ascii").split(":", 1)
        except (ValueError, UnicodeError):
            raise ValueError("Invalid DataForSEO credential scope.") from None
        if not self.authorization.startswith("Basic ") or not login or not password:
            raise ValueError("Invalid DataForSEO credential scope.")


def validate_dataforseo_body(path: str, body: bytes) -> None:
    try:
        document = json.loads(body)
        json.dumps(document, allow_nan=False)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("DataForSEO requires bounded JSON.") from None
    if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
        raise ValueError("DataForSEO requires exactly one task.")
    task = document[0]
    if path == DATAFORSEO_ENDPOINTS["backlinks"]:
        if set(task) != {"target", "include_subdomains"} or task["include_subdomains"] is not True:
            raise ValueError("Invalid DataForSEO backlink task.")
        if (
            not isinstance(task["target"], str)
            or re.fullmatch(
                r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
                task["target"],
            )
            is None
        ):
            raise ValueError("Invalid competitor domain.")
        return
    serp = path == DATAFORSEO_ENDPOINTS["serp"]
    fields = (
        {"keyword", "location_code", "language_code", "depth", "max_crawl_pages"}
        if serp
        else {"keywords", "location_code", "language_code"}
    )
    if (
        set(task) != fields
        or type(task["location_code"]) is not int
        or not 1 <= task["location_code"] <= 1000000
    ):
        raise ValueError("Invalid DataForSEO keyword task.")
    if (
        not isinstance(task["language_code"], str)
        or re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", task["language_code"]) is None
    ):
        raise ValueError("Invalid DataForSEO language.")
    if serp and (
        type(task["depth"]) is not int
        or task["depth"] != 10
        or type(task["max_crawl_pages"]) is not int
        or task["max_crawl_pages"] != 1
    ):
        raise ValueError("Invalid DataForSEO SERP bounds.")
    keywords = [task["keyword"]] if serp else task["keywords"]
    if not isinstance(keywords, list) or len(keywords) != 1:
        raise ValueError("Invalid DataForSEO keywords.")
    for keyword in keywords:
        if (
            not isinstance(keyword, str)
            or not 1 <= len(keyword) <= 200
            or any(ord(c) < 32 or ord(c) == 127 for c in keyword)
            or any(c in keyword for c in ":%+")
        ):
            raise ValueError("Invalid DataForSEO keyword.")


@dataclass(frozen=True, repr=False)
class GitHubRepositoryWriteScope:
    owner: str
    repository: str
    installation_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.owner, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", self.owner) is None
            or not isinstance(self.repository, str)
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", self.repository) is None
            or self.repository in {".", ".."}
            or not isinstance(self.installation_token, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{20,512}", self.installation_token) is None
        ):
            raise ValueError("An exact repository and installation token are required.")

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repository}"


@dataclass(frozen=True, repr=False)
class Ga4ReadScope:
    access_token: str = field(repr=False)
    property_resource_name: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.access_token, str)
            or re.fullmatch(r"[!-~]{16,4096}", self.access_token) is None
            or (
                self.property_resource_name is not None
                and (
                    not isinstance(self.property_resource_name, str)
                    or re.fullmatch(r"properties/[1-9][0-9]{0,19}", self.property_resource_name)
                    is None
                )
            )
        ):
            raise ValueError("An exact GA4 read scope is required.")


def _validate_ga4(
    request: EgressHttpRequest, scope: Ga4ReadScope | None, profile: EgressProfile
) -> None:
    if (
        not isinstance(scope, Ga4ReadScope)
        or dict(request.headers).get("authorization") != f"Bearer {scope.access_token}"
    ):
        raise ValueError("GA4 requires the bound bearer token.")
    url = urlsplit(request.url)
    if url.fragment or "%" in url.path:
        raise ValueError("GA4 route is invalid.")
    if profile == EgressProfile.GA4_ADMIN:
        path = (
            "/v1beta/accountSummaries"
            if scope.property_resource_name is None
            else f"/v1beta/{scope.property_resource_name}/dataStreams"
        )
        query = parse_qs(url.query, keep_blank_values=True)
        if (
            request.body
            or url.path != path
            or set(query) not in ({"pageSize"}, {"pageSize", "pageToken"})
            or query.get("pageSize") != ["50"]
            or any(len(v) != 1 for v in query.values())
            or (
                "pageToken" in query
                and re.fullmatch(r"[A-Za-z0-9_=.-]{1,512}", query["pageToken"][0]) is None
            )
        ):
            raise ValueError("GA4 Admin read is not exact and bounded.")
        return
    if (
        scope.property_resource_name is None
        or url.query
        or url.path != f"/v1beta/{scope.property_resource_name}:runReport"
    ):
        raise ValueError("GA4 Data read differs from the bound property.")
    try:
        body = json.loads(request.body)
        if not isinstance(body, dict) or set(body) != {
            "dateRanges",
            "dimensions",
            "metrics",
            "limit",
            "offset",
            "returnPropertyQuota",
            "orderBys",
        }:
            raise ValueError
        from datetime import date

        ranges = body["dateRanges"]
        if (
            not isinstance(ranges, list)
            or len(ranges) != 1
            or set(ranges[0]) != {"startDate", "endDate"}
        ):
            raise ValueError
        start = date.fromisoformat(ranges[0]["startDate"])
        end = date.fromisoformat(ranges[0]["endDate"])
        if not 0 <= (end - start).days <= 92:
            raise ValueError
        if (
            body["dimensions"] != [{"name": "pagePath"}]
            or body["metrics"]
            != [
                {"name": name}
                for name in ("sessions", "engagedSessions", "engagementRate", "keyEvents")
            ]
            or body["limit"] != "1000"
            or not isinstance(body["offset"], str)
            or body["offset"] not in {str(i * 1000) for i in range(5)}
            or body["returnPropertyQuota"] is not True
            or body["orderBys"] != [{"dimension": {"dimensionName": "pagePath"}}]
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("GA4 report shape is not allowed.") from None


DRIVE_METADATA_FIELDS = "id,mimeType,modifiedTime,version,trashed,isAppAuthorized"


@dataclass(frozen=True, repr=False)
class DrivePickedScope:
    picked_file_ids: tuple[str, ...]
    access_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.picked_file_ids, tuple)
            or not 1 <= len(self.picked_file_ids) <= 20
            or len(set(self.picked_file_ids)) != len(self.picked_file_ids)
            or any(
                not isinstance(x, str) or re.fullmatch(r"[A-Za-z0-9_-]{10,200}", x) is None
                for x in self.picked_file_ids
            )
            or not isinstance(self.access_token, str)
            or re.fullmatch(r"[!-~]{16,4096}", self.access_token) is None
        ):
            raise ValueError("An exact picked-file scope and token are required.")


def _validate_drive(request: EgressHttpRequest, scope: object, profile: EgressProfile) -> None:
    if not isinstance(scope, DrivePickedScope):
        raise ValueError("Drive reads require a picked-file scope.")
    url = urlsplit(request.url)
    suffix = "/export" if profile == EgressProfile.DRIVE_EXPORT else ""
    if url.path not in {f"/drive/v3/files/{item}{suffix}" for item in scope.picked_file_ids}:
        raise ValueError("Drive path is not a picked file.")
    query = parse_qs(url.query, keep_blank_values=True)
    expected = {"mimeType": ["text/plain"]} if suffix else {"fields": [DRIVE_METADATA_FIELDS]}
    if query != expected or url.fragment or request.body or request.method != "GET":
        raise ValueError("Drive reads require exact read-only parameters.")
    if dict(request.headers).get("authorization") != f"Bearer {scope.access_token}":
        raise ValueError("Drive credential differs from the bound token.")


@dataclass(frozen=True, repr=False)
class PageSpeedScope:
    verified_origin: str
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        origin = normalize_crawl_url(self.verified_origin)
        if origin.origin != self.verified_origin or origin.scheme != "https":
            raise ValueError("PageSpeed requires an exact HTTPS verified origin.")
        if self.api_key is not None and (
            not isinstance(self.api_key, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{16,256}", self.api_key) is None
        ):
            raise ValueError("PageSpeed credential is invalid.")


def pagespeed_evidence_url(url: str) -> str:
    """Only validated PSI requests use this projection; keys never reach evidence."""
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    return "https://www.googleapis.com/pagespeedonline/v5/runPagespeed?" + urlencode(
        [(name, query[name][0]) for name in ("url", "strategy", "category")]
    )


def _validate_pagespeed(request: EgressHttpRequest, scope: PageSpeedScope | None) -> None:
    parts = urlsplit(request.url)
    try:
        query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
        expected = {"url", "strategy", "category"}
        if scope is not None and scope.api_key is not None:
            expected.add("key")
        if (
            not isinstance(scope, PageSpeedScope)
            or parts.path != "/pagespeedonline/v5/runPagespeed"
            or parts.fragment
            or set(query) != expected
            or any(len(values) != 1 for values in query.values())
            or query["strategy"][0] not in {"mobile", "desktop"}
            or query["category"] != ["performance"]
            or query.get("key", [None]) != [scope.api_key]
        ):
            raise ValueError()
        page = normalize_crawl_url(query["url"][0])
        if page.origin != scope.verified_origin or page.fetch_url != query["url"][0]:
            raise ValueError()
    except (ValueError, KeyError):
        raise ValueError("PageSpeed request is not exact and verified-site scoped.") from None


def _validate_repository_write(request: EgressHttpRequest, scope: object) -> None:
    if not isinstance(scope, GitHubRepositoryWriteScope):
        raise ValueError("Repository-write profile requires a bound installation token.")
    if dict(request.headers).get("authorization") != f"Bearer {scope.installation_token}":
        raise ValueError("Repository-write credential differs from the bound token.")
    url = urlsplit(request.url)
    prefix = f"/repos/{scope.full_name}"
    if not url.path.startswith(prefix + "/") or "%" in url.path:
        raise ValueError("Repository-write path differs from the bound repository.")
    suffix = url.path[len(prefix) :]
    if request.method == "POST":
        if url.query or suffix not in {
            "/git/refs",
            "/git/trees",
            "/git/blobs",
            "/git/commits",
            "/pulls",
        }:
            raise ValueError("Repository-write route is not allowed.")
        try:
            document = json.loads(request.body)
            json.dumps(document, allow_nan=False)
        except (ValueError, UnicodeDecodeError):
            raise ValueError("Repository writes require JSON objects.") from None
        if not isinstance(document, dict):
            raise ValueError("Repository writes require JSON objects.")
        if suffix == "/git/refs" and (
            not isinstance(document.get("ref"), str)
            or re.fullmatch(r"refs/heads/signal/[0-9a-f]{32}", document["ref"]) is None
        ):
            raise ValueError("Only an operation-specific Signal branch may be created.")
        return
    if suffix == "/pulls":
        query = parse_qs(url.query, keep_blank_values=True)
        if (
            set(query) != {"head", "base", "state", "per_page"}
            or any(len(value) != 1 for value in query.values())
            or query["state"] != ["all"]
            or query["per_page"] != ["100"]
            or re.fullmatch(re.escape(scope.owner) + r":signal/[0-9a-f]{32}", query["head"][0])
            is None
            or not 1 <= len(query["base"][0]) <= 255
        ):
            raise ValueError("Repository-write PR lookup is not exact and bounded.")
        return
    if (
        url.query
        or re.fullmatch(
            r"/git/(?:(?:trees|blobs|commits)/[0-9a-f]{40}|ref/heads/signal/[0-9a-f]{32})", suffix
        )
        is None
    ):
        raise ValueError("Repository-write lookup route is not allowed.")


@dataclass(frozen=True, repr=False)
class WordPressScope:
    origin: str
    authorization: str = field(repr=False)
    author_id: int | None = None
    slug: str | None = None
    post_id: int | None = None

    def __post_init__(self):
        try:
            decoded = base64.b64decode(
                self.authorization.removeprefix("Basic "), validate=True
            ).decode("ascii")
            username, separator, password = decoded.partition(":")
            if (
                not separator
                or not 1 <= len(username) <= 256
                or not 1 <= len(password) <= 256
                or not all(32 <= ord(c) < 127 for c in decoded)
            ):
                raise ValueError
        except (ValueError, UnicodeError, binascii.Error):
            raise ValueError("Invalid WordPress scope.") from None
        if (
            normalize_crawl_url(self.origin + "/").origin != self.origin
            or not self.origin.startswith("https://")
            or not self.authorization.startswith("Basic ")
            or any(c in self.authorization for c in "\r\n")
            or any(
                v is not None and (type(v) is not int or v < 1)
                for v in (self.author_id, self.post_id)
            )
            or self.slug is not None
            and re.fullmatch(r"signal-s[0-9a-f]{32}", self.slug) is None
        ):
            raise ValueError("Invalid WordPress scope.")


def _validate_wordpress(request, scope):
    if not isinstance(scope, WordPressScope):
        raise ValueError("WordPress requires a bound scope.")
    u = urlsplit(request.url)
    if (
        normalize_crawl_url(request.url).origin != scope.origin
        or u.fragment
        or dict(request.headers).get("authorization") != scope.authorization
    ):
        raise ValueError("WordPress origin or credential mismatch.")
    prefix = "/wp-json/wp/v2"
    if request.method == "POST":
        try:
            body = json.loads(request.body, object_pairs_hook=unique_object)
            json.dumps(body, allow_nan=False)
        except (ValueError, UnicodeError):
            raise ValueError("WordPress requires unambiguous JSON.") from None
        if (
            scope.author_id is None
            or scope.slug is None
            or scope.post_id is not None
            or u.path != prefix + "/posts"
            or u.query
            or not isinstance(body, dict)
            or set(body) != {"status", "slug", "title", "content", "excerpt"}
            or body["status"] != "draft"
            or body["slug"] != scope.slug
            or any(
                not isinstance(body[k], str) or "\x00" in body[k]
                for k in ("title", "content", "excerpt")
            )
        ):
            raise ValueError("WordPress only permits exact new draft creation.")
    elif u.path == prefix + "/users/me":
        if u.query != "context=edit" or request.body:
            raise ValueError("WordPress identity read is not exact.")
    elif scope.post_id is not None and u.path == prefix + f"/posts/{scope.post_id}":
        if u.query != "context=edit" or request.body:
            raise ValueError("WordPress post read is not exact.")
    elif u.path == prefix + "/posts" and scope.slug and scope.author_id:
        expected = (
            f"slug={scope.slug}&status=draft&context=edit&author={scope.author_id}&per_page=100"
        )
        if u.query != expected or request.body:
            raise ValueError("WordPress reconciliation read is not exact.")
    else:
        raise ValueError("WordPress route is unavailable.")


@dataclass(frozen=True, repr=False)
class WebflowScope:
    site_id: str
    collection_id: str
    token: str = field(repr=False)
    approved_body_sha256: str | None = None

    def __post_init__(self):
        from signal_core.webflow import object_id, secret

        object_id(self.site_id)
        object_id(self.collection_id)
        secret(self.token)
        if (
            self.approved_body_sha256 is not None
            and re.fullmatch(r"[0-9a-f]{64}", self.approved_body_sha256) is None
        ):
            raise ValueError("Webflow body digest is invalid.")


def _validate_webflow(request, scope):
    import hashlib

    from signal_core.webflow import document

    url = urlsplit(request.url)
    if (
        not isinstance(scope, WebflowScope)
        or dict(request.headers).get("authorization") != f"Bearer {scope.token}"
    ):
        raise ValueError("Webflow requires its exact bound token.")
    if url.fragment or "%" in url.path:
        raise ValueError("Webflow route is not allowed.")
    collection = f"/v2/collections/{scope.collection_id}"
    if request.method == "POST":
        if (
            url.path != collection + "/items/insert"
            or url.query
            or scope.approved_body_sha256 != hashlib.sha256(request.body).hexdigest()
        ):
            raise ValueError("Webflow draft body is not approved.")
        value = document(request.body)
        items = value.get("items")
        if (
            set(value) != {"items"}
            or not isinstance(items, list)
            or len(items) != 1
            or not isinstance(items[0], dict)
            or set(items[0]) != {"isDraft", "fieldData"}
            or items[0]["isDraft"] is not True
            or not isinstance(items[0]["fieldData"], dict)
            or len(items[0]["fieldData"]) != 4
            or "name" not in items[0]["fieldData"]
            or not all(isinstance(v, str) for v in items[0]["fieldData"].values())
            or re.fullmatch(
                r"[a-z0-9-]{1,80}-signal-[0-9a-f]{32}", items[0]["fieldData"].get("slug", "")
            )
            is None
        ):
            raise ValueError("Webflow only creates one marked draft.")
        return
    reads = {
        "/v2/token/introspect",
        f"/v2/sites/{scope.site_id}/custom_domains",
        f"/v2/sites/{scope.site_id}/collections",
        collection,
    }
    if url.path in reads and not url.query and not request.body:
        return
    query = parse_qs(url.query, keep_blank_values=True)
    if (
        url.path != collection + "/items"
        or request.body
        or set(query) != {"slug", "limit", "offset"}
        or any(len(v) != 1 for v in query.values())
        or query["limit"] != ["2"]
        or query["offset"] != ["0"]
        or re.fullmatch(r"[a-z0-9-]{1,80}-signal-[0-9a-f]{32}", query["slug"][0]) is None
    ):
        raise ValueError("Webflow read is not exact and bounded.")


@dataclass(frozen=True, repr=False)
class BingPageReadScope:
    site_url: str
    access_token: str = field(repr=False)

    def __post_init__(self) -> None:
        url = normalize_crawl_url(self.site_url)
        if (
            self.site_url not in {url.origin, url.origin + "/"}
            or url.scheme != "https"
            or not isinstance(self.access_token, str)
            or re.fullmatch(r"[!-~]{16,4096}", self.access_token) is None
        ):
            raise ValueError("An exact Bing site and OAuth token are required.")


def _validate_bing_read(request: EgressHttpRequest, scope: BingPageReadScope | None) -> None:
    endpoint = urlsplit(request.url)
    paths = {
        "/webmaster/api.svc/json/GetUserSites": set(),
        "/webmaster/api.svc/json/GetRankAndTrafficStats": {"siteUrl"},
        "/webmaster/api.svc/json/GetLinkCounts": {"siteUrl", "page"},
        "/webmaster/api.svc/json/GetUrlLinks": {"siteUrl", "link", "page"},
        "/webmaster/api.svc/json/GetPageStats": {"siteUrl"},
    }
    try:
        params = parse_qs(endpoint.query, strict_parsing=True, max_num_fields=3)
    except ValueError:
        raise ValueError("Bing read parameters are invalid.") from None
    if (
        endpoint.path not in paths
        or endpoint.fragment
        or set(params) != paths[endpoint.path]
        or any(len(values) != 1 or not values[0] for values in params.values())
        or ("page" in params and params["page"] != ["0"])
    ):
        raise ValueError("Bing read route is not allowed.")
    if endpoint.path.endswith("/GetPageStats"):
        if (
            not isinstance(scope, BingPageReadScope)
            or params["siteUrl"] != [scope.site_url]
            or dict(request.headers).get("authorization") != f"Bearer {scope.access_token}"
        ):
            raise ValueError("Bing page read differs from the bound site or OAuth token.")
    elif scope is not None:
        raise ValueError("Bing page scope cannot authorize another route.")


def validate_profile_request(
    profile: EgressProfile,
    purpose: str,
    request: EgressHttpRequest,
    sensitive_body: bool,
    github_write_scope: GitHubRepositoryWriteScope | None = None,
    dataforseo_scope: DataForSeoCredentialScope | None = None,
    indexnow_scope: IndexNowSubmitScope | None = None,
    ga4_scope: Ga4ReadScope | None = None,
    wordpress_scope: WordPressScope | None = None,
    drive_picked_scope: DrivePickedScope | None = None,
    webflow_scope: WebflowScope | None = None,
    bing_page_scope: BingPageReadScope | None = None,
    pagespeed_scope: PageSpeedScope | None = None,
) -> None:
    if not isinstance(profile, EgressProfile):
        raise ValueError("A known egress profile is required.")
    if profile in NON_HTTP_PROFILES:
        raise ValueError("SMTP submission cannot borrow the HTTP boundary.")
    rules = PROFILE_RULES[profile]
    scopes = {
        "github_write_scope": github_write_scope,
        "dataforseo_scope": dataforseo_scope,
        "indexnow_scope": indexnow_scope,
        "ga4_scope": ga4_scope,
        "wordpress_scope": wordpress_scope,
        "drive_picked_scope": drive_picked_scope,
        "webflow_scope": webflow_scope,
        "bing_page_scope": bing_page_scope,
        "pagespeed_scope": pagespeed_scope,
    }
    if rules.purpose != purpose or request.method not in rules.methods:
        raise ValueError("Egress profile purpose or method is invalid.")
    origin = normalize_crawl_url(request.url).origin
    if rules.origin is not None and origin != rules.origin:
        raise ValueError("Egress profile origin is invalid.")
    for reservation in ORIGIN_RESERVATIONS:
        reservation.validate(origin, profile, request)
    if len(request.body) > rules.max_request_bytes:
        raise ValueError("Egress profile request body exceeds its limit.")
    if rules.route is not None and not rules.route.permits(request):
        raise ValueError("Egress profile route is not allowed.")
    for scope_rule in SCOPE_RULES:
        value = scopes[scope_rule.field]
        if profile in scope_rule.profiles:
            scope_rule.validate(request, value, profile)
        elif value is not None:
            raise ValueError(scope_rule.borrow_error)
    for validator in rules.validators:
        validator(request)
    if not rules.path_credential and request.telegram_credential is not None:
        raise ValueError("Telegram path credentials cannot be borrowed by another profile.")
    if (
        request.max_response_bytes > rules.max_response_bytes
        or request.timeout_seconds > rules.max_timeout_seconds
    ):
        raise ValueError("Egress profile response or time bound is invalid.")
    if request.accepted_media_types != rules.response_media_types:
        raise ValueError("Egress profile response media types are invalid.")
    expected = {"accept": rules.accept, **dict(rules.extra_headers)}
    if rules.authorization:
        authorization = dict(request.headers).get("authorization")
        prefix = rules.authorization_prefix
        if (
            not authorization
            or not authorization.startswith(prefix)
            or len(authorization) <= len(prefix)
            or len(authorization) < rules.minimum_authorization_length
        ):
            raise ValueError("Egress profile requires bearer authorization.")
        expected["authorization"] = authorization
    if rules.api_key_header:
        api_key = dict(request.headers).get("x-goog-api-key")
        if not api_key:
            raise ValueError("Egress profile requires an API key header.")
        expected["x-goog-api-key"] = api_key
    if request.method == "POST":
        expected["content-type"] = rules.content_type
    if dict(request.headers) != expected or sensitive_body != rules.sensitive_body:
        raise ValueError("Egress profile headers or sensitivity are invalid.")


def profile_headers(
    profile: EgressProfile,
    method: str,
    authorization: str | None,
    google_api_key: str | None = None,
) -> tuple[tuple[str, str], ...]:
    if profile in NON_HTTP_PROFILES:
        raise ValueError("SMTP submission has no HTTP headers.")
    rules = PROFILE_RULES[profile]
    headers = [("accept", rules.accept), *rules.extra_headers]
    if authorization is not None:
        headers.append(("authorization", authorization))
    if google_api_key is not None:
        headers.append(("x-goog-api-key", google_api_key))
    if method == "POST":
        headers.append(("content-type", rules.content_type or ""))
    return tuple(headers)


def npm_tarball_identity(url: str) -> tuple[str, str]:
    match = re.fullmatch(
        r"https://registry[.]npmjs[.]org/(?P<scope>@[a-z0-9][a-z0-9._-]*/)?"
        r"(?P<name>[a-z0-9][a-z0-9._-]*)/-/(?P=name)-"
        r"(?P<version>[0-9]+[.][0-9]+[.][0-9]+(?:-[A-Za-z0-9.-]+)?(?:[+][A-Za-z0-9.-]+)?)[.]tgz",
        url,
    )
    if match is None or len(url) > 1024:
        raise ValueError("Only canonical official registry tarball GETs are allowed.")
    return (match["scope"] or "") + match["name"], match["version"]


@dataclass(frozen=True)
class ScopeRule:
    field: str
    profiles: frozenset[EgressProfile]
    validate: Callable
    borrow_error: str


@dataclass(frozen=True)
class OriginReservation:
    origin: str
    allowed: tuple[tuple[EgressProfile, RouteRule | None], ...]
    methods: frozenset[str] = frozenset()

    def validate(self, origin, profile, request):
        if origin != self.origin or (self.methods and request.method not in self.methods):
            return
        if not any(
            profile == permitted and (route is None or route.permits(request))
            for permitted, route in self.allowed
        ):
            raise ValueError("Egress profile cannot borrow a reserved provider origin.")


def _dataforseo(request, scope, profile):
    endpoint = urlsplit(request.url)
    if (
        not isinstance(scope, DataForSeoCredentialScope)
        or dict(request.headers).get("authorization") != scope.authorization
    ):
        raise ValueError("DataForSEO endpoint or credential binding is invalid.")
    validate_dataforseo_body(endpoint.path, request.body)


def _json_body(request, *, label, finite=False):
    try:
        document = json.loads(request.body)
        if finite:
            json.dumps(document, allow_nan=False)
    except (ValueError, UnicodeError):
        raise ValueError(f"{label} egress requires JSON.") from None
    if not isinstance(document, dict):
        raise ValueError(f"{label} egress requires a JSON object.")


def _telegram(request):
    if request.telegram_credential is None:
        raise ValueError("Telegram egress method is not allowed.")
    _json_body(request, label="Telegram", finite=True)


def _slack_oauth(request):
    try:
        form = parse_qs(request.body.decode("ascii"), strict_parsing=True, max_num_fields=4)
    except (ValueError, UnicodeError):
        raise ValueError("Slack OAuth requires form encoding.") from None
    if set(form) != {"client_id", "client_secret", "code", "redirect_uri"} or any(
        len(values) != 1 or not values[0] for values in form.values()
    ):
        raise ValueError("Slack OAuth requires exact token-exchange fields.")


def _webflow_oauth(request, *, fields):
    from signal_core.webflow import document

    value = document(request.body)
    if set(value) != fields or any(not isinstance(v, str) or not v for v in value.values()):
        raise ValueError("Webflow OAuth fields are invalid.")


SCOPE_RULES = (
    ScopeRule(
        "pagespeed_scope",
        frozenset({EgressProfile.PAGESPEED}),
        lambda request, scope, profile: _validate_pagespeed(request, scope),
        "PageSpeed scope cannot be borrowed by another profile.",
    ),
    ScopeRule(
        "dataforseo_scope",
        frozenset({EgressProfile.DATAFORSEO}),
        _dataforseo,
        "DataForSEO credentials cannot be borrowed by another profile.",
    ),
    ScopeRule(
        "indexnow_scope",
        frozenset({EgressProfile.INDEXNOW_SUBMIT}),
        lambda request, scope, profile: validate_indexnow_request(request.url, request.body, scope),
        "IndexNow scope cannot be borrowed by another profile.",
    ),
    ScopeRule(
        "ga4_scope",
        frozenset({EgressProfile.GA4_ADMIN, EgressProfile.GA4_DATA}),
        _validate_ga4,
        "GA4 scope cannot be borrowed by another profile.",
    ),
    ScopeRule(
        "drive_picked_scope",
        frozenset({EgressProfile.DRIVE_METADATA, EgressProfile.DRIVE_EXPORT}),
        _validate_drive,
        "Picked-file scope cannot be borrowed by another profile.",
    ),
    ScopeRule(
        "webflow_scope",
        frozenset({EgressProfile.WEBFLOW}),
        lambda request, scope, profile: _validate_webflow(request, scope),
        "Webflow scope cannot be borrowed by another profile.",
    ),
    ScopeRule(
        "bing_page_scope",
        frozenset({EgressProfile.BING_API}),
        lambda request, scope, profile: _validate_bing_read(request, scope),
        "Bing page scope cannot be borrowed by another profile.",
    ),
    ScopeRule(
        "github_write_scope",
        frozenset({EgressProfile.GITHUB_REPOSITORY_WRITE}),
        lambda request, scope, profile: _validate_repository_write(request, scope),
        "Repository-write scope cannot be borrowed by another profile.",
    ),
    ScopeRule(
        "wordpress_scope",
        frozenset({EgressProfile.WORDPRESS_REST}),
        lambda request, scope, profile: _validate_wordpress(request, scope),
        "WordPress scope cannot be borrowed.",
    ),
)

ORIGIN_RESERVATIONS = (
    OriginReservation(
        "https://api.dataforseo.com",
        (
            (EgressProfile.DATAFORSEO, None),
            (EgressProfile.DATAFORSEO_ROBOTS, None),
        ),
    ),
    OriginReservation(
        "https://api.webflow.com",
        (
            (EgressProfile.WEBFLOW, None),
            (EgressProfile.WEBFLOW_OAUTH, None),
            (
                EgressProfile.OWNER_CONNECTOR_ROBOTS,
                RouteRule(exact_urls=("https://api.webflow.com/robots.txt",)),
            ),
        ),
    ),
    OriginReservation(
        "https://webflow.com", ((EgressProfile.WEBFLOW_REVOKE, None),), frozenset({"POST"})
    ),
    OriginReservation(
        "https://api.github.com",
        (
            (EgressProfile.GITHUB_REPOSITORY_WRITE, None),
            (
                EgressProfile.GITHUB_REST,
                RouteRule((r"/app/installations/[1-9][0-9]*/access_tokens",), no_query=True),
            ),
        ),
        frozenset({"POST"}),
    ),
)

NON_HTTP_PROFILES = frozenset({EgressProfile.SMTP_SUBMIT})

_DECLARATIONS = {
    EgressProfile.DATAFORSEO_ROBOTS: dict(
        route=RouteRule(exact_urls=("https://api.dataforseo.com/robots.txt",))
    ),
    EgressProfile.DATAFORSEO: dict(
        route=RouteRule(
            tuple(re.escape(p) for p in DATAFORSEO_ENDPOINTS.values()),
            no_query=True,
            no_fragment=True,
            exact_netloc="api.dataforseo.com",
        ),
        authorization_prefix="Basic ",
        robots_profile=EgressProfile.DATAFORSEO_ROBOTS,
        binding=ProfileBinding("bind_dataforseo_egress_profile", ("dataforseo_scope.generation",)),
    ),
    EgressProfile.WORDPRESS_REST: dict(
        authorization_prefix="Basic ", minimum_authorization_length=8
    ),
    EgressProfile.CRAWL_KEY_FILE: dict(
        route=RouteRule((r"/[A-Za-z0-9-]{8,128}\.txt",), no_query=True, no_fragment=True)
    ),
    EgressProfile.GOOGLE_OAUTH_TOKEN: dict(
        route=RouteRule(("/token",), no_query=True, no_fragment=True)
    ),
    EgressProfile.GOOGLE_OAUTH_REVOKE: dict(
        route=RouteRule(("/revoke",), no_query=True, no_fragment=True)
    ),
    EgressProfile.TELEGRAM_BOT: dict(
        path_credential=True,
        route=RouteRule(
            tuple("/" + method for method in TELEGRAM_METHODS), no_query=True, no_fragment=True
        ),
        validators=(_telegram,),
    ),
    EgressProfile.WEBFLOW_OAUTH: dict(
        route=RouteRule(("/oauth/access_token",), no_query=True, no_fragment=True),
        validators=(
            lambda request: _webflow_oauth(
                request, fields={"client_id", "client_secret", "code", "redirect_uri"}
            ),
        ),
    ),
    EgressProfile.WEBFLOW_REVOKE: dict(
        route=RouteRule(("/oauth/revoke_authorization",), no_query=True, no_fragment=True),
        validators=(
            lambda request: _webflow_oauth(
                request, fields={"client_id", "client_secret", "access_token"}
            ),
        ),
    ),
    EgressProfile.NPM_REGISTRY: dict(
        validators=(lambda request: npm_tarball_identity(request.url),)
    ),
    EgressProfile.SLACK_BOT: dict(
        route=RouteRule(
            ("/api/chat[.]postMessage", "/api/auth[.]revoke"), no_query=True, no_fragment=True
        ),
        validators=(lambda request: _json_body(request, label="Slack"),),
    ),
    EgressProfile.SLACK_OAUTH: dict(
        route=RouteRule(("/api/oauth[.]v2[.]access",), no_query=True, no_fragment=True),
        validators=(_slack_oauth,),
    ),
    EgressProfile.BROWSER_WORKER_READ: dict(binding=ProfileBinding("bind_browser_read_profile")),
    EgressProfile.DRIVE_METADATA: dict(binding=ProfileBinding("bind_docs_egress_profile")),
    EgressProfile.DRIVE_EXPORT: dict(binding=ProfileBinding("bind_docs_egress_profile")),
    EgressProfile.WEBFLOW: dict(
        binding=ProfileBinding(
            "bind_webflow_egress_profile", ("webflow_scope.site_id", "webflow_scope.collection_id")
        )
    ),
    EgressProfile.GITHUB_REPOSITORY_WRITE: dict(
        binding=ProfileBinding(
            "bind_github_repository_write_profile", ("github_write_scope.full_name",)
        )
    ),
    EgressProfile.PAGESPEED: dict(
        context_types=("OwnerConnectorContext", "WeeklyPageSpeedContext"),
        evidence_projection=pagespeed_evidence_url,
    ),
}
for _assistant in (
    EgressProfile.OPENAI_ASSISTANT,
    EgressProfile.PERPLEXITY_ASSISTANT,
    EgressProfile.GEMINI_ASSISTANT,
):
    _DECLARATIONS[_assistant] = dict(binding=ProfileBinding("bind_assistant_egress_profile"))
for _owner_profile in (
    EgressProfile.GITHUB_REST,
    EgressProfile.GOOGLE_OAUTH_TOKEN,
    EgressProfile.GOOGLE_OAUTH_REVOKE,
    EgressProfile.GSC_API,
    EgressProfile.PAGESPEED,
    EgressProfile.SLACK_OAUTH,
    EgressProfile.SLACK_BOT,
    EgressProfile.WEBFLOW,
    EgressProfile.WEBFLOW_OAUTH,
    EgressProfile.WEBFLOW_REVOKE,
    EgressProfile.BING_OAUTH_TOKEN,
    EgressProfile.BING_API,
    EgressProfile.DATAFORSEO,
):
    _DECLARATIONS.setdefault(_owner_profile, {})["owner_allowed"] = True
PROFILE_RULES = MappingProxyType(
    {
        profile: replace(rules, **_DECLARATIONS.get(profile, {}))
        for profile, rules in PROFILE_RULES.items()
    }
)


def validate_provider_context(profile, context):
    from signal_core.owner_connector_egress import OwnerConnectorContext, WeeklyPageSpeedContext

    types = {
        "OwnerConnectorContext": OwnerConnectorContext,
        "WeeklyPageSpeedContext": WeeklyPageSpeedContext,
    }
    required = PROFILE_RULES[profile].context_types
    if required and not isinstance(context, tuple(types[name] for name in required)):
        raise ValueError("PageSpeed requires the reserved owner-site collector boundary.")


@dataclass(frozen=True)
class ConnectorFactoryRules:
    profiles: tuple[EgressProfile, ...]
    max_body_bytes: int
    request_timeout_seconds: float
    total_timeout_seconds: float

    @property
    def origins(self):
        return tuple(dict.fromkeys(PROFILE_RULES[profile].origin for profile in self.profiles))

    def policy(self, origins=None):
        from signal_core.crawl_urls import CrawlScopePolicy

        selected = self.origins if origins is None else origins
        if not selected or any(origin not in self.origins for origin in selected):
            raise ValueError("Connector factory origin is not declared.")
        return CrawlScopePolicy(
            1,
            selected,
            "SignalBot/1.0 (+https://signal.example/bot)",
            max_redirects=0,
            max_body_bytes=self.max_body_bytes,
            request_timeout_seconds=self.request_timeout_seconds,
            total_timeout_seconds=self.total_timeout_seconds,
        )


CONNECTOR_FACTORY_RULES = MappingProxyType(
    {
        "slack": ConnectorFactoryRules((EgressProfile.SLACK_BOT,), 256 * 1024, 5, 10),
        "github": ConnectorFactoryRules((EgressProfile.GITHUB_REST,), 256 * 1024, 5, 10),
        "gsc": ConnectorFactoryRules(
            (EgressProfile.GOOGLE_OAUTH_TOKEN, EgressProfile.GSC_API), 256 * 1024, 5, 10
        ),
        "webflow": ConnectorFactoryRules(
            (EgressProfile.WEBFLOW, EgressProfile.WEBFLOW_REVOKE), 131072, 5, 10
        ),
        "strategy_dataforseo": ConnectorFactoryRules(
            (EgressProfile.DATAFORSEO,), 256 * 1024, 30, 30
        ),
    }
)
