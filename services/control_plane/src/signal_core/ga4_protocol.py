"""Closed GA4 aggregated reads; provider metadata is evidence, not completeness."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from signal_core.egress_profiles import EgressProfile, Ga4ReadScope
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider
from signal_core.site_onboarding import InvalidSiteOnboarding, normalize_public_site_origin

GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
ADMIN_ORIGIN = "https://analyticsadmin.googleapis.com"
DATA_ORIGIN = "https://analyticsdata.googleapis.com"
METRICS = ("sessions", "engagedSessions", "engagementRate", "keyEvents")
ROW_LIMIT = 1000
MAX_ROWS = 5000


def _page_id(operation_id: UUID, page: int) -> UUID:
    if not isinstance(operation_id, UUID) or operation_id.version != 4:
        raise Ga4Error("GA4_OPERATION_REJECTED")
    if page == 0:
        return operation_id
    digest = hashlib.sha256(operation_id.bytes + page.to_bytes(4, "big")).digest()
    return UUID(bytes=digest[:16], version=4)


class Ga4Error(Exception):
    def __init__(self, code: str, operation_id: UUID | None = None) -> None:
        self.code = code
        self.operation_id = operation_id
        super().__init__(code)


@dataclass(frozen=True)
class Ga4Property:
    resource_name: str
    display_name: str


@dataclass(frozen=True)
class Ga4Report:
    rows: tuple[dict, ...]
    coverage: dict
    receipts: tuple[tuple[UUID, bytes], ...]


def _request(egress, scope, profile, url, operation_id, body=b""):
    if not isinstance(egress, SharedEgressProvider) or egress.purpose != "connector":
        raise Ga4Error("GA4_EGRESS_REQUIRED")
    try:
        response = egress.request_json(
            method="POST" if profile == EgressProfile.GA4_DATA else "GET",
            url=url,
            profile=profile,
            authorization=f"Bearer {scope.access_token}",
            ga4_scope=scope,
            body=body,
            operation_id=operation_id,
            timeout_seconds=10,
            max_response_bytes=128 * 1024,
        )
    except ProviderEgressUnavailable:
        raise Ga4Error("GA4_PROVIDER_UNAVAILABLE") from None
    if response.status_code in {401, 403}:
        raise Ga4Error("GA4_REAUTH_REQUIRED", operation_id)
    if response.status_code == 429 or response.status_code >= 500:
        raise Ga4Error("GA4_PROVIDER_UNAVAILABLE")
    if (
        response.status_code != 200
        or response.media_type != "application/json"
        or len(response.body) > 128 * 1024
    ):
        raise Ga4Error("GA4_RESPONSE_REJECTED")
    try:
        document = json.loads(response.body)
        json.dumps(document, allow_nan=False)
    except (ValueError, UnicodeError):
        raise Ga4Error("GA4_RESPONSE_REJECTED") from None
    if not isinstance(document, dict) or scope.access_token in response.body.decode("utf-8"):
        raise Ga4Error("GA4_RESPONSE_REJECTED")
    return document, hashlib.sha256(response.body).digest()


def _pages(egress, scope, operation_id):
    path = (
        "/v1beta/accountSummaries"
        if scope.property_resource_name is None
        else f"/v1beta/{scope.property_resource_name}/dataStreams"
    )
    token = None
    seen = set()
    for page in range(5):
        query = {"pageSize": "50"}
        if token:
            query["pageToken"] = token
        document, digest = _request(
            egress,
            scope,
            EgressProfile.GA4_ADMIN,
            ADMIN_ORIGIN + path + "?" + urlencode(query),
            _page_id(operation_id, page),
        )
        yield document, digest
        token = document.get("nextPageToken")
        if not token:
            return
        if (
            not isinstance(token, str)
            or re.fullmatch(r"[A-Za-z0-9_=.-]{1,512}", token) is None
            or token in seen
        ):
            raise Ga4Error("GA4_PAGINATION_REJECTED")
        seen.add(token)
    raise Ga4Error("GA4_DISCOVERY_LIMIT")


def discover_ga4_properties(
    *, egress, access_token: str, operation_id: UUID
) -> tuple[Ga4Property, ...]:
    properties = []
    seen = set()
    for document, _ in _pages(egress, Ga4ReadScope(access_token), operation_id):
        if not set(document).issubset({"accountSummaries", "nextPageToken"}):
            raise Ga4Error("GA4_RESPONSE_REJECTED")
        accounts = document.get("accountSummaries", [])
        if not isinstance(accounts, list) or len(accounts) > 50:
            raise Ga4Error("GA4_RESPONSE_REJECTED")
        for account in accounts:
            if not isinstance(account, dict) or not isinstance(
                account.get("propertySummaries", []), list
            ):
                raise Ga4Error("GA4_RESPONSE_REJECTED")
            for item in account.get("propertySummaries", []):
                if not isinstance(item, dict):
                    raise Ga4Error("GA4_RESPONSE_REJECTED")
                resource, name = item.get("property"), item.get("displayName", "")
                if (
                    not isinstance(resource, str)
                    or re.fullmatch(r"properties/[1-9][0-9]{0,19}", resource) is None
                    or resource in seen
                    or not isinstance(name, str)
                    or len(name) > 256
                ):
                    raise Ga4Error("GA4_RESPONSE_REJECTED")
                seen.add(resource)
                properties.append(Ga4Property(resource, name))
                if len(properties) > 100:
                    raise Ga4Error("GA4_DISCOVERY_LIMIT")
    return tuple(properties)


def verify_ga4_stream(
    *,
    egress,
    access_token: str,
    property_resource_name: str,
    verified_origin: str,
    operation_id: UUID,
) -> tuple[UUID, bytes]:
    try:
        origin = normalize_public_site_origin(verified_origin)
    except InvalidSiteOnboarding:
        raise Ga4Error("GA4_ORIGIN_REJECTED") from None
    matched = None
    scope = Ga4ReadScope(access_token, property_resource_name)
    for page, (document, digest) in enumerate(_pages(egress, scope, operation_id)):
        if not set(document).issubset({"dataStreams", "nextPageToken"}):
            raise Ga4Error("GA4_RESPONSE_REJECTED")
        streams = document.get("dataStreams", [])
        if not isinstance(streams, list) or len(streams) > 50:
            raise Ga4Error("GA4_RESPONSE_REJECTED")
        for stream in streams:
            if not isinstance(stream, dict):
                raise Ga4Error("GA4_RESPONSE_REJECTED")
            if stream.get("type") != "WEB_DATA_STREAM":
                continue
            data = stream.get("webStreamData")
            value = data.get("defaultUri") if isinstance(data, dict) else None
            if not isinstance(value, str) or len(value) > 2048:
                raise Ga4Error("GA4_RESPONSE_REJECTED")
            try:
                parts = urlsplit(value)
                candidate = normalize_public_site_origin(f"{parts.scheme}://{parts.netloc}")
            except (InvalidSiteOnboarding, ValueError):
                raise Ga4Error("GA4_RESPONSE_REJECTED") from None
            if parts.username or parts.password or parts.query or parts.fragment or "\\" in value:
                raise Ga4Error("GA4_RESPONSE_REJECTED")
            if candidate == origin:
                matched = (_page_id(operation_id, page), digest)
    if matched is None:
        raise Ga4Error("GA4_PROPERTY_ORIGIN_MISMATCH")
    return matched


def query_ga4_report(
    *,
    egress,
    access_token: str,
    property_resource_name: str,
    start_date: date,
    end_date: date,
    operation_id: UUID,
) -> Ga4Report:
    if (
        not isinstance(start_date, date)
        or not isinstance(end_date, date)
        or not 0 <= (end_date - start_date).days <= 92
    ):
        raise Ga4Error("GA4_QUERY_REJECTED")
    scope = Ga4ReadScope(access_token, property_resource_name)
    rows, pages, receipts = [], [], []
    total = None
    seen = set()
    for page in range(MAX_ROWS // ROW_LIMIT):
        offset = page * ROW_LIMIT
        body = json.dumps(
            {
                "dateRanges": [
                    {"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}
                ],
                "dimensions": [{"name": "pagePath"}],
                "metrics": [{"name": name} for name in METRICS],
                "limit": str(ROW_LIMIT),
                "offset": str(offset),
                "returnPropertyQuota": True,
                "orderBys": [{"dimension": {"dimensionName": "pagePath"}}],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        op = _page_id(operation_id, page)
        document, digest = _request(
            egress,
            scope,
            EgressProfile.GA4_DATA,
            f"{DATA_ORIGIN}/v1beta/{property_resource_name}:runReport",
            op,
            body,
        )
        batch, metadata, quota, count = _parse_report(document)
        if total is not None and total != count:
            raise Ga4Error("GA4_PAGINATION_CHANGED")
        total = count
        for row in batch:
            if row["page_path"] in seen:
                raise Ga4Error("GA4_PAGINATION_REJECTED")
            seen.add(row["page_path"])
        rows.extend(batch)
        if len(rows) > count or (len(batch) < ROW_LIMIT and len(rows) < count):
            raise Ga4Error("GA4_PAGINATION_REJECTED")
        pages.append(
            {
                "offset": offset,
                "returned_rows": len(batch),
                "row_count": count,
                "metadata": metadata,
                "property_quota": quota,
            }
        )
        receipts.append((op, digest))
        if len(rows) >= count:
            break
    coverage = {
        "schema_version": 1,
        "requested_start_date": start_date.isoformat(),
        "requested_end_date": end_date.isoformat(),
        "dimension": "pagePath",
        "metrics": list(METRICS),
        "returned_rows": len(rows),
        "row_limit": ROW_LIMIT,
        "max_rows": MAX_ROWS,
        "row_bound_reached": len(rows) < total,
        "pages": pages,
        "sampling": any(bool(p["metadata"].get("samplingMetadatas")) for p in pages),
        "thresholding": any(p["metadata"].get("subjectToThresholding", False) for p in pages),
        "other_row": "(other)" in seen
        or any(p["metadata"].get("dataLossFromOtherRow", False) for p in pages),
        "complete": False,
        "missing_data": "unknown_not_zero",
        "conversion_instrumentation": "not_validated",
        "consent_setup": "not_assessed",
    }
    return Ga4Report(tuple(rows), coverage, tuple(receipts))


def _parse_report(document):
    if not set(document).issubset(
        {
            "dimensionHeaders",
            "metricHeaders",
            "rows",
            "rowCount",
            "metadata",
            "propertyQuota",
            "kind",
        }
    ):
        raise Ga4Error("GA4_RESPONSE_REJECTED")
    if document.get("dimensionHeaders") != [{"name": "pagePath"}]:
        raise Ga4Error("GA4_RESPONSE_REJECTED")
    headers = document.get("metricHeaders")
    if (
        not isinstance(headers, list)
        or len(headers) != 4
        or any(not isinstance(h, dict) or set(h) != {"name", "type"} for h in headers)
    ):
        raise Ga4Error("GA4_RESPONSE_REJECTED")
    if [h["name"] for h in headers] != list(METRICS) or any(
        h["type"] not in {"TYPE_INTEGER", "TYPE_FLOAT"} for h in headers
    ):
        raise Ga4Error("GA4_RESPONSE_REJECTED")
    batch, count = document.get("rows", []), document.get("rowCount", 0)
    metadata, quota = document.get("metadata", {}), document.get("propertyQuota", {})
    if (
        not isinstance(batch, list)
        or len(batch) > ROW_LIMIT
        or type(count) is not int
        or not 0 <= count <= 1_000_000_000
        or not isinstance(metadata, dict)
        or not isinstance(quota, dict)
    ):
        raise Ga4Error("GA4_RESPONSE_REJECTED")
    for flag in ("subjectToThresholding", "dataLossFromOtherRow", "emptyReason"):
        if flag in metadata and (
            not isinstance(metadata[flag], str)
            if flag == "emptyReason"
            else type(metadata[flag]) is not bool
        ):
            raise Ga4Error("GA4_RESPONSE_REJECTED")
    samples = metadata.get("samplingMetadatas", [])
    if not isinstance(samples, list) or len(samples) > 1:
        raise Ga4Error("GA4_RESPONSE_REJECTED")
    rows = []
    for item in batch:
        try:
            if not isinstance(item, dict) or set(item) != {"dimensionValues", "metricValues"}:
                raise ValueError
            dims, values = item["dimensionValues"], item["metricValues"]
            if (
                len(dims) != 1
                or len(values) != 4
                or set(dims[0]) != {"value"}
                or any(set(v) != {"value"} for v in values)
            ):
                raise ValueError
            path = dims[0]["value"]
            if (
                not isinstance(path, str)
                or len(path) > 2048
                or (path != "(other)" and not path.startswith("/"))
            ):
                raise ValueError
            metrics = [float(v["value"]) for v in values]
            if (
                any(not isinstance(v["value"], str) for v in values)
                or any(not math.isfinite(v) or not 0 <= v <= 1e12 for v in metrics)
                or metrics[2] > 1
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, IndexError):
            raise Ga4Error("GA4_RESPONSE_REJECTED") from None
        rows.append({"page_path": path, **dict(zip(METRICS, metrics, strict=True))})
    return rows, metadata, quota, count
