import httpx2
import pytest
from joserfc.jwk import RSAKey
from signal_core.github_read_binding import (
    GitHubBindingUnavailable,
    OpenBaoGitHubAppCredential,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _client():
    return OpenBaoGitHubAppCredential(base_url="https://bao.example.invalid", token="a" * 32)


def _response(data, *, status=200, metadata=None):
    return httpx2.Response(
        status,
        headers={"content-type": "application/json"},
        json={
            "data": {
                "data": data,
                "metadata": metadata or {"version": 1, "destroyed": False, "deletion_time": ""},
            }
        },
    )


@pytest.mark.anyio
async def test_reads_only_fixed_openbao_path_and_hides_private_key():
    pem = (
        RSAKey.generate_key(parameters={"alg": "RS256", "use": "sig"})
        .as_pem(private=True)
        .decode("ascii")
    )

    async def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/signal-github/data/github/app"
        assert request.headers["x-vault-token"] == "a" * 32
        return _response({"app_id": 123, "private_key_pem": pem})

    client = _client()
    credentials = await client.credentials(transport=httpx2.MockTransport(handler))
    assert credentials.app_id == 123
    assert credentials.private_key_pem == pem
    assert pem not in repr(client)
    assert pem not in repr(credentials)
    assert "a" * 32 not in repr(client)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(
            403, headers={"content-type": "application/json"}, json={"errors": ["secret"]}
        ),
        _response({"app_id": 123}),
        _response({"app_id": True, "private_key_pem": "secret"}),
        _response(
            {"app_id": 123, "private_key_pem": "secret"}, metadata={"version": 1, "destroyed": True}
        ),
        httpx2.Response(200, headers={"content-type": "text/html"}, text="secret"),
    ],
)
async def test_missing_revoked_or_malformed_secret_fails_closed(response):
    async def handler(request):
        return response

    with pytest.raises(GitHubBindingUnavailable) as error:
        await _client().credentials(transport=httpx2.MockTransport(handler))
    assert error.value.code == "GITHUB_CREDENTIAL_UNAVAILABLE"
    assert "secret" not in repr(error.value)


@pytest.mark.anyio
async def test_tls_bypass_and_transport_failure_fail_closed():
    with pytest.raises(GitHubBindingUnavailable):
        await _client().credentials(verify=False)

    async def handler(request):
        raise httpx2.ConnectError("secret", request=request)

    with pytest.raises(GitHubBindingUnavailable) as error:
        await _client().credentials(transport=httpx2.MockTransport(handler))
    assert error.value.code == "GITHUB_CREDENTIAL_UNAVAILABLE"
    assert "secret" not in repr(error.value)
