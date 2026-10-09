import json
from dataclasses import replace
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from signal_core.bing_protocol import BING_API_URL, BingProtocolError, import_bing_page_performance
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import BingPageReadScope, EgressProfile
from signal_core.shared_egress import ProviderEgressResponse, SharedEgressRequest
from test_bing_protocol import FakeEgress

TOKEN = "synthetic-bing-page-access-token"
SITE = "https://site.example.invalid/"
DATE = "/Date(1316156400000-0700)/"


def page_row(**changes):
    return {
        "Query": SITE + "page?x=1&x=2",
        "Date": DATE,
        "Clicks": 15,
        "Impressions": 100,
        "AvgClickPosition": 18,
        "AvgImpressionPosition": 17,
        **changes,
    }


def test_pages_keep_exact_provider_fields_and_count_other_origins(caplog):
    egress = FakeEgress(
        {
            "d": [
                page_row(),
                page_row(Query="https://other.example.invalid/page"),
                page_row(Query="https://sub.site.example.invalid/page"),
                page_row(Query="http://site.example.invalid/page"),
            ]
        }
    )
    result = import_bing_page_performance(egress, TOKEN, SITE, uuid4())
    assert result.rows == (
        {
            "page_url": SITE + "page?x=1&x=2",
            "date": DATE,
            "clicks": 15,
            "impressions": 100,
            "avg_click_position": 18,
            "avg_impression_position": 17,
        },
    )
    assert result.coverage == {
        "complete": False,
        "missing_data": "unknown",
        "source": "bing_webmaster",
        "coverage": "provider_returned_top_pages",
        "date_granularity": "unknown",
        "provider_update_frequency": "weekly",
        "dropped_out_of_site_rows": 3,
    }
    assert egress.calls[0]["url"] == BING_API_URL + "/GetPageStats?" + urlencode({"siteUrl": SITE})
    assert egress.calls[0]["bing_page_scope"].site_url == SITE
    assert TOKEN not in repr(result) + repr(egress.calls[0]["bing_page_scope"]) + caplog.text


@pytest.mark.parametrize(
    "changes",
    [
        {"Clicks": True},
        {"Clicks": -1},
        {"Clicks": 9223372036854775808},
        {"AvgClickPosition": 2147483648},
        {"Clicks": "1"},
        {"AvgClickPosition": 1.5},
        {"AvgImpressionPosition": None},
        {"Impressions": False},
        {"Date": "today"},
        {"Date": "/Date(9999999999999999+0000)/"},
        {"Date": "/Date(1316156400000+1460)/"},
        {"Date": "/Date(1316156400000+1500)/"},
        {"Query": "https://site.example.invalid/%ZZ"},
        {"Query": SITE + "%0a"},
        {"Query": "https://user@site.example.invalid/page"},
        {"Query": SITE + "a#fragment"},
        {"Query": SITE + "a\\b"},
        {"Query": SITE + "a\nb"},
        {"Query": SITE + "a" * 2048},
        {"__type": "UnknownType"},
        {"unknown": "unexpected"},
        {"Query": SITE + TOKEN},
        {"Query": SITE + "%73" + TOKEN[1:]},
        {"Query": SITE + "%2573" + TOKEN[1:]},
    ],
)
def test_strict_page_validation_has_fixed_nonsecret_errors(changes, caplog):
    with pytest.raises(BingProtocolError) as caught:
        import_bing_page_performance(FakeEgress({"d": [page_row(**changes)]}), TOKEN, SITE, uuid4())
    assert str(caught.value) == "BING_RESPONSE_REJECTED"
    assert TOKEN not in str(caught.value) + caplog.text


@pytest.mark.parametrize(
    "document",
    [
        {"d": {}},
        {"d": [None]},
        {"d": [page_row(), page_row()]},
        {"d": [page_row()] * 5001},
        {"d": [], "token": TOKEN},
    ],
)
def test_reject_invalid_wrapper_duplicates_and_row_limit(document):
    with pytest.raises(BingProtocolError, match="BING_RESPONSE_REJECTED"):
        import_bing_page_performance(FakeEgress(document), TOKEN, SITE, uuid4())


def test_empty_missing_fields_duplicate_json_and_byte_limit():
    egress = FakeEgress({"d": []})
    result = import_bing_page_performance(egress, TOKEN, SITE, uuid4())
    assert result.rows == () and result.coverage["missing_data"] == "unknown"
    row = page_row()
    del row["AvgClickPosition"]
    for body in (
        json.dumps({"d": [row]}).encode(),
        b'{"d":[],"d":[]}',
        b'{"d":[]}' + b" " * 1048576,
    ):
        egress.response = ProviderEgressResponse(200, "application/json", body)
        with pytest.raises(BingProtocolError, match="BING_RESPONSE_REJECTED"):
            import_bing_page_performance(egress, TOKEN, SITE, uuid4())


