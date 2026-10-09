import base64

import httpx2
import pytest
from signal_core.artifact_keys import ArtifactKeyUnavailable, OpenBaoBrandArtifactKey


@pytest.fixture
def anyio_backend():
    return "asyncio"


def reader():
    return OpenBaoBrandArtifactKey(
        "https://openbao.example.test", "reader-token-that-is-long-enough"
    )


@pytest.mark.anyio
async def test_reads_only_fixed_versioned_brand_key():
    material = bytes(range(32))

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/signal-artifacts/data/brand/default"
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "data": {
                    "data": {"key_base64": base64.b64encode(material).decode()},
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                }
            },
        )

    result = await reader().read(transport=httpx2.MockTransport(handler))
    assert result.material == material
    assert result.reference == "openbao:signal-artifacts:brand:v1"
    assert material.hex() not in repr(result)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"data": {"key_base64": "bad"}, "metadata": {"version": 1}}},
        {
            "data": {
                "data": {"key_base64": base64.b64encode(bytes(32)).decode()},
                "metadata": {"version": 2, "destroyed": False, "deletion_time": ""},
            }
        },
    ],
)
async def test_missing_or_invalid_key_fails_closed(payload):
    transport = httpx2.MockTransport(
        lambda request: httpx2.Response(
            200, headers={"content-type": "application/json"}, json=payload
        )
    )
    with pytest.raises(ArtifactKeyUnavailable):
        await reader().read(transport=transport)
