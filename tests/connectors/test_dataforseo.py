import asyncio
import base64
import json
from dataclasses import replace
from uuid import uuid4

import httpx2
import pytest
from signal_core.crawl_http import EgressHttpRequest
from signal_core.dataforseo import DataForSeoQuery, parse_response, reported_cost
from signal_core.dataforseo_credentials import DataForSeoUnavailable, OpenBaoDataForSeoCredentials
from signal_core.egress_profiles import DataForSeoCredentialScope, EgressProfile, profile_headers
from signal_core.shared_egress import SharedEgressRequest

LOGIN = "synthetic-login@example.invalid"
PASSWORD = "synthetic-dataforseo-password"
AUTH = "Basic " + base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()


@pytest.mark.parametrize(
    "url,method,authorization,allowed",
    [
        ("https://api.dataforseo.com/robots.txt", "GET", None, True),
        ("https://api.dataforseo.com/robots.txt?extra=1", "GET", None, False),
        ("https://api.dataforseo.com/v3/backlinks/summary/live", "GET", None, False),
        ("https://api.dataforseo.com/robots.txt", "POST", None, False),
        ("https://other.invalid/robots.txt", "GET", None, False),
        ("https://api.dataforseo.com/robots.txt", "GET", AUTH, False),
    ],
)
def test_new_uncredentialed_robots_profile_is_one_exact_read(url, method, authorization, allowed):
    def request():
        return SharedEgressRequest(
            "crawl",
            EgressHttpRequest(
                method,
                url,
                headers=profile_headers(
                    EgressProfile.DATAFORSEO_ROBOTS, method, authorization=authorization
                ),
                accepted_media_types=(
                    "text/plain",
                    "text/html",
                    "application/octet-stream",
                    "application/json",
                ),
                max_response_bytes=16384,
                timeout_seconds=5,
            ),
            EgressProfile.DATAFORSEO_ROBOTS,
        )

    if allowed:
        assert request().credentialed is False
    else:
        with pytest.raises(ValueError):
            request()


def response(query):
    if query.kind == "backlinks":
        result = {"target": query.subject, "backlinks": 300, "referring_domains": 10, "rank": 123}
    elif query.kind == "volume":
        result = {
            "keyword": query.subject,
            "location_code": query.location_code,
            "language_code": query.language_code,
            "search_volume": 200,
        }
    else:
        result = {
            "keyword": query.subject,
            "location_code": query.location_code,
            "language_code": query.language_code,
            "items_count": 1,
            "items": [
                {
                    "type": "organic",
                    "rank_group": 1,
                    "domain": "competitor.example",
                    "url": "https://competitor.example/page",
                }
            ],
        }
    return {
        "status_code": 20000,
        "cost": 0.09,
        "tasks_count": 1,
        "tasks_error": 0,
        "tasks": [
            {
                "id": "synthetic-task",
                "status_code": 20000,
                "cost": 0.09,
                "path": query.endpoint.split(".com/")[1].split("/"),
                "data": json.loads(query.body())[0],
                "result_count": 1,
                "result": [result],
            }
        ],
    }


@pytest.mark.parametrize("kind", ["serp", "volume", "backlinks"])
def test_strict_typed_results(kind):
    query = DataForSeoQuery(
        kind,
        "competitor.example" if kind == "backlinks" else "widgets",
        None if kind == "backlinks" else 2840,
        None if kind == "backlinks" else "en",
    )
    result = parse_response(query, json.dumps(response(query)).encode())
    assert result.cost_micros == 90000
    assert result.data["subject"] == query.subject
    assert result.data["kind"] == kind
    assert reported_cost(query, json.dumps(response(query)).encode()) == 90000


@pytest.mark.parametrize(
    "change",
    [
        {"status_code": 20001},
        {"status_code": True},
        {"tasks_count": True},
        {"cost": -1},
        {"cost": "0.09"},
        {"cost": 0.0000001},
        {"cost": float("nan")},
        {"tasks": []},
        {"tasks": [None]},
        {"tasks_error": 1},
    ],
)
def test_malformed_envelopes(change):
    query = DataForSeoQuery("volume", "widgets", 2840, "en")
    with pytest.raises(DataForSeoUnavailable, match="RESPONSE_REJECTED"):
        parse_response(query, json.dumps({**response(query), **change}).encode())


@pytest.mark.parametrize(
    "change",
    [
        {"search_volume": True},
        {"search_volume": -1},
        {"search_volume": 1.2},
        {"keyword": "wrong"},
        {"location_code": 1},
        {"language_code": "fr"},
    ],
)
def test_volume_identity_and_numeric_validation(change):
    query = DataForSeoQuery("volume", "widgets", 2840, "en")
    document = response(query)
    document["tasks"][0]["result"][0].update(change)
    with pytest.raises(DataForSeoUnavailable):
        parse_response(query, json.dumps(document).encode())
    assert reported_cost(query, json.dumps(document).encode()) == 90000