def test_documented_int64_counts_are_not_truncated_to_int32():
    egress = FakeEgress({"d": [page_row(Clicks=2147483648, Impressions=9223372036854775807)]})
    result = import_bing_page_performance(egress, TOKEN, SITE, uuid4())
    assert result.rows[0]["clicks"] == 2147483648
    assert result.rows[0]["impressions"] == 9223372036854775807


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "BING_REAUTH_REQUIRED"),
        (403, "BING_REAUTH_REQUIRED"),
        (429, "BING_PROVIDER_UNAVAILABLE"),
        (503, "BING_PROVIDER_UNAVAILABLE"),
        (302, "BING_RESPONSE_REJECTED"),
    ],
)
def test_provider_failures_never_render_credential(status, code, caplog):
    with pytest.raises(BingProtocolError, match=code) as caught:
        import_bing_page_performance(FakeEgress({"error": TOKEN}, status), TOKEN, SITE, uuid4())
    assert TOKEN not in str(caught.value) + caplog.text


def outbound(url=None, *, token=TOKEN):
    return EgressHttpRequest(
        "GET",
        url or BING_API_URL + "/GetPageStats?" + urlencode({"siteUrl": SITE}),
        headers=(("accept", "application/json"), ("authorization", "Bearer " + token)),
        accepted_media_types=("application/json",),
        max_response_bytes=1048576,
        timeout_seconds=15,
    )


def test_page_profile_positive_and_exact_existing_routes():
    scope = BingPageReadScope(SITE, TOKEN)
    SharedEgressRequest("connector", outbound(), EgressProfile.BING_API, bing_page_scope=scope)
    for method, params in [
        ("GetUserSites", {}),
        ("GetRankAndTrafficStats", {"siteUrl": SITE}),
        ("GetLinkCounts", {"siteUrl": SITE, "page": "0"}),
        ("GetUrlLinks", {"siteUrl": SITE, "link": json.dumps(SITE + "page"), "page": "0"}),
    ]:
        url = BING_API_URL + "/" + method + ("?" + urlencode(params) if params else "")
        SharedEgressRequest("connector", outbound(url), EgressProfile.BING_API)


@pytest.mark.parametrize(
    "suffix",
    [
        "SubmitUrl?siteUrl=x",
        "GetPageQueryStats?siteUrl=x",
        "GetPageStats",
        "GetPageStats?siteUrl=",
        "GetPageStats?siteUrl=x",
        "GetPageStats?siteUrl=x&siteUrl=y",
        "GetPageStats?siteUrl=x&apikey=synthetic-api-key",
        "GetPageStats?siteUrl=x&startDate=2026-01-01",
        "GetPageStats?siteUrl=x#fragment",
        "%47etPageStats?siteUrl=x",
        "GetPageStats/../SubmitUrl?siteUrl=x",
        "GetLinkCounts?siteUrl=x&page=1",
    ],
)
def test_profile_route_and_query_negatives(suffix):
    with pytest.raises(ValueError):
        SharedEgressRequest(
            "connector",
            outbound(BING_API_URL + "/" + suffix),
            EgressProfile.BING_API,
            bing_page_scope=BingPageReadScope(SITE, TOKEN),
        )


def test_profile_wrong_credential_scope_method_origin_and_bounds():
    scope = BingPageReadScope(SITE, TOKEN)
    for http, supplied in [
        (outbound(), None),
        (outbound(token="synthetic-other-access-token"), scope),
        (replace(outbound(), max_response_bytes=1048577), scope),
        (replace(outbound(), timeout_seconds=16), scope),
        (
            EgressHttpRequest(
                "GET",
                "https://other.example.invalid/GetPageStats",
                headers=outbound().headers,
                accepted_media_types=("application/json",),
            ),
            scope,
        ),
    ]:
        with pytest.raises(ValueError):
            SharedEgressRequest("connector", http, EgressProfile.BING_API, bing_page_scope=supplied)
    with pytest.raises(ValueError):
        SharedEgressRequest("connector", outbound(), EgressProfile.GSC_API, bing_page_scope=scope)
    for changes in ({"method": "POST"}, {"body": b"{}"}):
        with pytest.raises(ValueError):
            SharedEgressRequest(
                "connector",
                replace(outbound(), **changes),
                EgressProfile.BING_API,
                bing_page_scope=scope,
            )
