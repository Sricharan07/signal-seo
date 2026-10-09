import json
from datetime import date
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import EgressProfile
from signal_core.gsc_oauth import (
    GSC_TOKEN_URL,
    GscOAuthError,
    exchange_gsc_code,
    new_gsc_authorization,
    query_gsc_analytics,
    refresh_gsc_access_token,
    revoke_gsc_refresh_token,
)
from signal_core.gsc_properties import (
    GSC_READONLY_SCOPE,
    GSC_SITES_URL,
    GscProtocolError,
    discover_gsc_properties_via_egress,
)
from signal_core.gsc_secrets import GscClientCredentials
from signal_core.shared_egress import (
    ProviderEgressResponse,
    SharedEgressProvider,
    SharedEgressRequest,
)


class FakeEgress(SharedEgressProvider):
    def __init__(self, response: ProviderEgressResponse):
        object.__setattr__(self, "purpose", "connector")
        self.response = response
        self.requests = []

    def request_json(self, **kwargs):
        self.requests.append(kwargs)
        return self.response


CLIENT = GscClientCredentials(
    "synthetic-client-123.apps.googleusercontent.com", "synthetic-client-secret-123456"
)


def test_authorization_url_has_exact_read_only_offline_pkce_contract():
    request = new_gsc_authorization(
        client_id=CLIENT.client_id, redirect_uri="https://signal.example/oauth/gsc/callback"
    )
    parsed = urlsplit(request.url)
    params = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert params["scope"] == [GSC_READONLY_SCOPE]
    assert params["access_type"] == ["offline"]
    assert params["include_granted_scopes"] == ["false"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"] == [request.code_challenge]
    assert params["state"] == [request.state]
    assert len(request.state_sha256) == 32
    assert request.verifier not in repr(request)


def test_form_secret_is_flagged_credentialed_without_retaining_it_in_digest_material():
    request = SharedEgressRequest(
        "connector",
        EgressHttpRequest(
            "POST",
            GSC_TOKEN_URL,
            headers=(
                ("accept", "application/json"),
                ("content-type", "application/x-www-form-urlencoded"),
            ),
            body=b"client_secret=synthetic-secret-12345",
            max_response_bytes=16 * 1024,
            timeout_seconds=10,
        ),
        EgressProfile.GOOGLE_OAUTH_TOKEN,
        sensitive_body=True,
    )
    assert request.credentialed is True
    assert len(request.request_sha256) == 32
    assert b"synthetic-secret" not in request.request_sha256


def test_exchange_uses_sensitive_form_body_and_rejects_reduced_scope():
    body = {
        "access_token": "synthetic-access-token-12345",
        "refresh_token": "synthetic-refresh-token-12345",
        "expires_in": 3600,
        "token_type": "Bearer",
        "scope": GSC_READONLY_SCOPE,
    }
    egress = FakeEgress(ProviderEgressResponse(200, "application/json", json.dumps(body).encode()))
    tokens = exchange_gsc_code(
        egress=egress,
        credentials=CLIENT,
        code="synthetic-code-12345",
        verifier="v" * 43,
        redirect_uri="https://signal.example/oauth/gsc/callback",
        operation_id=uuid4(),
    )
    request = egress.requests[0]
    assert request["url"] == GSC_TOKEN_URL
    assert request["profile"] == EgressProfile.GOOGLE_OAUTH_TOKEN
    assert request["authorization"] is None
    assert b"client_secret=" in request["body"]
    assert tokens.access_token not in repr(tokens)
    body["scope"] = "https://www.googleapis.com/auth/webmasters"
    egress.response = ProviderEgressResponse(200, "application/json", json.dumps(body).encode())
    with pytest.raises(GscOAuthError, match="GSC_REDUCED_SCOPE"):
        exchange_gsc_code(
            egress=egress,
            credentials=CLIENT,
            code="synthetic-code-12345",
            verifier="v" * 43,
            redirect_uri="https://signal.example/oauth/gsc/callback",
            operation_id=uuid4(),
        )


def test_refresh_reports_rotation_for_cas_persistence():
    egress = FakeEgress(
        ProviderEgressResponse(
            200,
            "application/json",
            json.dumps(
                {
                    "access_token": "synthetic-access-token-12345",
                    "refresh_token": "synthetic-rotated-token-12345",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            ).encode(),
        )
    )
    tokens = refresh_gsc_access_token(
        egress=egress,
        credentials=CLIENT,
        refresh_token="synthetic-refresh-token-12345",
        operation_id=uuid4(),
    )
    assert tokens.refresh_token == "synthetic-rotated-token-12345"


def test_upstream_revocation_error_never_claims_success():
    egress = FakeEgress(ProviderEgressResponse(400, "application/json", b"{}"))
    assert (
        revoke_gsc_refresh_token(
            egress=egress,
            refresh_token="synthetic-refresh-token-12345",
            operation_id=uuid4(),
        )
        is False
    )
    assert egress.requests[0]["profile"] == EgressProfile.GOOGLE_OAUTH_REVOKE


def test_discovery_stays_on_shared_gateway_and_rejects_wrong_property():
    egress = FakeEgress(
        ProviderEgressResponse(
            200,
            "application/json",
            json.dumps(
                {
                    "siteEntry": [
                        {"siteUrl": "https://www.example.com/", "permissionLevel": "siteOwner"},
                        {"siteUrl": "https://other.example/", "permissionLevel": "siteOwner"},
                    ],
                }
            ).encode(),
        )
    )
    properties = discover_gsc_properties_via_egress(
        access_token="synthetic-access-token-12345",
        verified_origin="https://www.example.com",
        egress=egress,
        operation_id=uuid4(),
    )
    assert egress.requests[0]["url"] == GSC_SITES_URL
    assert egress.requests[0]["method"] == "GET"
    assert [item.eligible for item in properties] == [True, False]
    egress.response = ProviderEgressResponse(403, "application/json", b"{}")
    with pytest.raises(GscProtocolError, match="GSC_AUTHORIZATION_REJECTED"):
        discover_gsc_properties_via_egress(
            access_token="synthetic-access-token-12345",
            verified_origin="https://www.example.com",
            egress=egress,
            operation_id=uuid4(),
        )


def test_analytics_preserves_unknown_coverage_instead_of_zero():
    egress = FakeEgress(
        ProviderEgressResponse(
            200,
            "application/json",
            json.dumps(
                {
                    "rows": [],
                    "responseAggregationType": "auto",
                    "metadata": {"first_incomplete_date": "2026-09-28"},
                }
            ).encode(),
        )
    )
    analytics = query_gsc_analytics(
        egress=egress,
        access_token="synthetic-access-token-12345",
        property_resource_name="https://www.example.com/",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 28),
        dimensions=("date", "query"),
        operation_id=uuid4(),
    )
    assert analytics.rows == ()
    assert analytics.coverage["complete"] is False
    assert analytics.coverage["missing_data"] == "unknown_not_zero"
    assert analytics.coverage["first_incomplete_date"] == "2026-09-28"
    assert "%3A%2F%2F" in egress.requests[0]["url"]


def test_analytics_rejects_malformed_rows_and_overbroad_query():
    egress = FakeEgress(
        ProviderEgressResponse(
            200,
            "application/json",
            json.dumps(
                {"rows": [{"keys": ["x"], "clicks": -1, "impressions": 2, "ctr": 0, "position": 1}]}
            ).encode(),
        )
    )
    with pytest.raises(GscOAuthError, match="GSC_RESPONSE_REJECTED"):
        query_gsc_analytics(
            egress=egress,
            access_token="synthetic-access-token-12345",
            property_resource_name="sc-domain:example.com",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 28),
            dimensions=("query",),
            operation_id=uuid4(),
        )
    with pytest.raises(GscOAuthError, match="GSC_QUERY_REJECTED"):
        query_gsc_analytics(
            egress=egress,
            access_token="synthetic-access-token-12345",
            property_resource_name="sc-domain:example.com",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 9, 28),
            dimensions=("query",),
            operation_id=uuid4(),
        )
