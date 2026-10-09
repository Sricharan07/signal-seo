from uuid import uuid4

import httpx2
import pytest
from signal_core.bing_secrets import BingSecretError, OpenBaoBingSecrets

BAO = OpenBaoBingSecrets("https://bao.example", "synthetic-bao-token-123456")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def response(data, version=1):
    return httpx2.Response(
        200,
        json={
            "data": {
                "data": data,
                "metadata": {"version": version, "destroyed": False, "deletion_time": ""},
            }
        },
    )


@pytest.mark.anyio
async def test_client_reads_only_exact_openbao_document():
    async def handler(request):
        assert request.url.path == "/v1/signal-bing/data/oauth-client"
        return response(
            {"client_id": "synthetic-client-123456", "client_secret": "synthetic-secret-123456"}
        )

    credentials = await BAO.client_credentials(transport=httpx2.MockTransport(handler))
    assert credentials.client_id == "synthetic-client-123456"
    assert credentials.client_secret not in repr(credentials)

    async def invalid(_request):
        return response(
            {
                "client_id": "synthetic-client-123456",
                "client_secret": "synthetic-secret-123456",
                "extra": "bad",
            }
        )

    with pytest.raises(BingSecretError, match="BING_CLIENT_UNAVAILABLE"):
        await BAO.client_credentials(transport=httpx2.MockTransport(invalid))


@pytest.mark.anyio
async def test_refresh_cas_rotation_and_destroy():
    identifier = uuid4()
    calls = []

    async def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert b'"cas":0' in request.content or b'"cas":3' in request.content
            return httpx2.Response(200, json={"data": {"version": 1 if len(calls) == 1 else 4}})
        if request.method == "GET":
            return response({"refresh_token": "synthetic-refresh-token-123456"}, 3)
        return httpx2.Response(204)

    transport = httpx2.MockTransport(handler)
    reference = await BAO.store_refresh_token(
        identifier, "synthetic-refresh-token-123456", transport=transport
    )
    assert reference == f"secret://bing/{identifier}"
    assert (
        await BAO.refresh_token(reference, transport=transport) == "synthetic-refresh-token-123456"
    )
    with pytest.raises(BingSecretError, match="BING_REFRESH_CONFLICT"):
        await BAO.replace_refresh_token(
            reference,
            "synthetic-wrong-refresh-token-123456",
            "synthetic-rotated-refresh-token-123456",
            transport=transport,
        )
    await BAO.replace_refresh_token(
        reference,
        "synthetic-refresh-token-123456",
        "synthetic-rotated-refresh-token-123456",
        transport=transport,
    )
    await BAO.destroy_refresh_token(reference, transport=transport)
    assert calls[-1] == ("DELETE", f"/v1/signal-bing/metadata/refresh/{identifier}")


@pytest.mark.anyio
async def test_openbao_failure_is_not_silent():
    async def denied(_request):
        return httpx2.Response(403)

    with pytest.raises(BingSecretError, match="BING_SECRET_UNAVAILABLE"):
        await BAO.client_credentials(transport=httpx2.MockTransport(denied))
    with pytest.raises(BingSecretError, match="BING_REFRESH_UNAVAILABLE"):
        await BAO.refresh_token("secret://gsc/" + str(uuid4()))
