from uuid import uuid4

import httpx2
import pytest
from signal_core.gsc_secrets import GscSecretError, OpenBaoGscSecrets

BAO = OpenBaoGscSecrets("https://bao.example", "synthetic-bao-token-123456")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def response(data, version=1):
    return httpx2.Response(
        200,
        json={
            "data": {
                "data": data,
                "metadata": {
                    "version": version,
                    "destroyed": False,
                    "deletion_time": "",
                },
            }
        },
    )


@pytest.mark.anyio
async def test_reads_exact_client_without_exposing_secret():
    credential = "synthetic-client-secret-123456"

    async def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/signal-gsc/data/oauth-client"
        return response(
            {
                "client_id": "synthetic-client-123.apps.googleusercontent.com",
                "client_secret": credential,
            }
        )

    client = await BAO.client_credentials(transport=httpx2.MockTransport(handler))
    assert client.client_id.endswith(".apps.googleusercontent.com")
    assert credential not in repr(client)


@pytest.mark.anyio
async def test_refresh_secret_is_cas_zero_and_destroyed_without_replay():
    identifier = uuid4()
    calls = []

    async def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert request.url.path == f"/v1/signal-gsc/data/refresh/{identifier}"
            assert request.headers["x-vault-token"] == BAO.token
            return httpx2.Response(200, json={"data": {"version": 1}})
        if request.method == "GET":
            return response({"refresh_token": "synthetic-refresh-token-123456"})
        return httpx2.Response(204)

    transport = httpx2.MockTransport(handler)
    reference = await BAO.store_refresh_token(
        identifier, "synthetic-refresh-token-123456", transport=transport
    )
    assert reference == f"secret://gsc/{identifier}"
    assert await BAO.refresh_token(reference, transport=transport) == (
        "synthetic-refresh-token-123456"
    )
    await BAO.destroy_refresh_token(reference, transport=transport)
    assert calls[-1] == ("DELETE", f"/v1/signal-gsc/metadata/refresh/{identifier}")


@pytest.mark.anyio
async def test_refresh_rotation_requires_matching_previous_token_and_cas():
    identifier = uuid4()
    reference = f"secret://gsc/{identifier}"
    writes = []

    async def handler(request):
        if request.method == "GET":
            return response({"refresh_token": "synthetic-old-refresh-token-12345"}, 3)
        writes.append(request.content)
        return httpx2.Response(200, json={"data": {"version": 4}})

    transport = httpx2.MockTransport(handler)
    with pytest.raises(GscSecretError, match="GSC_REFRESH_TOKEN_CONFLICT"):
        await BAO.replace_refresh_token(
            reference,
            "synthetic-wrong-refresh-token-123",
            "synthetic-new-refresh-token-12345",
            transport=transport,
        )
    assert writes == []
    await BAO.replace_refresh_token(
        reference,
        "synthetic-old-refresh-token-12345",
        "synthetic-new-refresh-token-12345",
        transport=transport,
    )
    assert b'"cas":3' in writes[0]


@pytest.mark.anyio
async def test_missing_or_wrong_version_fails_without_secret_text():
    secret = "synthetic-refresh-token-123456"
    with pytest.raises(GscSecretError, match="GSC_REFRESH_TOKEN_UNAVAILABLE") as raised:
        await BAO.refresh_token(
            f"secret://gsc/{uuid4()}",
            transport=httpx2.MockTransport(lambda _: response({"other": secret}, 2)),
        )
    assert secret not in str(raised.value)
    with pytest.raises(GscSecretError, match="GSC_SECRET_WRITE_FAILED"):
        await BAO.store_refresh_token(
            uuid4(),
            secret,
            transport=httpx2.MockTransport(lambda _: httpx2.Response(403, json={})),
        )


@pytest.mark.anyio
async def test_tls_downgrade_and_invalid_reference_fail_closed():
    with pytest.raises(GscSecretError, match="GSC_SECRET_UNAVAILABLE"):
        await BAO.client_credentials(verify=False)
    with pytest.raises(GscSecretError, match="GSC_REFRESH_TOKEN_UNAVAILABLE"):
        await BAO.refresh_token("secret://gsc/other")
