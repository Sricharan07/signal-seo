"""Read-only Bing Webmaster OAuth and bounded JSON/HTTP observations."""

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlencode, urlsplit
from uuid import UUID

from signal_core.crawl_urls import CrawlUrlRejected, normalize_crawl_url
from signal_core.egress_profiles import BingPageReadScope, EgressProfile
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider
from signal_core.site_onboarding import InvalidSiteOnboarding, normalize_public_site_origin

BING_READ_SCOPE = "webmaster.read"
BING_AUTHORIZE_URL = "https://www.bing.com/webmasters/oauth/authorize"
BING_TOKEN_URL = "https://www.bing.com/webmasters/oauth/token"
BING_API_URL = "https://www.bing.com/webmaster/api.svc/json"
_SECRET = re.compile(r"[!-~]{16,4096}")
_DATE = re.compile(r"/Date\((-?[0-9]{1,16})([+-][0-9]{4})\)/")


class BingProtocolError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class BingAuthorization:
    url: str
    state_sha256: bytes = field(repr=False)
    state: str = field(repr=False)


@dataclass(frozen=True, repr=False)
class BingTokens:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_in: int


@dataclass(frozen=True)
class BingSite:
    url: str
    verified: bool
    eligible: bool


@dataclass(frozen=True)
class BingObservation:
    rows: tuple[dict, ...]
    coverage: dict
    response_sha256: bytes


def new_bing_authorization(client_id: str, redirect_uri: str) -> BingAuthorization:
    if not isinstance(client_id, str) or not 16 <= len(client_id) <= 256 or not client_id.isascii():
        raise BingProtocolError("BING_CLIENT_REJECTED")
    _redirect(redirect_uri)
    state = secrets.token_urlsafe(32)
    url = (
        BING_AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": BING_READ_SCOPE,
                "state": state,
            }
        )
    )
    return BingAuthorization(url, hashlib.sha256(state.encode()).digest(), state)


def exchange_bing_code(
    egress: SharedEgressProvider,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    operation_id: UUID,
) -> BingTokens:
    _credentials(client_id, client_secret)
    if not isinstance(code, str) or _SECRET.fullmatch(code) is None:
        raise BingProtocolError("BING_CODE_REJECTED")
    _redirect(redirect_uri)
    return _token_request(
        egress,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        operation_id,
        require_refresh=True,
    )


def refresh_bing_token(
    egress: SharedEgressProvider,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    operation_id: UUID,
) -> BingTokens:
    _credentials(client_id, client_secret)
    if not isinstance(refresh_token, str) or _SECRET.fullmatch(refresh_token) is None:
        raise BingProtocolError("BING_REFRESH_REJECTED")
    return _token_request(
        egress,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        operation_id,
        require_refresh=False,
    )


def discover_bing_sites(
    egress: SharedEgressProvider,
    access_token: str,
    verified_origin: str,
    operation_id: UUID,
) -> tuple[BingSite, ...]:
    origin = _origin(verified_origin)
    response = _api(egress, access_token, "GetUserSites", {}, operation_id, 128 * 1024)
    rows = _document(response.body, list)
    if len(rows) > 1000:
        raise BingProtocolError("BING_RESPONSE_REJECTED")
    sites = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("Url"), str)
            or type(row.get("IsVerified")) is not bool
        ):
            raise BingProtocolError("BING_RESPONSE_REJECTED")
        url = row["Url"]
        if len(url) > 2048:
            raise BingProtocolError("BING_RESPONSE_REJECTED")
        try:
            matches = _origin(url, allow_trailing_slash=True) == origin
        except BingProtocolError:
            matches = False
        sites.append(BingSite(url, row["IsVerified"], row["IsVerified"] and matches))
    return tuple(sites)


def import_bing_performance(
    egress: SharedEgressProvider,
    access_token: str,
    site_url: str,
    operation_id: UUID,
) -> BingObservation:
    _origin(site_url, allow_trailing_slash=True)
    response = _api(
        egress,
        access_token,
        "GetRankAndTrafficStats",
        {"siteUrl": site_url},
        operation_id,
        1024 * 1024,
    )
    rows = _document(response.body, list)
    if len(rows) > 5000:
        raise BingProtocolError("BING_RESPONSE_REJECTED")
    parsed = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or type(row.get("Clicks")) is not int
            or type(row.get("Impressions")) is not int
            or row["Clicks"] < 0
            or row["Impressions"] < 0
            or not isinstance(row.get("Date"), str)
            or _DATE.fullmatch(row["Date"]) is None
        ):
            raise BingProtocolError("BING_RESPONSE_REJECTED")
        parsed.append(
            {"date": row["Date"], "clicks": row["Clicks"], "impressions": row["Impressions"]}
        )
    return BingObservation(
        tuple(parsed),
        {
            "complete": False,
            "missing_data": "unknown",
            "source": "bing_webmaster",
            "coverage": "provider_returned_rows",
            "verticals": "all_provider_verticals",
        },
        hashlib.sha256(response.body).digest(),
    )


