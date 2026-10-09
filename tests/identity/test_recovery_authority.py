import json

import httpx2
import pytest
from signal_core.recovery_authority import (
    OpenBaoRecoveryAuthority,
    RecoveryAuthorityError,
    RecoveryGeneration,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def authority(**overrides) -> OpenBaoRecoveryAuthority:
    values = {
        "base_url": "https://openbao.example.test",
        "token": "hvs." + "r" * 43,
        "mount": "signal-authority",
        **overrides,
    }
    return OpenBaoRecoveryAuthority(**values)


def response(
    *,
    generation: object = "recovery-generation-7",
    version: object = 7,
    destroyed: object = False,
    deletion_time: object = "",
) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "data": {
                "data": {"generation": generation},
                "metadata": {
                    "version": version,
                    "destroyed": destroyed,
                    "deletion_time": deletion_time,
                },
            }
        },
    )


@pytest.mark.anyio
async def test_reads_exact_current_path_with_read_only_credential():
    observed = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        observed.append(request)
        return response()

    configured = authority()
    result = await configured.current_generation(transport=httpx2.MockTransport(handle))

    assert result == RecoveryGeneration("recovery-generation-7", 7)
    assert configured.token not in repr(configured)
    assert len(observed) == 1
    request = observed[0]
    assert request.method == "GET"
    assert request.url == "https://openbao.example.test/v1/signal-authority/data/recovery/current"
    assert request.headers["x-vault-token"] == configured.token
    assert request.headers["accept"] == "application/json"


@pytest.mark.anyio
@pytest.mark.parametrize("status", [201, 204, 302, 403, 404, 500])
async def test_accepts_only_a_successful_authority_read(status):
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(status, json={"errors": ["private provider text"]})
    )
    with pytest.raises(RecoveryAuthorityError) as failure:
        await authority().current_generation(transport=transport)
    assert failure.value.code == "RECOVERY_AUTHORITY_UNAVAILABLE"
    assert "private" not in str(failure.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider_response",
    [
        httpx2.Response(200, text="not-json"),
        httpx2.Response(200, json=[]),
        httpx2.Response(200, json={}),
        httpx2.Response(200, json={"data": []}),
        httpx2.Response(200, json={"data": {"data": {}, "metadata": {}}}),
        response(generation="bad generation"),
        response(version=0),
        response(version=True),
        response(destroyed=True),
        response(deletion_time="2026-09-08T00:00:00Z"),
    ],
)
async def test_rejects_malformed_deleted_or_ambiguous_authority_data(provider_response):
    with pytest.raises(RecoveryAuthorityError, match="RECOVERY_AUTHORITY_UNAVAILABLE"):
        await authority().current_generation(
            transport=httpx2.MockTransport(lambda _: provider_response)
        )


@pytest.mark.anyio
async def test_rejects_extra_generation_fields():
    document = response().json()
    document["data"]["data"]["unexpected"] = "value"
    with pytest.raises(RecoveryAuthorityError, match="RECOVERY_AUTHORITY_UNAVAILABLE"):
        await authority().current_generation(
            transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=document))
        )


@pytest.mark.anyio
async def test_response_size_and_content_length_are_bounded():
    responses = [
        httpx2.Response(200, content=b"x" * (16 * 1024 + 1)),
        httpx2.Response(200, headers={"Content-Length": "invalid"}, content=b"{}"),
    ]
    for provider_response in responses:
        with pytest.raises(RecoveryAuthorityError, match="RECOVERY_AUTHORITY_UNAVAILABLE"):
            await authority().current_generation(
                transport=httpx2.MockTransport(lambda _, item=provider_response: item)
            )


@pytest.mark.anyio
async def test_network_errors_are_redacted():
    def handle(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("private network detail", request=request)

    with pytest.raises(RecoveryAuthorityError) as failure:
        await authority().current_generation(transport=httpx2.MockTransport(handle))
    assert failure.value.code == "RECOVERY_AUTHORITY_UNAVAILABLE"
    assert "network" not in str(failure.value)


@pytest.mark.anyio
async def test_tls_verification_cannot_be_disabled():
    no_network = httpx2.MockTransport(lambda _: pytest.fail("No request is allowed"))
    with pytest.raises(RecoveryAuthorityError, match="RECOVERY_AUTHORITY_TLS_REJECTED"):
        await authority().current_generation(transport=no_network, verify=False)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_url", "http://openbao.example.test"),
        ("base_url", "https://user@openbao.example.test"),
        ("base_url", "https://openbao.example.test/path"),
        ("token", "short"),
        ("mount", "../authority"),
        ("mount", "UPPERCASE"),
    ],
)
def test_configuration_is_strict(field, value):
    with pytest.raises(ValueError):
        authority(**{field: value})


@pytest.mark.parametrize(
    ("generation", "version"),
    [("", 1), ("bad generation", 1), ("x" * 129, 1), ("valid", 0), ("valid", True)],
)
def test_generation_value_object_is_strict(generation, version):
    with pytest.raises(ValueError):
        RecoveryGeneration(generation, version)


def test_serialized_success_fixture_contains_no_credentials():
    encoded = json.dumps({"generation": "recovery-generation-7", "version": 7})
    assert "hvs." not in encoded
    assert "token" not in encoded
