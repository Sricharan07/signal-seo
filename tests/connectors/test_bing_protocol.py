import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from signal_core.bing_protocol import (
    BING_API_URL,
    BING_READ_SCOPE,
    BING_TOKEN_URL,
    BingProtocolError,
    discover_bing_sites,
    exchange_bing_code,
    import_bing_link_counts,
    import_bing_performance,
    import_bing_url_links,
    new_bing_authorization,
    refresh_bing_token,
)
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import EgressProfile
from signal_core.shared_egress import (
    ProviderEgressResponse,
    SharedEgressProvider,
    SharedEgressRequest,
)


class FakeEgress(SharedEgressProvider):
    def __init__(self, body, status=200):
        object.__setattr__(self, "purpose", "connector")
        self.response = ProviderEgressResponse(
            status, "application/json", json.dumps(body).encode()
        )
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_authorization_exact_read_scope_and_state():
    auth = new_bing_authorization("synthetic-client-123456", "https://signal.example/bing/callback")
    params = parse_qs(urlsplit(auth.url).query)
    assert params["scope"] == [BING_READ_SCOPE]
    assert params["response_type"] == ["code"]
    assert params["state"] == [auth.state]
    assert len(auth.state_sha256) == 32
    assert auth.state not in repr(auth)
    with pytest.raises(BingProtocolError, match="BING_REDIRECT_REJECTED"):
        new_bing_authorization("synthetic-client-123456", "http://signal.example/callback")


def test_bing_profiles_reject_each_others_request_and_response_shapes():
    with pytest.raises(ValueError):
        SharedEgressRequest(
            "connector",
            EgressHttpRequest(
                "POST",
                f"{BING_API_URL}/GetUserSites",
                headers=(
                    ("accept", "application/json"),
                    ("content-type", "application/x-www-form-urlencoded"),
                ),
                body=b"grant_type=refresh_token",
            ),
            EgressProfile.BING_API,
            sensitive_body=True,
        )
    with pytest.raises(ValueError):
        SharedEgressRequest(
            "connector",
            EgressHttpRequest(
                "POST",
                BING_TOKEN_URL,
                headers=(
                    ("accept", "application/json"),
                    ("content-type", "application/x-www-form-urlencoded"),
                ),
                body=b"grant_type=authorization_code",
                accepted_media_types=("application/json", "text/html"),
            ),
            EgressProfile.BING_OAUTH_TOKEN,
            sensitive_body=True,
        )


def test_token_exchange_uses_sensitive_shared_egress_and_rejects_scope_change():
    egress = FakeEgress(
        {
            "access_token": "synthetic-access-token-12345",
            "refresh_token": "synthetic-refresh-token-12345",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "webmaster.read",
        }
    )
    token = exchange_bing_code(
        egress,
        "synthetic-client-123456",
        "synthetic-secret-123456",
        "synthetic-code-123456",
        "https://signal.example/bing/callback",
        uuid4(),
    )
    assert token.refresh_token == "synthetic-refresh-token-12345"
    assert token.access_token not in repr(token)
    assert egress.calls[0]["url"] == BING_TOKEN_URL
    assert egress.calls[0]["profile"] == EgressProfile.BING_OAUTH_TOKEN
    assert b"client_secret=" in egress.calls[0]["body"]
    egress.response = ProviderEgressResponse(
        200,
        "application/json",
        json.dumps(
            {
                "access_token": "synthetic-access-token-12345",
                "refresh_token": "synthetic-refresh-token-12345",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "webmaster.manage",
            }
        ).encode(),
    )
    with pytest.raises(BingProtocolError, match="BING_REDUCED_SCOPE"):
        exchange_bing_code(
            egress,
            "synthetic-client-123456",
            "synthetic-secret-123456",
            "synthetic-code-123456",
            "https://signal.example/bing/callback",
            uuid4(),
        )


