import copy
import json
from datetime import UTC, datetime
from urllib.parse import urlencode
from uuid import uuid4

import httpx2
import pytest
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import EgressProfile, PageSpeedScope
from signal_core.pagespeed import (
    MAX_RESPONSE_BYTES,
    PageSpeedRejected,
    pagespeed_url,
    parse_pagespeed,
)
from signal_core.pagespeed_credentials import (
    OpenBaoPageSpeedCredentials,
    PageSpeedCredentialUnavailable,
)
from signal_core.shared_egress import SharedEgressRequest

URL = "https://example.invalid/product"
KEY = "synthetic-pagespeed-key-00000000"


def document(url=URL, strategy="mobile"):
    def field(locator):
        return {
            "id": locator,
            "metrics": {
                "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 4200},
                "INTERACTION_TO_NEXT_PAINT": {"percentile": 501},
                "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 26},
            },
            "collectionPeriod": {
                "firstDate": {"year": 2026, "month": 9, "day": 1},
                "lastDate": {"year": 2026, "month": 9, "day": 28},
            },
        }

    return {
        "id": url,
        "loadingExperience": field(url),
        "originLoadingExperience": field(url.rsplit("/", 1)[0]),
        "lighthouseResult": {
            "lighthouseVersion": "13.0.0",
            "requestedUrl": url,
            "finalUrl": url,
            "fetchTime": "2026-10-01T12:00:00Z",
            "configSettings": {"formFactor": strategy},
            "categories": {"performance": {"score": 0.95}},
            "audits": {
                "largest-contentful-paint": {"numericValue": 1200},
                "first-contentful-paint": {"numericValue": 900},
                "total-blocking-time": {"numericValue": 20},
                "speed-index": {"numericValue": 1800},
                "cumulative-layout-shift": {"numericValue": 0.01},
            },
        },
    }


def parse(value):
    return parse_pagespeed(
        json.dumps(value).encode(),
        url=URL,
        strategy="mobile",
        evidence_id=uuid4(),
        fetched_at=datetime.now(UTC),
    )


def outbound(url=None, method="GET", scope=None, **changes):
    scope = scope or PageSpeedScope("https://example.invalid", KEY)
    return SharedEgressRequest(
        "connector",
        EgressHttpRequest(
            method,
            url or pagespeed_url(scope, URL, "mobile"),
            headers=(("accept", "application/json"),),
            max_response_bytes=MAX_RESPONSE_BYTES,
            timeout_seconds=25,
            **changes,
        ),
        EgressProfile.PAGESPEED,
        pagespeed_scope=scope,
    )


@pytest.mark.parametrize("strategy", ["mobile", "desktop"])
@pytest.mark.parametrize("key", [None, KEY])
def test_closed_request_optional_key_and_redacted_evidence(strategy, key):
    scope = PageSpeedScope("https://example.invalid", key)
    request = outbound(pagespeed_url(scope, URL, strategy), scope=scope)
    assert request.credentialed is (key is not None)
    assert KEY not in request.evidence_url and KEY not in repr(request)
    replacement = PageSpeedScope(scope.verified_origin, "synthetic-replacement-key-00000")
    if key:
        assert (
            request.request_sha256
            == outbound(pagespeed_url(replacement, URL, strategy), scope=replacement).request_sha256
        )


@pytest.mark.parametrize(
    "change",
    [
        "host",
        "path",
        "extra",
        "other_page",
        "post",
        "duplicate",
        "missing_key",
        "unconfigured_key",
        "strategy",
        "category",
        "fragment",
        "http_page",
        "credentials",
        "subdomain",
    ],
)
def test_profile_negatives(change):
    scope = PageSpeedScope("https://example.invalid", KEY)
    url = pagespeed_url(scope, URL, "mobile")
    method = "GET"
    if change == "host":
        url = url.replace("www.googleapis.com", "other.invalid")
    elif change == "path":
        url = url.replace("runPagespeed", "sites")
    elif change == "extra":
        url += "&locale=en"
    elif change == "other_page":
        url = url.replace("example.invalid", "other.invalid")
    elif change == "post":
        method = "POST"
    elif change == "duplicate":
        url += "&strategy=desktop"
    elif change == "missing_key":
        url = url.split("&key=")[0]
    elif change == "unconfigured_key":
        scope = PageSpeedScope("https://example.invalid")
    elif change == "strategy":
        url = url.replace("strategy=mobile", "strategy=tablet")
    elif change == "category":
        url = url.replace("category=performance", "category=seo")
    elif change == "fragment":
        url += "#ignored"
    elif change == "http_page":
        url = url.replace("url=https", "url=http")
    else:
        page = (
            "https://owner@example.invalid/product"
            if change == "credentials"
            else "https://sub.example.invalid/product"
        )
        url = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed?" + urlencode(
            {"url": page, "strategy": "mobile", "category": "performance", "key": KEY}
        )
    with pytest.raises(ValueError) as error:
        outbound(url, method, scope)
    assert KEY not in str(error.value)


def test_lab_field_source_period_cls_scale_and_observation_only_findings():
    result = parse(document())
    assert result.lab["metrics"]["lcp"]["value"] == 1200
    assert "inp" not in result.lab["metrics"]
    assert result.field_url["metrics"]["lcp"]["value"] == 4200
    assert result.field_url["metrics"]["cls"]["value"] == 0.26
    assert result.field_origin["source"] == "origin"
    assert result.field_url["collection_period"]["first_date"] == "2026-09-01"
    assert len(result.findings) == 6
    assert all(
        f.source_id == result.evidence_id and "Observation only" in f.summary
        for f in result.findings
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"id": URL, "metrics": {}},
        {"id": URL, "metrics": {"INTERACTION_TO_NEXT_PAINT": {}}},
    ],
)
def test_insufficient_field_never_becomes_zero_or_origin_substitution(value):
    source = document()
    source["loadingExperience"] = value
    result = parse(source)
    assert result.field_url["status"] == "unavailable"
    assert result.field_url["metrics"]["lcp"]["value"] is None
    assert result.field_origin["status"] == "poor"
    assert all(".origin." in finding.key for finding in result.findings)


