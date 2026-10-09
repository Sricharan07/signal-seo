import json
from datetime import date
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import EgressProfile, Ga4ReadScope, profile_headers
from signal_core.ga4_protocol import (
    GA4_SCOPE,
    METRICS,
    Ga4Error,
    discover_ga4_properties,
    query_ga4_report,
    verify_ga4_stream,
)
from signal_core.gsc_oauth import GscOAuthError, exchange_gsc_code, new_gsc_authorization
from signal_core.gsc_secrets import GscClientCredentials
from signal_core.shared_egress import (
    ProviderEgressResponse,
    SharedEgressProvider,
    SharedEgressRequest,
)

ACCESS = "synthetic-ga4-access-token"
CLIENT = GscClientCredentials(
    "synthetic-google-client.apps.googleusercontent.com", "synthetic-google-secret"
)


class Egress(SharedEgressProvider):
    def __init__(self, documents, status=200):
        object.__setattr__(self, "purpose", "connector")
        self.documents, self.requests, self.status = list(documents), [], status

    def request_json(self, **kwargs):
        self.requests.append(kwargs)
        profile = kwargs["profile"]
        SharedEgressRequest(
            "connector",
            EgressHttpRequest(
                kwargs["method"],
                kwargs["url"],
                headers=profile_headers(profile, kwargs["method"], kwargs["authorization"]),
                body=kwargs.get("body", b""),
                max_response_bytes=kwargs["max_response_bytes"],
                timeout_seconds=kwargs["timeout_seconds"],
            ),
            profile,
            sensitive_body=profile == EgressProfile.GOOGLE_OAUTH_TOKEN,
            ga4_scope=kwargs.get("ga4_scope"),
        )
        return ProviderEgressResponse(
            self.status, "application/json", json.dumps(self.documents.pop(0)).encode()
        )


def report(paths=(), count=None, metadata=None):
    return {
        "dimensionHeaders": [{"name": "pagePath"}],
        "metricHeaders": [{"name": name, "type": "TYPE_FLOAT"} for name in METRICS],
        "rows": [
            {
                "dimensionValues": [{"value": path}],
                "metricValues": [{"value": v} for v in ("3", "2", "0.666", "1")],
            }
            for path in paths
        ],
        "rowCount": len(paths) if count is None else count,
        "metadata": metadata or {},
        "propertyQuota": {"tokensPerDay": {"consumed": 1, "remaining": 999}},
    }


def query(egress, **kwargs):
    return query_ga4_report(
        egress=egress,
        access_token=ACCESS,
        property_resource_name="properties/123",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 28),
        operation_id=uuid4(),
        **kwargs,
    )


def test_ga4_fixed_scope_and_shared_pkce():
    auth = new_gsc_authorization(
        client_id=CLIENT.client_id,
        redirect_uri="https://dashboard.example.invalid/auth/ga4/callback",
        scope=GA4_SCOPE,
    )
    params = parse_qs(urlsplit(auth.url).query)
    assert params["scope"] == [GA4_SCOPE]
    assert params["include_granted_scopes"] == ["false"]
    assert params["code_challenge_method"] == ["S256"]
    assert auth.verifier not in repr(auth)


@pytest.mark.parametrize(
    "scope",
    [
        "https://www.googleapis.com/auth/analytics.edit",
        GA4_SCOPE + " openid",
        "",
        "https://www.googleapis.com/auth/webmasters.readonly",
    ],
)
def test_exchange_rejects_every_nonexact_ga4_scope(scope):
    egress = Egress(
        [
            {
                "access_token": ACCESS,
                "refresh_token": "synthetic-ga4-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": scope,
            }
        ]
    )
    with pytest.raises(GscOAuthError, match="GSC_REDUCED_SCOPE"):
        exchange_gsc_code(
            egress=egress,
            credentials=CLIENT,
            code="synthetic-google-code",
            verifier="synthetic-" + "v" * 43,
            redirect_uri="https://dashboard.example.invalid/auth/ga4/callback",
            operation_id=uuid4(),
            scope=GA4_SCOPE,
        )


def test_admin_pagination_and_origin_refusal():
    egress = Egress(
        [
            {
                "accountSummaries": [
                    {
                        "propertySummaries": [
                            {"property": "properties/123", "displayName": "Synthetic property"}
                        ]
                    }
                ],
                "nextPageToken": "synthetic-next",
            },
            {"accountSummaries": []},
        ]
    )
    assert (
        discover_ga4_properties(egress=egress, access_token=ACCESS, operation_id=uuid4())[
            0
        ].resource_name
        == "properties/123"
    )
    assert "pageToken=synthetic-next" in egress.requests[1]["url"]
    stream = {
        "dataStreams": [
            {
                "type": "WEB_DATA_STREAM",
                "webStreamData": {"defaultUri": "https://other.example.invalid/"},
            }
        ]
    }
    with pytest.raises(Ga4Error, match="GA4_PROPERTY_ORIGIN_MISMATCH"):
        verify_ga4_stream(
            egress=Egress([stream]),
            access_token=ACCESS,
            property_resource_name="properties/123",
            verified_origin="https://site.example.invalid",
            operation_id=uuid4(),
        )
    stream["dataStreams"][0]["webStreamData"]["defaultUri"] = "https://site.example.invalid/blog"
    assert (
        len(
            verify_ga4_stream(
                egress=Egress([stream]),
                access_token=ACCESS,
                property_resource_name="properties/123",
                verified_origin="https://site.example.invalid",
                operation_id=uuid4(),
            )[1]
        )
        == 32
    )


