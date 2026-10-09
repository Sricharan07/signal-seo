import json

import httpx2
import pytest
from signal_core.gsc_properties import (
    GSC_SITES_URL,
    GscProtocolError,
    discover_gsc_properties,
)

ACCESS_TOKEN = "gsc-test-access-token-not-a-real-secret"
ORIGIN = "https://www.example.com"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def response(entries: list[dict] | None = None, **kwargs) -> httpx2.Response:
    document = {} if entries is None else {"siteEntry": entries}
    return httpx2.Response(
        200,
        headers={"content-type": "application/json"},
        json=document,
        **kwargs,
    )


@pytest.mark.anyio
async def test_discovers_exact_read_only_domain_and_url_prefix_matches():
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == GSC_SITES_URL
        assert request.method == "GET"
        assert request.headers["accept"] == "application/json"
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        return response(
            [
                {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"},
                {"siteUrl": "https://www.example.com/", "permissionLevel": "siteFullUser"},
                {
                    "siteUrl": "https://other.example/",
                    "permissionLevel": "siteRestrictedUser",
                },
                {
                    "siteUrl": "https://www.example.com/docs/",
                    "permissionLevel": "siteUnverifiedUser",
                },
            ]
        )

    properties = await discover_gsc_properties(
        access_token=ACCESS_TOKEN,
        verified_origin=ORIGIN,
        transport=httpx2.MockTransport(handler),
    )

    assert [(item.property_type, item.eligible) for item in properties] == [
        ("domain", True),
        ("url_prefix", True),
        ("url_prefix", False),
        ("url_prefix", False),
    ]
    assert properties[3].readable is False
    assert ACCESS_TOKEN not in repr(properties)


@pytest.mark.anyio
async def test_domain_property_matches_only_the_verified_origin_dns_suffix():
    properties = await discover_gsc_properties(
        access_token=ACCESS_TOKEN,
        verified_origin="https://shop.eu.example.com",
        transport=httpx2.MockTransport(
            lambda _: response(
                [
                    {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"},
                    {"siteUrl": "sc-domain:ample.com", "permissionLevel": "siteOwner"},
                ]
            )
        ),
    )
    assert [item.matches_verified_origin for item in properties] == [True, False]


@pytest.mark.anyio
async def test_empty_provider_result_remains_empty_not_synthetic():
    properties = await discover_gsc_properties(
        access_token=ACCESS_TOKEN,
        verified_origin=ORIGIN,
        transport=httpx2.MockTransport(lambda _: response()),
    )
    assert properties == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "entry",
    [
        {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteUnknown"},
        {"siteUrl": "sc-domain:Example.com", "permissionLevel": "siteOwner"},
        {"siteUrl": "sc-domain:example", "permissionLevel": "siteOwner"},
        {"siteUrl": "ftp://example.com/", "permissionLevel": "siteOwner"},
        {"siteUrl": "https://user@example.com/", "permissionLevel": "siteOwner"},
        {"siteUrl": "https://example.com/?x=1", "permissionLevel": "siteOwner"},
        {"siteUrl": "https://example.com/", "permissionLevel": "siteOwner", "extra": True},
    ],
)
async def test_rejects_malformed_or_ambiguous_property_entries(entry: dict):
    with pytest.raises(GscProtocolError, match="GSC_PROVIDER_RESPONSE_REJECTED"):
        await discover_gsc_properties(
            access_token=ACCESS_TOKEN,
            verified_origin=ORIGIN,
            transport=httpx2.MockTransport(lambda _: response([entry])),
        )


@pytest.mark.anyio
async def test_rejects_duplicate_and_unbounded_property_lists():
    duplicate = {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"}
    for entries in ([duplicate, duplicate], [duplicate] * 1001):
        with pytest.raises(GscProtocolError, match="GSC_PROVIDER_RESPONSE_REJECTED"):
            await discover_gsc_properties(
                access_token=ACCESS_TOKEN,
                verified_origin=ORIGIN,
                transport=httpx2.MockTransport(lambda _, entries=entries: response(entries)),
            )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_response", "code"),
    [
        (
            httpx2.Response(302, headers={"location": "https://attacker.example"}),
            "GSC_PROVIDER_RESPONSE_REJECTED",
        ),
        (
            httpx2.Response(401, json={"error": "token value is not returned"}),
            "GSC_AUTHORIZATION_REJECTED",
        ),
        (httpx2.Response(403, json={"error": "scope"}), "GSC_AUTHORIZATION_REJECTED"),
        (httpx2.Response(429, json={"error": "quota"}), "GSC_PROVIDER_UNAVAILABLE"),
        (httpx2.Response(503, json={"error": "down"}), "GSC_PROVIDER_UNAVAILABLE"),
        (
            httpx2.Response(200, headers={"content-type": "text/html"}, text="no"),
            "GSC_PROVIDER_RESPONSE_REJECTED",
        ),
        (
            httpx2.Response(200, headers={"content-type": "application/json"}, content=b"[]"),
            "GSC_PROVIDER_RESPONSE_REJECTED",
        ),
    ],
)
async def test_provider_failures_map_to_fixed_nonsecret_codes(
    provider_response: httpx2.Response, code: str
):
    with pytest.raises(GscProtocolError, match=code) as raised:
        await discover_gsc_properties(
            access_token=ACCESS_TOKEN,
            verified_origin=ORIGIN,
            transport=httpx2.MockTransport(lambda _: provider_response),
        )
    assert ACCESS_TOKEN not in str(raised.value)
    assert "token value" not in str(raised.value)


@pytest.mark.anyio
async def test_transport_failure_maps_to_provider_unavailable_without_token_leak():
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("provider connection failed", request=request)

    with pytest.raises(GscProtocolError, match="GSC_PROVIDER_UNAVAILABLE") as raised:
        await discover_gsc_properties(
            access_token=ACCESS_TOKEN,
            verified_origin=ORIGIN,
            transport=httpx2.MockTransport(handler),
        )

    assert ACCESS_TOKEN not in str(raised.value)


@pytest.mark.anyio
async def test_rejects_oversized_declared_and_streamed_documents():
    responses = [
        httpx2.Response(
            200,
            headers={"content-type": "application/json", "content-length": "131073"},
            content=b"{}",
        ),
        httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps({"padding": "x" * (128 * 1024)}).encode(),
        ),
    ]
    for provider_response in responses:
        with pytest.raises(GscProtocolError, match="GSC_PROVIDER_RESPONSE_REJECTED"):
            await discover_gsc_properties(
                access_token=ACCESS_TOKEN,
                verified_origin=ORIGIN,
                transport=httpx2.MockTransport(lambda _, value=provider_response: value),
            )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("token", "origin", "code"),
    [
        ("short", ORIGIN, "GSC_ACCESS_TOKEN_REJECTED"),
        ("x" * 16 + "\n", ORIGIN, "GSC_ACCESS_TOKEN_REJECTED"),
        (ACCESS_TOKEN, "http://www.example.com", "GSC_SITE_ORIGIN_REJECTED"),
        (ACCESS_TOKEN, "https://127.0.0.1", "GSC_SITE_ORIGIN_REJECTED"),
    ],
)
async def test_rejects_invalid_authority_before_network_access(token, origin, code):
    with pytest.raises(GscProtocolError, match=code):
        await discover_gsc_properties(
            access_token=token,
            verified_origin=origin,
            transport=httpx2.MockTransport(lambda _: pytest.fail("No request is allowed")),
        )


@pytest.mark.anyio
async def test_tls_verification_cannot_be_disabled():
    with pytest.raises(GscProtocolError, match="GSC_TLS_CONFIGURATION_REJECTED"):
        await discover_gsc_properties(
            access_token=ACCESS_TOKEN,
            verified_origin=ORIGIN,
            verify=False,
            transport=httpx2.MockTransport(lambda _: pytest.fail("No request is allowed")),
        )