def test_origin_fallback_in_url_slot_and_unknown_window_are_explicit():
    source = document()
    source["loadingExperience"] = copy.deepcopy(source["originLoadingExperience"])
    source["loadingExperience"]["origin_fallback"] = True
    del source["originLoadingExperience"]["collectionPeriod"]
    result = parse(source)
    assert result.field_url["metrics"]["lcp"]["reason"] == "origin_fallback_not_page_data"
    assert result.field_origin["collection_period"]["state"] == "unavailable"
    assert result.field_origin["metrics"]["lcp"]["state"] == "available"


@pytest.mark.parametrize(
    ("lcp", "inp", "cls", "rating"),
    [(2500, 200, 10, "good"), (4000, 500, 25, "needs_improvement"), (4001, 501, 26, "poor")],
)
def test_documented_threshold_boundaries(lcp, inp, cls, rating):
    source = document()
    for key, value in zip(
        (
            "LARGEST_CONTENTFUL_PAINT_MS",
            "INTERACTION_TO_NEXT_PAINT",
            "CUMULATIVE_LAYOUT_SHIFT_SCORE",
        ),
        (lcp, inp, cls),
        strict=True,
    ):
        source["loadingExperience"]["metrics"][key]["percentile"] = value
    assert parse(source).field_url["status"] == rating


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "strategy",
        "final_url",
        "id",
        "bool",
        "negative",
        "nan",
        "period",
        "field_identity",
        "metrics_type",
        "lab_type",
        "score",
    ],
)
def test_strict_response_rejects_malformed_identity_and_metrics(change):
    source = document()
    lh = source["lighthouseResult"]
    if change == "version":
        lh["lighthouseVersion"] = "provider instructions"
    elif change == "strategy":
        lh["configSettings"]["formFactor"] = "desktop"
    elif change == "final_url":
        lh["finalUrl"] = "https://other.invalid/"
    elif change == "id":
        source["id"] = "https://other.invalid/"
    elif change in {"bool", "negative", "nan"}:
        source["loadingExperience"]["metrics"]["LARGEST_CONTENTFUL_PAINT_MS"]["percentile"] = {
            "bool": True,
            "negative": -1,
            "nan": float("nan"),
        }[change]
    elif change == "period":
        source["loadingExperience"]["collectionPeriod"]["lastDate"]["month"] = 13
    elif change == "field_identity":
        source["loadingExperience"]["id"] = "https://other.invalid/"
    elif change == "metrics_type":
        source["loadingExperience"]["metrics"] = []
    elif change == "lab_type":
        lh["audits"] = []
    else:
        lh["categories"]["performance"]["score"] = 2
    with pytest.raises(PageSpeedRejected, match="PSI_RESPONSE_REJECTED"):
        parse(source)


@pytest.mark.parametrize("body", [b"{", b"[]", b'{"id":1,"id":2}', b"x" * (MAX_RESPONSE_BYTES + 1)])
def test_invalid_and_oversized_json(body):
    with pytest.raises(PageSpeedRejected):
        parse_pagespeed(
            body, url=URL, strategy="mobile", evidence_id=uuid4(), fetched_at=datetime.now(UTC)
        )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_openbao_optional_key_does_not_call_network_and_configured_read_is_closed():
    client = OpenBaoPageSpeedCredentials(
        "https://bao.example.invalid", "synthetic-bao-reader-token"
    )
    assert await client.api_key() is None
    client = OpenBaoPageSpeedCredentials(
        client.base_url, client.token, "signal-psi/data/pagespeed/site"
    )

    def handler(request):
        assert request.url.path == "/v1/signal-psi/data/pagespeed/site"
        assert request.method == "GET"
        return httpx2.Response(
            200,
            json={
                "data": {
                    "data": {"api_key": KEY},
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                }
            },
        )

    assert await client.api_key(transport=httpx2.MockTransport(handler)) == KEY
    assert KEY not in repr(client)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure", ["missing", "destroyed", "version", "extra", "tls", "transport"]
)
async def test_configured_key_failure_never_falls_back_to_keyless_or_exposes_secrets(failure):
    client = OpenBaoPageSpeedCredentials(
        "https://bao.example.invalid",
        "synthetic-bao-reader-token",
        "signal-psi/data/pagespeed/site",
    )

    def handler(request):
        if failure == "transport":
            raise httpx2.ConnectError(KEY)
        if failure == "missing":
            return httpx2.Response(404, text=KEY)
        data = {"api_key": KEY}
        metadata = {"version": 1, "destroyed": False, "deletion_time": ""}
        if failure == "extra":
            data["instructions"] = KEY
        if failure == "destroyed":
            metadata["destroyed"] = True
        if failure == "version":
            metadata["version"] = True
        return httpx2.Response(200, json={"data": {"data": data, "metadata": metadata}})

    with pytest.raises(PageSpeedCredentialUnavailable) as error:
        await client.api_key(transport=httpx2.MockTransport(handler), verify=failure != "tls")
    assert KEY not in str(error.value)