def test_sampling_threshold_and_other_metadata_preserved_verbatim():
    metadata = {
        "samplingMetadatas": [{"samplesReadCount": "50", "samplingSpaceSize": "100"}],
        "subjectToThresholding": True,
        "dataLossFromOtherRow": True,
        "timeZone": "UTC",
        "currencyCode": "USD",
    }
    result = query(Egress([report(["(other)", "/"], metadata=metadata)]))
    assert result.coverage["pages"][0]["metadata"] == metadata
    assert all(result.coverage[k] for k in ("sampling", "thresholding", "other_row"))
    assert result.coverage["complete"] is False
    assert result.coverage["conversion_instrumentation"] == "not_validated"


def test_bounded_pagination_never_claims_complete():
    egress = Egress(
        [report([f"/{i}" for i in range(p * 1000, (p + 1) * 1000)], count=6000) for p in range(5)]
    )
    result = query(egress)
    assert len(result.rows) == 5000 and len(egress.requests) == 5
    assert result.coverage["row_bound_reached"] and not result.coverage["complete"]
    assert [json.loads(r["body"])["offset"] for r in egress.requests] == [
        "0",
        "1000",
        "2000",
        "3000",
        "4000",
    ]


@pytest.mark.parametrize(
    "document",
    [
        report(["/"], count=2000),
        report(["/"] * 1001),
        report(["not-a-path"]),
        report([ACCESS]),
        {**report(), "access_token": ACCESS},
        {**report(), "rowCount": True},
    ],
)
def test_bad_rows_and_pagination_fail_without_payload(document):
    with pytest.raises(Ga4Error) as error:
        query(Egress([document]))
    assert ACCESS not in str(error.value)


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "GA4_REAUTH_REQUIRED"),
        (403, "GA4_REAUTH_REQUIRED"),
        (429, "GA4_PROVIDER_UNAVAILABLE"),
        (503, "GA4_PROVIDER_UNAVAILABLE"),
        (302, "GA4_RESPONSE_REJECTED"),
    ],
)
def test_provider_failures_are_fixed(status, code):
    with pytest.raises(Ga4Error, match=code):
        query(Egress([{"message": ACCESS}], status))


@pytest.mark.parametrize(
    "changes",
    [
        {"method": "DELETE"},
        {"url": "https://analyticsdata.googleapis.com/v1beta/properties/456:runReport"},
        {"url": "https://analyticsdata.googleapis.com/v1beta/properties/123:batchRunReports"},
        {"authorization": "Bearer synthetic-other-token"},
        {"max_response_bytes": 131073},
        {"timeout_seconds": 11},
        {"ga4_scope": None},
    ],
)
def test_data_profile_negatives(changes):
    egress = Egress([report()])
    query(egress)
    request = {**egress.requests[0], **changes}
    with pytest.raises(ValueError):
        Egress([report()]).request_json(**request)


@pytest.mark.parametrize(
    "changes",
    [
        {"metrics": [{"name": "totalUsers"}]},
        {"limit": "1001"},
        {"offset": "5000"},
        {"dimensionFilter": {}},
        {"dateRanges": [{"startDate": "2020-01-01", "endDate": "2026-01-01"}]},
    ],
)
def test_data_profile_rejects_unbounded_and_unapproved_shapes(changes):
    egress = Egress([report()])
    query(egress)
    request = egress.requests[0]
    body = {**json.loads(request["body"]), **changes}
    with pytest.raises(ValueError):
        Egress([report()]).request_json(**{**request, "body": json.dumps(body).encode()})


def test_admin_cannot_read_other_google_routes_or_borrow_token():
    for url in (
        "https://analyticsadmin.googleapis.com/v1beta/properties/123/dataStreams/123",
        "https://analyticsadmin.googleapis.com/v1beta/properties/123/measurementProtocolSecrets",
        "https://www.googleapis.com/drive/v3/files",
    ):
        with pytest.raises(ValueError):
            Egress([{}]).request_json(
                method="GET",
                url=url,
                profile=EgressProfile.GA4_ADMIN,
                authorization=f"Bearer {ACCESS}",
                ga4_scope=Ga4ReadScope(ACCESS, "properties/123"),
                operation_id=uuid4(),
                max_response_bytes=131072,
                timeout_seconds=10,
            )
