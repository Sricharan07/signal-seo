import json
import secrets
from uuid import UUID, uuid4

import httpx2
import pytest
from signal_core.pkce_secrets import (
    OpenBaoPkceClient,
    PkceSecretConflict,
    PkceSecretError,
    PkceSecretUnavailable,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def client(**overrides) -> OpenBaoPkceClient:
    values = {
        "base_url": "https://openbao.example.test",
        "token": "hvs." + "x" * 43,
        "mount": "signal-ephemeral",
        **overrides,
    }
    return OpenBaoPkceClient(**values)


def verifier() -> str:
    return secrets.token_urlsafe(64)


def reference(identifier: UUID) -> str:
    return f"secret://oidc-login/{identifier}/1"


def secret_response(value: str) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "data": {
                "data": {"code_verifier": value},
                "metadata": {
                    "version": 1,
                    "destroyed": False,
                    "deletion_time": "",
                },
            }
        },
    )


@pytest.mark.anyio
async def test_store_uses_exact_cas_zero_path_and_returns_non_secret_reference():
    identifier = uuid4()
    value = verifier()
    observed = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        observed.append(request)
        return httpx2.Response(200, json={"data": {"version": 1}})

    configuration = client()
    result = await configuration.store_verifier(
        attempt_id=identifier,
        code_verifier=value,
        transport=httpx2.MockTransport(handler),
    )

    assert result == reference(identifier)
    assert value not in result
    assert configuration.token not in repr(configuration)
    assert len(observed) == 1
    request = observed[0]
    assert request.method == "POST"
    assert request.url == httpx2.URL(
        f"https://openbao.example.test/v1/signal-ephemeral/data/oidc-login/{identifier}"
    )
    assert request.headers["x-vault-token"] == configuration.token
    assert json.loads(request.content) == {
        "options": {"cas": 0},
        "data": {"code_verifier": value},
    }


@pytest.mark.anyio
async def test_store_rejects_collision_without_provider_text():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"errors": ["sensitive provider detail"]})

    with pytest.raises(PkceSecretConflict) as failure:
        await client().store_verifier(
            attempt_id=uuid4(),
            code_verifier=verifier(),
            transport=httpx2.MockTransport(handler),
        )
    assert failure.value.code == "PKCE_SECRET_CONFLICT"
    assert "sensitive" not in str(failure.value)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [201, 204, 307, 403, 500])
async def test_store_accepts_only_valid_kv_v2_version_one_response(status):
    transport = httpx2.MockTransport(lambda request: httpx2.Response(status, json={}))
    with pytest.raises(PkceSecretError) as failure:
        await client().store_verifier(
            attempt_id=uuid4(), code_verifier=verifier(), transport=transport
        )
    assert failure.value.code == "PKCE_SECRET_WRITE_FAILED"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, text="not-json"),
        httpx2.Response(200, json=[]),
        httpx2.Response(200, json={"data": {"version": 2}}),
        httpx2.Response(200, json={"data": {"version": True}}),
    ],
)
async def test_store_rejects_malformed_success_documents(response):
    transport = httpx2.MockTransport(lambda request: response)
    with pytest.raises(PkceSecretError, match="PKCE_SECRET_WRITE_FAILED"):
        await client().store_verifier(
            attempt_id=uuid4(), code_verifier=verifier(), transport=transport
        )


@pytest.mark.anyio
async def test_consume_reads_exact_version_and_deletes_all_metadata_before_returning():
    identifier = uuid4()
    value = verifier()
    observed = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        observed.append((request.method, str(request.url)))
        if request.method == "GET":
            return secret_response(value)
        return httpx2.Response(204)

    result = await client().consume_verifier(
        secret_reference=reference(identifier),
        transport=httpx2.MockTransport(handler),
    )

    assert result == value
    assert observed == [
        (
            "GET",
            "https://openbao.example.test/v1/"
            f"signal-ephemeral/data/oidc-login/{identifier}?version=1",
        ),
        (
            "DELETE",
            f"https://openbao.example.test/v1/signal-ephemeral/metadata/oidc-login/{identifier}",
        ),
    ]


@pytest.mark.anyio
async def test_consume_retries_idempotent_delete_after_server_failure():
    value = verifier()
    delete_calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal delete_calls
        if request.method == "GET":
            return secret_response(value)
        delete_calls += 1
        return httpx2.Response(500 if delete_calls == 1 else 204)

    result = await client().consume_verifier(
        secret_reference=reference(uuid4()),
        transport=httpx2.MockTransport(handler),
    )
    assert result == value
    assert delete_calls == 2


@pytest.mark.anyio
async def test_consume_never_releases_verifier_without_confirmed_permanent_delete():
    value = verifier()

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return secret_response(value)
        return httpx2.Response(500, json={"errors": [value]})

    with pytest.raises(PkceSecretError) as failure:
        await client().consume_verifier(
            secret_reference=reference(uuid4()),
            transport=httpx2.MockTransport(handler),
        )
    assert failure.value.code == "PKCE_SECRET_DELETE_UNCONFIRMED"
    assert value not in str(failure.value)