def test_refresh_reauth_and_rotation():
    egress = FakeEgress(
        {
            "access_token": "synthetic-access-token-12345",
            "refresh_token": "synthetic-rotated-refresh-token-12345",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
    )
    token = refresh_bing_token(
        egress,
        "synthetic-client-123456",
        "synthetic-secret-123456",
        "synthetic-refresh-token-12345",
        uuid4(),
    )
    assert token.refresh_token == "synthetic-rotated-refresh-token-12345"
    egress.response = ProviderEgressResponse(401, "application/json", b"{}")
    with pytest.raises(BingProtocolError, match="BING_REAUTH_REQUIRED"):
        refresh_bing_token(
            egress,
            "synthetic-client-123456",
            "synthetic-secret-123456",
            "synthetic-refresh-token-12345",
            uuid4(),
        )


def test_discovery_requires_verified_exact_origin():
    egress = FakeEgress(
        {
            "d": [
                {"Url": "https://example.com/", "IsVerified": True},
                {"Url": "https://other.example/", "IsVerified": True},
                {"Url": "https://example.com/path/", "IsVerified": True},
                {"Url": "https://example.com", "IsVerified": False},
            ]
        }
    )
    sites = discover_bing_sites(
        egress, "synthetic-access-token-12345", "https://example.com", uuid4()
    )
    assert [site.eligible for site in sites] == [True, False, False, False]
    assert egress.calls[0]["url"] == f"{BING_API_URL}/GetUserSites"
    egress.response = ProviderEgressResponse(
        200, "application/json", b'{"d":[{"Url":"https://example.com/","IsVerified":"true"}]}'
    )
    with pytest.raises(BingProtocolError, match="BING_RESPONSE_REJECTED"):
        discover_bing_sites(egress, "synthetic-access-token-12345", "https://example.com", uuid4())


def test_performance_and_links_preserve_incomplete_source_specific_coverage():
    egress = FakeEgress(
        {"d": [{"Date": "/Date(1316156400000-0700)/", "Clicks": 15, "Impressions": 100}]}
    )
    performance = import_bing_performance(
        egress, "synthetic-access-token-12345", "https://example.com/", uuid4()
    )
    assert performance.rows[0]["clicks"] == 15
    assert performance.coverage["complete"] is False
    assert performance.coverage["missing_data"] == "unknown"
    assert performance.coverage["source"] == "bing_webmaster"
    assert "GetRankAndTrafficStats" in egress.calls[-1]["url"]
    egress.response = ProviderEgressResponse(
        200,
        "application/json",
        b'{"d":{"Links":[{"Url":"https://example.com/page","Count":14}],"TotalPages":3}}',
    )
    links = import_bing_link_counts(
        egress, "synthetic-access-token-12345", "https://example.com/", uuid4()
    )
    assert links.rows[0]["inbound_count"] == 14
    assert links.coverage["total_pages"] == 3
    assert links.coverage["complete"] is False
    assert "GetLinkCounts" in egress.calls[-1]["url"]
    egress.response = ProviderEgressResponse(
        200,
        "application/json",
        b'{"d":{"Details":[{"Url":"https://source.example/page","AnchorText":"hello"}],"TotalPages":2}}',
    )
    details = import_bing_url_links(
        egress,
        "synthetic-access-token-12345",
        "https://example.com/",
        "https://example.com/page",
        uuid4(),
    )
    assert details.rows[0]["source_url"] == "https://source.example/page"
    assert details.coverage["complete"] is False
    assert "GetUrlLinks" in egress.calls[-1]["url"]
    with pytest.raises(BingProtocolError, match="BING_TARGET_REJECTED"):
        import_bing_url_links(
            egress,
            "synthetic-access-token-12345",
            "https://example.com/",
            "https://other.example/page",
            uuid4(),
        )
    with pytest.raises(BingProtocolError, match="BING_TARGET_REJECTED"):
        import_bing_url_links(
            egress,
            "synthetic-access-token-12345",
            "https://example.com/",
            "https://example.com/page?email=private",
            uuid4(),
        )


def test_malformed_provider_data_and_wrong_gateway_fail_closed():
    egress = FakeEgress({"d": [{"Date": "today", "Clicks": 0, "Impressions": 1}]})
    with pytest.raises(BingProtocolError, match="BING_RESPONSE_REJECTED"):
        import_bing_performance(
            egress, "synthetic-access-token-12345", "https://example.com/", uuid4()
        )
    egress.response = ProviderEgressResponse(503, "application/json", b"{}")
    with pytest.raises(BingProtocolError, match="BING_PROVIDER_UNAVAILABLE"):
        discover_bing_sites(egress, "synthetic-access-token-12345", "https://example.com", uuid4())
    with pytest.raises(BingProtocolError, match="BING_EGRESS_REQUIRED"):
        discover_bing_sites(None, "synthetic-access-token-12345", "https://example.com", uuid4())