def import_bing_link_counts(
    egress: SharedEgressProvider,
    access_token: str,
    site_url: str,
    operation_id: UUID,
) -> BingObservation:
    _origin(site_url, allow_trailing_slash=True)
    response = _api(
        egress,
        access_token,
        "GetLinkCounts",
        {"siteUrl": site_url, "page": "0"},
        operation_id,
        1024 * 1024,
    )
    document = _document(response.body, dict)
    links = document.get("Links")
    total_pages = document.get("TotalPages")
    if (
        not isinstance(links, list)
        or len(links) > 5000
        or type(total_pages) is not int
        or total_pages < 0
    ):
        raise BingProtocolError("BING_RESPONSE_REJECTED")
    parsed = []
    for link in links:
        if (
            not isinstance(link, dict)
            or not isinstance(link.get("Url"), str)
            or type(link.get("Count")) is not int
            or link["Count"] < 0
            or len(link["Url"]) > 2048
        ):
            raise BingProtocolError("BING_RESPONSE_REJECTED")
        parsed.append({"target_url": link["Url"], "inbound_count": link["Count"]})
    return BingObservation(
        tuple(parsed),
        {
            "complete": False,
            "missing_data": "unknown",
            "source": "bing_webmaster",
            "page": 0,
            "total_pages": total_pages,
            "detail_links": "not_imported",
        },
        hashlib.sha256(response.body).digest(),
    )


def import_bing_page_performance(
    egress: SharedEgressProvider,
    access_token: str,
    site_url: str,
    operation_id: UUID,
) -> BingObservation:
    site_origin = _origin(site_url, allow_trailing_slash=True)
    if not isinstance(access_token, str) or _SECRET.fullmatch(access_token) is None:
        raise BingProtocolError("BING_ACCESS_REJECTED")
    response = _api(
        egress,
        access_token,
        "GetPageStats",
        {"siteUrl": site_url},
        operation_id,
        1024 * 1024,
        page_scope=BingPageReadScope(site_url, access_token),
    )
    if len(response.body) > 1024 * 1024:
        raise BingProtocolError("BING_RESPONSE_REJECTED")
    try:
        document = json.loads(response.body, object_pairs_hook=_unique_page_fields)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise BingProtocolError("BING_RESPONSE_REJECTED") from None
    if (
        not isinstance(document, dict)
        or set(document) != {"d"}
        or not isinstance(document["d"], list)
        or len(document["d"]) > 5000
        or access_token in json.dumps(document, ensure_ascii=False)
    ):
        raise BingProtocolError("BING_RESPONSE_REJECTED")
    parsed = []
    dropped = 0
    identities = set()
    fields = {"Query", "Date", "Clicks", "Impressions", "AvgClickPosition", "AvgImpressionPosition"}
    for row in document["d"]:
        if (
            not isinstance(row, dict)
            or set(row) - {"__type"} != fields
            or ("__type" in row and row["__type"] != "QueryStats:#Microsoft.Bing.Webmaster.Api")
            or any(
                type(row[key]) is not int or not 0 <= row[key] <= limit
                for key, limit in (
                    ("Clicks", 9223372036854775807),
                    ("Impressions", 9223372036854775807),
                    ("AvgClickPosition", 2147483647),
                    ("AvgImpressionPosition", 2147483647),
                )
            )
            or not isinstance(row["Query"], str)
            or not row["Query"].isascii()
            or "#" in row["Query"]
            or _page_url_contains_secret(row["Query"], access_token)
        ):
            raise BingProtocolError("BING_RESPONSE_REJECTED")
        validate_bing_page_date(row["Date"])
        try:
            page = normalize_crawl_url(row["Query"])
        except CrawlUrlRejected:
            raise BingProtocolError("BING_RESPONSE_REJECTED") from None
        if page.origin != site_origin:
            dropped += 1
            continue
        identity = (page.fetch_url, row["Date"])
        if identity in identities:
            raise BingProtocolError("BING_RESPONSE_REJECTED")
        identities.add(identity)
        parsed.append(
            {
                "page_url": page.fetch_url,
                "date": row["Date"],
                "clicks": row["Clicks"],
                "impressions": row["Impressions"],
                "avg_click_position": row["AvgClickPosition"],
                "avg_impression_position": row["AvgImpressionPosition"],
            }
        )
    return BingObservation(
        tuple(parsed),
        {
            "complete": False,
            "missing_data": "unknown",
            "source": "bing_webmaster",
            "coverage": "provider_returned_top_pages",
            "date_granularity": "unknown",
            "provider_update_frequency": "weekly",
            "dropped_out_of_site_rows": dropped,
        },
        hashlib.sha256(response.body).digest(),
    )


