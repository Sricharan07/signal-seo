"""Fixed-scope Search Console OAuth and bounded analytics protocol."""

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID

from signal_core.egress_profiles import EgressProfile
from signal_core.gsc_properties import GSC_READONLY_SCOPE
from signal_core.gsc_secrets import GscClientCredentials
from signal_core.shared_egress import (
    ProviderEgressResponse,
    ProviderEgressUnavailable,
    SharedEgressProvider,
)

GSC_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GSC_TOKEN_URL = "https://oauth2.googleapis.com/token"
GSC_REVOCATION_URL = "https://oauth2.googleapis.com/revoke"
GSC_ANALYTICS_URL = "https://www.googleapis.com/webmasters/v3/sites"
_TOKEN = re.compile(r"[!-~]{16,4096}")
_STATE = re.compile(r"[A-Za-z0-9_-]{43}")
_VERIFIER = re.compile(r"[A-Za-z0-9_-]{43,128}")
_DIMENSIONS = frozenset({"date", "query", "page", "country", "device"})
GA4_READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"


def _google_read_scope(scope: str) -> str:
    if scope not in {GSC_READONLY_SCOPE, GA4_READONLY_SCOPE, DRIVE_FILE_SCOPE}:
        raise GscOAuthError("GSC_REDUCED_SCOPE")
    return scope