@pytest.mark.parametrize(
    "body", [b"x", b"[]", b"{}", b"x" * 131073, b'{"cost":0,"cost":1}', b"[" * 2000]
)
def test_bounded_duplicate_and_invalid_json(body):
    with pytest.raises(DataForSeoUnavailable):
        parse_response(DataForSeoQuery("serp", "widgets", 2840, "en"), body)


@pytest.mark.parametrize(
    "query",
    [
        DataForSeoQuery("serp", "site:example.com", 2840, "en"),
        DataForSeoQuery("volume", "a%25", 2840, "en"),
        DataForSeoQuery("serp", "widgets", True, "en"),
        DataForSeoQuery("volume", "widgets", 2840, "../../x"),
        DataForSeoQuery("backlinks", "https://example.com"),
        DataForSeoQuery("backlinks", "example.com", 2840, "en"),
    ],
)
def test_closed_request_shapes(query):
    with pytest.raises(DataForSeoUnavailable):
        query.body()


def outbound():
    query = DataForSeoQuery("serp", "widgets", 2840, "en")
    return SharedEgressRequest(
        "connector",
        EgressHttpRequest(
            "POST",
            query.endpoint,
            headers=profile_headers(EgressProfile.DATAFORSEO, "POST", AUTH),
            body=query.body(),
            accepted_media_types=("application/json",),
            timeout_seconds=30,
            max_response_bytes=131072,
        ),
        EgressProfile.DATAFORSEO,
        dataforseo_scope=DataForSeoCredentialScope(str(uuid4()), AUTH),
    )


def test_profile_binds_basic_auth_and_redacts_identity():
    request = outbound()
    assert request.credentialed
    assert LOGIN not in repr(request) and AUTH not in repr(request)
    assert (
        request.request_sha256
        != replace(
            request, dataforseo_scope=DataForSeoCredentialScope(str(uuid4()), AUTH)
        ).request_sha256
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "https://evil.invalid/v3/serp/google/organic/live/advanced"},
        {"url": "https://api.dataforseo.com/v3/appendix/user_data"},
        {"url": "https://api.dataforseo.com/v3/serp/google/organic/live/advanced?x=1"},
        {"method": "GET"},
        {"body": b"{}"},
        {"body": b"x" * 4097},
        {"timeout_seconds": 31},
        {"max_response_bytes": 131073},
        {
            "headers": (
                ("accept", "application/json"),
                ("authorization", "Bearer synthetic-wrong"),
                ("content-type", "application/json"),
            )
        },
    ],
)
def test_profile_negative(changes):
    request = outbound()
    with pytest.raises(ValueError):
        replace(request, http=replace(request.http, **changes))


def test_profile_cannot_be_borrowed_or_credential_unbound():
    request = outbound()
    with pytest.raises(ValueError):
        replace(request, dataforseo_scope=None)
    with pytest.raises(ValueError):
        replace(request, profile=EgressProfile.MODEL_JSON, purpose="model")


def test_openbao_only_secret_generation_storage_removal_and_no_error_echo():
    tenant, site, generation = uuid4(), uuid4(), uuid4()
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "bao.example.invalid"
        if request.method == "POST":
            assert json.loads(request.content) == {
                "options": {"cas": 0},
                "data": {"login": LOGIN, "password": PASSWORD},
            }
            return httpx2.Response(200, json={"data": {"version": 1}})
        if request.method == "DELETE":
            assert "/metadata/" in request.url.path
            return httpx2.Response(204)
        return httpx2.Response(
            200,
            json={
                "data": {
                    "data": {"login": LOGIN, "password": PASSWORD},
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                }
            },
        )

    credentials = OpenBaoDataForSeoCredentials(
        "https://bao.example.invalid", "synthetic-bao-token-1234"
    )
    options = {"transport": httpx2.MockTransport(handler)}
    asyncio.run(credentials.put(tenant, site, generation, LOGIN, PASSWORD, **options))
    scope = asyncio.run(credentials.scope(tenant, site, generation, **options))
    assert scope.authorization == AUTH and PASSWORD not in repr(scope)
    asyncio.run(credentials.remove(tenant, site, generation, **options))
    assert (
        str(tenant) in calls[0].url.path
        and str(site) in calls[0].url.path
        and str(generation) in calls[0].url.path
    )
    with pytest.raises(DataForSeoUnavailable) as error:
        asyncio.run(
            credentials.scope(
                tenant,
                site,
                generation,
                transport=httpx2.MockTransport(lambda _: httpx2.Response(403, text=PASSWORD)),
            )
        )
    assert PASSWORD not in str(error.value)
