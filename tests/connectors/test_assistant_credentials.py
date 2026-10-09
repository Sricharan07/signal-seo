import httpx2
import pytest
from signal_core.assistant_credentials import (
    AssistantCredentialUnavailable,
    OpenBaoAssistantCredentials,
)

BAO = OpenBaoAssistantCredentials("https://bao.example", "synthetic-bao-token-123456")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def response(key):
    return httpx2.Response(
        200,
        json={
            "data": {
                "data": {"api_key": key},
                "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
            }
        },
    )


@pytest.mark.anyio
async def test_three_independent_openbao_paths_and_availability():
    paths = []

    async def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/perplexity/default"):
            return httpx2.Response(404)
        return response("synthetic-provider-key-123456")

    transport = httpx2.MockTransport(handler)
    assert await BAO.api_key("openai", transport=transport) == "synthetic-provider-key-123456"
    assert await BAO.api_key("gemini", transport=transport) == "synthetic-provider-key-123456"
    assert await BAO.availability(transport=transport) == {
        "openai": "configured_internal_only",
        "perplexity": "unavailable",
        "gemini": "configured_internal_only",
    }
    assert all(path.startswith("/v1/signal-assistants/data/") for path in paths)
    assert "synthetic-provider-key" not in repr(BAO)


@pytest.mark.anyio
async def test_deleted_invalid_or_unconfigured_secret_fails_closed():
    async def deleted(_request):
        return httpx2.Response(
            200,
            json={
                "data": {
                    "data": {"api_key": "synthetic-provider-key-123456"},
                    "metadata": {"version": 1, "destroyed": True, "deletion_time": ""},
                }
            },
        )

    with pytest.raises(AssistantCredentialUnavailable):
        await BAO.api_key("openai", transport=httpx2.MockTransport(deleted))

    async def invalid(_request):
        return response("short")

    with pytest.raises(AssistantCredentialUnavailable):
        await BAO.api_key("gemini", transport=httpx2.MockTransport(invalid))
    with pytest.raises(ValueError, match="Unsupported"):
        await BAO.api_key("untrusted")