def validate_bing_page_date(value: object) -> None:
    match = _DATE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise BingProtocolError("BING_RESPONSE_REJECTED")
    offset = match[2][1:]
    if (
        int(offset[:2]) > 14
        or int(offset[2:]) > 59
        or (int(offset[:2]) == 14 and offset[2:] != "00")
    ):
        raise BingProtocolError("BING_RESPONSE_REJECTED")
    try:
        datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(match[1]))
    except OverflowError:
        raise BingProtocolError("BING_RESPONSE_REJECTED") from None


def _unique_page_fields(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_field")
        result[key] = value
    return result


def _page_url_contains_secret(value: str, secret: str) -> bool:
    for _ in range(8):
        if secret in value:
            return True
        decoded = unquote(value)
        if decoded == value:
            return False
        value = decoded
    return True


def import_bing_url_links(
    egress: SharedEgressProvider,
    access_token: str,
    site_url: str,
    target_url: str,
    operation_id: UUID,
) -> BingObservation:
    site_origin = _origin(site_url, allow_trailing_slash=True)
    if not isinstance(target_url, str) or not 1 <= len(target_url) <= 2048:
        raise BingProtocolError("BING_TARGET_REJECTED")
    target = urlsplit(target_url)
    if (
        not target_url.isascii()
        or target.scheme != "https"
        or f"{target.scheme}://{target.netloc}" != site_origin
        or target.username
        or target.password
        or target.query
        or target.fragment
    ):
        raise BingProtocolError("BING_TARGET_REJECTED")
    response = _api(
        egress,
        access_token,
        "GetUrlLinks",
        {"siteUrl": site_url, "link": json.dumps(target_url), "page": "0"},
        operation_id,
        1024 * 1024,
    )
    document = _document(response.body, dict)
    details = document.get("Details")
    total_pages = document.get("TotalPages")
    if (
        not isinstance(details, list)
        or len(details) > 5000
        or type(total_pages) is not int
        or total_pages < 0
    ):
        raise BingProtocolError("BING_RESPONSE_REJECTED")
    parsed = []
    for detail in details:
        if (
            not isinstance(detail, dict)
            or not isinstance(detail.get("Url"), str)
            or not isinstance(detail.get("AnchorText"), str)
            or len(detail["Url"]) > 2048
            or len(detail["AnchorText"]) > 1024
        ):
            raise BingProtocolError("BING_RESPONSE_REJECTED")
        parsed.append(
            {
                "target_url": target_url,
                "source_url": detail["Url"],
                "anchor_text": detail["AnchorText"],
            }
        )
    return BingObservation(
        tuple(parsed),
        {
            "complete": False,
            "missing_data": "unknown",
            "source": "bing_webmaster",
            "target_url": target_url,
            "page": 0,
            "total_pages": total_pages,
        },
        hashlib.sha256(response.body).digest(),
    )


def _token_request(
    egress: SharedEgressProvider, form: dict[str, str], operation_id: UUID, *, require_refresh: bool
) -> BingTokens:
    _egress(egress)
    try:
        response = egress.request_json(
            method="POST",
            url=BING_TOKEN_URL,
            profile=EgressProfile.BING_OAUTH_TOKEN,
            authorization=None,
            body=urlencode(form).encode("ascii"),
            operation_id=operation_id,
            timeout_seconds=10,
            max_response_bytes=8192,
        )
    except ProviderEgressUnavailable:
        raise BingProtocolError("BING_PROVIDER_UNAVAILABLE") from None
    if response.status_code in {400, 401, 403}:
        raise BingProtocolError("BING_REAUTH_REQUIRED")
    if response.status_code != 200:
        raise BingProtocolError(
            "BING_PROVIDER_UNAVAILABLE"
            if response.status_code >= 500 or response.status_code == 429
            else "BING_RESPONSE_REJECTED"
        )
    try:
        data = json.loads(response.body)
    except (ValueError, UnicodeDecodeError):
        raise BingProtocolError("BING_RESPONSE_REJECTED") from None
    if (
        not isinstance(data, dict)
        or str(data.get("token_type", "")).lower() != "bearer"
        or type(data.get("expires_in")) is not int
        or not 1 <= data["expires_in"] <= 86400
        or not isinstance(data.get("access_token"), str)
        or _SECRET.fullmatch(data["access_token"]) is None
    ):
        raise BingProtocolError("BING_RESPONSE_REJECTED")
    refresh = data.get("refresh_token")
    if (require_refresh or refresh is not None) and (
        not isinstance(refresh, str) or _SECRET.fullmatch(refresh) is None
    ):
        raise BingProtocolError("BING_RESPONSE_REJECTED")
    scope = data.get("scope")
    if scope is not None and (
        not isinstance(scope, str)
        or BING_READ_SCOPE not in scope.lower().split()
        or "webmaster.manage" in scope.lower().split()
    ):
        raise BingProtocolError("BING_REDUCED_SCOPE")
    return BingTokens(data["access_token"], refresh, data["expires_in"])


def _api(
    egress: SharedEgressProvider,
    token: str,
    method: str,
    params: dict[str, str],
    operation_id: UUID,
    max_bytes: int,
    *,
    page_scope: BingPageReadScope | None = None,
):
    _egress(egress)
    if not isinstance(token, str) or _SECRET.fullmatch(token) is None:
        raise BingProtocolError("BING_ACCESS_REJECTED")
    url = f"{BING_API_URL}/{method}"
    if params:
        url += "?" + urlencode(params)
    try:
        response = egress.request_json(
            method="GET",
            url=url,
            profile=EgressProfile.BING_API,
            authorization=f"Bearer {token}",
            operation_id=operation_id,
            timeout_seconds=15,
            max_response_bytes=max_bytes,
            **({"bing_page_scope": page_scope} if page_scope is not None else {}),
        )
    except ProviderEgressUnavailable:
        raise BingProtocolError("BING_PROVIDER_UNAVAILABLE") from None
    if response.status_code in {401, 403}:
        raise BingProtocolError("BING_REAUTH_REQUIRED")
    if response.status_code != 200:
        raise BingProtocolError(
            "BING_PROVIDER_UNAVAILABLE"
            if response.status_code >= 500 or response.status_code == 429
            else "BING_RESPONSE_REJECTED"
        )
    return response


def _document(body: bytes, expected: type) -> object:
    try:
        wrapper = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise BingProtocolError("BING_RESPONSE_REJECTED") from None
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("d"), expected):
        raise BingProtocolError("BING_RESPONSE_REJECTED")
    return wrapper["d"]


