import json

import httpx2
import pytest
from signal_core.model_credentials import (
    JevCredentialError,
    ModelCredentialError,
    OpenBaoJevCredential,
    OpenBaoModelCredential,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _credential() -> OpenBaoModelCredential:
    return OpenBaoModelCredential(
        "https://openbao.example.test",
        "reader-token-that-is-long-enough",
    )


@pytest.mark.anyio
async def test_model_credential_reads_only_the_fixed_versioned_secret() -> None:
    api_key = "sk-test-local-model-credential"

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/signal-model/data/openai/default"
        assert request.headers["x-vault-token"] == "reader-token-that-is-long-enough"
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "data": {
                    "data": {"api_key": api_key},
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                }
            },
        )

    credential = _credential()
    observed = await credential.api_key(transport=httpx2.MockTransport(handler))

    assert observed == api_key
    assert api_key not in repr(credential)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(403, headers={"content-type": "application/json"}, json={}),
        httpx2.Response(200, headers={"content-type": "text/plain"}, content=b"no"),
        httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(
                {
                    "data": {
                        "data": {"api_key": "not-a-key"},
                        "metadata": {
                            "version": 1,
                            "destroyed": False,
                            "deletion_time": "",
                        },
                    }
                }
            ).encode(),
        ),
    ],
)
async def test_model_credential_fails_closed_without_provider_details(
    response: httpx2.Response,
) -> None:
    credential = _credential()

    with pytest.raises(ModelCredentialError) as captured:
        await credential.api_key(
            transport=httpx2.MockTransport(lambda request: response),
        )

    assert captured.value.code == "MODEL_CREDENTIAL_UNAVAILABLE"
    assert "not-a-key" not in str(captured.value)


@pytest.mark.anyio
async def test_model_credential_rejects_disabled_tls() -> None:
    with pytest.raises(ModelCredentialError) as captured:
        await _credential().api_key(verify=False)

    assert captured.value.code == "MODEL_CREDENTIAL_TLS_REJECTED"


def _jev_credential() -> OpenBaoJevCredential:
    return OpenBaoJevCredential(
        "https://openbao.example.test",
        "reader-token-that-is-long-enough",
    )


@pytest.mark.anyio
async def test_jev_credential_reads_only_the_fixed_versioned_secret() -> None:
    api_key = "typesafe-test-key-0000000000000000"

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/signal-decision/data/typesafe/default"
        assert request.headers["x-vault-token"] == "reader-token-that-is-long-enough"
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "data": {
                    "data": {"api_key": api_key},
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                }
            },
        )

    credential = _jev_credential()
    observed = await credential.api_key(transport=httpx2.MockTransport(handler))

    assert observed == api_key
    assert api_key not in repr(credential)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(403, headers={"content-type": "application/json"}, json={}),
        httpx2.Response(200, headers={"content-type": "text/plain"}, content=b"no"),
        httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "data": {
                    "data": {"api_key": "contains whitespace"},
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                }
            },
        ),
    ],
)
async def test_jev_credential_fails_closed_without_provider_details(
    response: httpx2.Response,
) -> None:
    with pytest.raises(JevCredentialError) as captured:
        await _jev_credential().api_key(
            transport=httpx2.MockTransport(lambda request: response),
        )

    assert captured.value.code == "JEV_CREDENTIAL_UNAVAILABLE"
    assert "contains whitespace" not in str(captured.value)


@pytest.mark.anyio
async def test_jev_credential_rejects_disabled_tls() -> None:
    with pytest.raises(JevCredentialError) as captured:
        await _jev_credential().api_key(verify=False)

    assert captured.value.code == "JEV_CREDENTIAL_TLS_REJECTED"