@pytest.mark.anyio
async def test_missing_secret_is_indistinguishable_and_does_not_delete():
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.method)
        return httpx2.Response(404, json={"errors": ["not found"]})

    with pytest.raises(PkceSecretUnavailable) as failure:
        await client().consume_verifier(
            secret_reference=reference(uuid4()),
            transport=httpx2.MockTransport(handler),
        )
    assert failure.value.code == "PKCE_SECRET_UNAVAILABLE"
    assert calls == ["GET"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "document",
    [
        {},
        {"data": []},
        {"data": {"data": {}, "metadata": {"version": 1, "destroyed": False}}},
        {
            "data": {
                "data": {"code_verifier": "short"},
                "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
            }
        },
        {
            "data": {
                "data": {"code_verifier": verifier(), "extra": "no"},
                "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
            }
        },
        {
            "data": {
                "data": {"code_verifier": verifier()},
                "metadata": {"version": 1, "destroyed": True, "deletion_time": ""},
            }
        },
    ],
)
async def test_consume_rejects_malformed_or_deleted_secret_documents(document):
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.method)
        return httpx2.Response(200, json=document)

    with pytest.raises(PkceSecretError, match="PKCE_SECRET_READ_FAILED"):
        await client().consume_verifier(
            secret_reference=reference(uuid4()),
            transport=httpx2.MockTransport(handler),
        )
    assert calls == ["GET"]


@pytest.mark.anyio
async def test_oversized_and_invalid_length_responses_fail_closed():
    responses = [
        httpx2.Response(200, content=b"x" * (16 * 1024 + 1)),
        httpx2.Response(200, headers={"Content-Length": "invalid"}, content=b"{}"),
    ]
    for response in responses:
        transport = httpx2.MockTransport(lambda request, response=response: response)
        with pytest.raises(PkceSecretError, match="PKCE_SECRET_WRITE_FAILED"):
            await client().store_verifier(
                attempt_id=uuid4(), code_verifier=verifier(), transport=transport
            )


@pytest.mark.anyio
async def test_network_errors_are_redacted():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("secret network detail", request=request)

    with pytest.raises(PkceSecretError) as failure:
        await client().store_verifier(
            attempt_id=uuid4(),
            code_verifier=verifier(),
            transport=httpx2.MockTransport(handler),
        )
    assert failure.value.code == "PKCE_SECRET_WRITE_FAILED"
    assert "network" not in str(failure.value)


@pytest.mark.anyio
async def test_tls_verification_cannot_be_disabled():
    transport = httpx2.MockTransport(lambda request: pytest.fail("No request is allowed"))
    with pytest.raises(PkceSecretError, match="OPENBAO_TLS_CONFIGURATION_REJECTED"):
        await client().store_verifier(
            attempt_id=uuid4(),
            code_verifier=verifier(),
            transport=transport,
            verify=False,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_url", "http://openbao.example.test"),
        ("base_url", "https://user@openbao.example.test"),
        ("base_url", "https://openbao.example.test/path"),
        ("base_url", "https://openbao.example.test?query=1"),
        ("token", "short"),
        ("token", "x" * 15 + " "),
        ("mount", "../secret"),
        ("mount", "UPPERCASE"),
    ],
)
def test_client_configuration_is_strict(field, value):
    with pytest.raises(ValueError):
        client(**{field: value})


@pytest.mark.anyio
@pytest.mark.parametrize("attempt_id", [None, "not-a-uuid", UUID(int=0), uuid4().hex])
async def test_invalid_attempt_id_fails_before_network(attempt_id):
    transport = httpx2.MockTransport(lambda request: pytest.fail("No request is allowed"))
    with pytest.raises(ValueError):
        await client().store_verifier(
            attempt_id=attempt_id,
            code_verifier=verifier(),
            transport=transport,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "value",
    [None, "", "x" * 42, "x" * 129, "x" * 42 + "+", "contains space" * 4],
)
async def test_invalid_verifier_fails_before_network(value):
    transport = httpx2.MockTransport(lambda request: pytest.fail("No request is allowed"))
    with pytest.raises(ValueError):
        await client().store_verifier(
            attempt_id=uuid4(),
            code_verifier=value,
            transport=transport,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "secret://oidc-login/not-a-uuid/1",
        f"secret://oidc-login/{uuid4()}/2",
        f"secret://other/{uuid4()}/1",
        f"secret://oidc-login/{str(uuid4()).upper()}/1",
    ],
)
async def test_invalid_reference_fails_before_network(value):
    transport = httpx2.MockTransport(lambda request: pytest.fail("No request is allowed"))
    with pytest.raises(PkceSecretUnavailable):
        await client().consume_verifier(secret_reference=value, transport=transport)