def _egress(egress: SharedEgressProvider) -> None:
    if not isinstance(egress, SharedEgressProvider) or egress.purpose != "connector":
        raise BingProtocolError("BING_EGRESS_REQUIRED")


def _redirect(uri: str) -> None:
    if not isinstance(uri, str) or not 12 <= len(uri) <= 2048 or not uri.isascii():
        raise BingProtocolError("BING_REDIRECT_REJECTED")
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise BingProtocolError("BING_REDIRECT_REJECTED")


def _credentials(client_id: str, client_secret: str) -> None:
    if (
        not isinstance(client_id, str)
        or not 16 <= len(client_id) <= 256
        or not client_id.isascii()
        or not isinstance(client_secret, str)
        or _SECRET.fullmatch(client_secret) is None
    ):
        raise BingProtocolError("BING_CLIENT_REJECTED")


def _origin(value: str, *, allow_trailing_slash: bool = False) -> str:
    if allow_trailing_slash and isinstance(value, str):
        parsed = urlsplit(value)
        if parsed.path == "/" and not parsed.query and not parsed.fragment:
            value = value[:-1]
    try:
        origin = normalize_public_site_origin(value)
    except (InvalidSiteOnboarding, TypeError, ValueError):
        raise BingProtocolError("BING_SITE_REJECTED") from None
    if not isinstance(origin, str):
        raise BingProtocolError("BING_SITE_REJECTED")
    return origin