class GscOAuthError(Exception):
    """Fixed failure with no credential or provider payload."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class GscAuthorization:
    url: str
    state: str = field(repr=False)
    state_sha256: bytes = field(repr=False)
    verifier: str = field(repr=False)
    code_challenge: str


@dataclass(frozen=True, repr=False)
class GscTokens:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_in: int


@dataclass(frozen=True, repr=False)
class GscAnalytics:
    rows: tuple[dict, ...]
    coverage: dict
    response_sha256: bytes
    aggregation_type: str


def new_gsc_authorization(
    *, client_id: str, redirect_uri: str, scope: str = GSC_READONLY_SCOPE
) -> GscAuthorization:
    """Generate one offline, non-incremental S256 request with a closed connector scope."""
    if (
        not isinstance(client_id, str)
        or not client_id.endswith(".apps.googleusercontent.com")
        or not 16 <= len(client_id) <= 256
    ):
        raise GscOAuthError("GSC_CLIENT_CONFIGURATION_REJECTED")
    _validated_redirect(redirect_uri)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _google_read_scope(scope),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return GscAuthorization(
        f"{GSC_AUTHORIZATION_URL}?{urlencode(params)}",
        state,
        hashlib.sha256(state.encode()).digest(),
        verifier,
        challenge,
    )


def exchange_gsc_code(
    *,
    egress: SharedEgressProvider,
    credentials: GscClientCredentials,
    code: str,
    verifier: str,
    redirect_uri: str,
    operation_id: UUID,
    scope: str = GSC_READONLY_SCOPE,
) -> GscTokens:
    if not isinstance(code, str) or _TOKEN.fullmatch(code) is None:
        raise GscOAuthError("GSC_CODE_REJECTED")
    if not isinstance(verifier, str) or _VERIFIER.fullmatch(verifier) is None:
        raise GscOAuthError("GSC_VERIFIER_REJECTED")
    _validated_redirect(redirect_uri)
    body = urlencode(
        {
            "code": code,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
    ).encode("ascii")
    _google_read_scope(scope)
    response = _token_request(egress, body, operation_id)
    return _parse_tokens(response, require_refresh=True, require_scope=True, expected_scope=scope)


def refresh_gsc_access_token(
    *,
    egress: SharedEgressProvider,
    credentials: GscClientCredentials,
    refresh_token: str,
    operation_id: UUID,
    scope: str = GSC_READONLY_SCOPE,
) -> GscTokens:
    if not isinstance(refresh_token, str) or _TOKEN.fullmatch(refresh_token) is None:
        raise GscOAuthError("GSC_REFRESH_TOKEN_REJECTED")
    body = urlencode(
        {
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("ascii")
    _google_read_scope(scope)
    response = _token_request(egress, body, operation_id)
    return _parse_tokens(
        response,
        require_refresh=False,
        require_scope=scope == DRIVE_FILE_SCOPE,
        expected_scope=scope,
    )


def revoke_gsc_refresh_token(
    *,
    egress: SharedEgressProvider,
    refresh_token: str,
    operation_id: UUID,
) -> bool:
    """Best-effort upstream revocation after local authority has been denied."""
    if not isinstance(refresh_token, str) or _TOKEN.fullmatch(refresh_token) is None:
        raise GscOAuthError("GSC_REFRESH_TOKEN_REJECTED")
    if not isinstance(egress, SharedEgressProvider) or egress.purpose != "connector":
        raise GscOAuthError("GSC_EGRESS_REQUIRED")
    try:
        response = egress.request_json(
            method="POST",
            url=GSC_REVOCATION_URL,
            profile=EgressProfile.GOOGLE_OAUTH_REVOKE,
            authorization=None,
            body=urlencode({"token": refresh_token}).encode("ascii"),
            operation_id=operation_id,
            timeout_seconds=10,
            max_response_bytes=4096,
        )
    except ProviderEgressUnavailable:
        return False
    return response.status_code == 200


def query_gsc_analytics(
    *,
    egress: SharedEgressProvider,
    access_token: str,
    property_resource_name: str,
    start_date: date,
    end_date: date,
    dimensions: tuple[str, ...],
    operation_id: UUID,
    search_type: str = "web",
    data_state: str = "all",
) -> GscAnalytics:
    if not isinstance(access_token, str) or _TOKEN.fullmatch(access_token) is None:
        raise GscOAuthError("GSC_ACCESS_TOKEN_REJECTED")
    if (
        not isinstance(property_resource_name, str)
        or not 1 <= len(property_resource_name) <= 2048
        or not property_resource_name.isascii()
    ):
        raise GscOAuthError("GSC_PROPERTY_REJECTED")
    if (
        not isinstance(start_date, date)
        or not isinstance(end_date, date)
        or start_date > end_date
        or (end_date - start_date).days > 92
        or search_type not in {"web", "image", "video", "news", "discover"}
        or data_state not in {"all", "final"}
        or not isinstance(dimensions, tuple)
        or not 1 <= len(dimensions) <= 5
        or len(set(dimensions)) != len(dimensions)
        or not set(dimensions).issubset(_DIMENSIONS)
    ):
        raise GscOAuthError("GSC_QUERY_REJECTED")
    body = json.dumps(
        {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": dimensions,
            "type": search_type,
            "dataState": data_state,
            "aggregationType": "auto",
            "rowLimit": 5000,
            "startRow": 0,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    url = f"{GSC_ANALYTICS_URL}/{quote(property_resource_name, safe='')}/searchAnalytics/query"
    try:
        response = egress.request_json(
            method="POST",
            url=url,
            profile=EgressProfile.GSC_API,
            authorization=f"Bearer {access_token}",
            body=body,
            operation_id=operation_id,
            timeout_seconds=20,
            max_response_bytes=5 * 1024 * 1024,
        )
    except ProviderEgressUnavailable:
        raise GscOAuthError("GSC_PROVIDER_UNAVAILABLE") from None
    if response.status_code in {401, 403}:
        raise GscOAuthError("GSC_REAUTH_REQUIRED")
    if response.status_code == 429 or response.status_code >= 500:
        raise GscOAuthError("GSC_PROVIDER_UNAVAILABLE")
    if response.status_code != 200 or response.media_type != "application/json":
        raise GscOAuthError("GSC_RESPONSE_REJECTED")
    return _parse_analytics(response.body, dimensions, start_date, end_date)


def _token_request(
    egress: SharedEgressProvider,
    body: bytes,
    operation_id: UUID,
) -> ProviderEgressResponse:
    if not isinstance(egress, SharedEgressProvider) or egress.purpose != "connector":
        raise GscOAuthError("GSC_EGRESS_REQUIRED")
    try:
        response = egress.request_json(
            method="POST",
            url=GSC_TOKEN_URL,
            profile=EgressProfile.GOOGLE_OAUTH_TOKEN,
            authorization=None,
            body=body,
            operation_id=operation_id,
            timeout_seconds=10,
            max_response_bytes=16 * 1024,
        )
    except ProviderEgressUnavailable:
        raise GscOAuthError("GSC_PROVIDER_UNAVAILABLE") from None
    if response.status_code in {400, 401, 403}:
        raise GscOAuthError("GSC_REAUTH_REQUIRED")
    if response.status_code == 429 or response.status_code >= 500:
        raise GscOAuthError("GSC_PROVIDER_UNAVAILABLE")
    if response.status_code != 200 or response.media_type != "application/json":
        raise GscOAuthError("GSC_RESPONSE_REJECTED")
    return response


def _parse_tokens(
    response: ProviderEgressResponse,
    *,
    require_refresh: bool,
    require_scope: bool,
    expected_scope: str = GSC_READONLY_SCOPE,
) -> GscTokens:
    document = _json_object(response.body)
    access = document.get("access_token")
    refresh = document.get("refresh_token")
    duration = document.get("expires_in")
    scope = document.get("scope")
    if (
        not isinstance(access, str)
        or _TOKEN.fullmatch(access) is None
        or (require_refresh and (not isinstance(refresh, str) or _TOKEN.fullmatch(refresh) is None))
        or (
            refresh is not None
            and (not isinstance(refresh, str) or _TOKEN.fullmatch(refresh) is None)
        )
        or isinstance(duration, bool)
        or not isinstance(duration, int)
        or not 60 <= duration <= 86400
        or document.get("token_type") != "Bearer"
    ):
        raise GscOAuthError("GSC_RESPONSE_REJECTED")
    if (require_scope and scope != expected_scope) or (
        scope is not None and scope != expected_scope
    ):
        raise GscOAuthError("GSC_REDUCED_SCOPE")
    return GscTokens(access, refresh, duration)


def _parse_analytics(
    body: bytes,
    dimensions: tuple[str, ...],
    start_date: date,
    end_date: date,
) -> GscAnalytics:
    document = _json_object(body)
    if not set(document).issubset({"rows", "responseAggregationType", "metadata"}):
        raise GscOAuthError("GSC_RESPONSE_REJECTED")
    raw_rows = document.get("rows", [])
    aggregation = document.get("responseAggregationType", "auto")
    metadata = document.get("metadata", {})
    if (
        not isinstance(raw_rows, list)
        or len(raw_rows) > 5000
        or aggregation not in {"auto", "byPage", "byProperty"}
        or not isinstance(metadata, dict)
        or not set(metadata).issubset({"first_incomplete_date", "first_incomplete_hour"})
    ):
        raise GscOAuthError("GSC_RESPONSE_REJECTED")
    rows = []
    for row in raw_rows:
        if not isinstance(row, dict) or set(row) != {
            "keys",
            "clicks",
            "impressions",
            "ctr",
            "position",
        }:
            raise GscOAuthError("GSC_RESPONSE_REJECTED")
        keys = row["keys"]
        if (
            not isinstance(keys, list)
            or len(keys) != len(dimensions)
            or not all(isinstance(key, str) and len(key) <= 2048 for key in keys)
        ):
            raise GscOAuthError("GSC_RESPONSE_REJECTED")
        for name in ("clicks", "impressions", "ctr", "position"):
            value = row[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not (0 <= value <= 1e12)
            ):
                raise GscOAuthError("GSC_RESPONSE_REJECTED")
        rows.append(row)
    first_incomplete = metadata.get("first_incomplete_date")
    if first_incomplete is not None:
        try:
            incomplete_date = date.fromisoformat(first_incomplete)
        except (TypeError, ValueError):
            raise GscOAuthError("GSC_RESPONSE_REJECTED") from None
        if not start_date <= incomplete_date <= end_date:
            raise GscOAuthError("GSC_RESPONSE_REJECTED")
    coverage = {
        "schema_version": 1,
        "requested_start_date": start_date.isoformat(),
        "requested_end_date": end_date.isoformat(),
        "returned_rows": len(rows),
        "row_limit": 5000,
        "start_row": 0,
        "filters": [],
        "complete": False,
        "missing_data": "unknown_not_zero",
        "date_presence": "not_exhaustive",
        "top_rows_only": True,
        "first_incomplete_date": first_incomplete,
        "source_timezone": "America/Los_Angeles",
    }
    return GscAnalytics(tuple(rows), coverage, hashlib.sha256(body).digest(), aggregation)


def _json_object(body: bytes) -> dict:
    try:
        document = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GscOAuthError("GSC_RESPONSE_REJECTED") from None
    if not isinstance(document, dict):
        raise GscOAuthError("GSC_RESPONSE_REJECTED")
    return document


def _validated_redirect(value: str) -> None:
    if not isinstance(value, str) or not value.isascii() or len(value) > 2048:
        raise GscOAuthError("GSC_REDIRECT_REJECTED")
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        raise GscOAuthError("GSC_REDIRECT_REJECTED") from None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or not parts.path.startswith("/")
        or parts.query
        or parts.fragment
        or "\\" in value
        or any(ord(ch) < 33 for ch in value)
    ):
        raise GscOAuthError("GSC_REDIRECT_REJECTED")
