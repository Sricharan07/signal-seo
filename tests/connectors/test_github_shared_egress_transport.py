import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import httpx2
import pytest
from joserfc.jwk import RSAKey
from signal_core.egress_profiles import EgressProfile, profile_headers
from signal_core.github_app import (
    GITHUB_API_VERSION,
    GitHubAppCredentials,
    GitHubRepositoryTarget,
    inspect_github_repository,
)
from signal_core.github_read_binding import GitHubSharedEgressTransport
from signal_core.shared_egress import (
    ProviderEgressResponse,
    ProviderEgressUnavailable,
    SharedEgressProvider,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _provider():
    provider = Mock(spec=SharedEgressProvider)
    provider.purpose = "connector"
    provider.policy = Mock(
        allowed_origins=("https://api.github.com",),
        user_agent="SignalBot/1.0 (+https://signal.example/bot)",
        max_redirects=0,
        max_body_bytes=256 * 1024,
        request_timeout_seconds=5,
    )
    return provider


@pytest.mark.anyio
async def test_exact_github_inspection_uses_shared_egress_for_all_three_calls():
    provider = _provider()
    seen = []

    def request_json(**kwargs):
        seen.append(kwargs)
        url = kwargs["url"]
        if url.endswith("/access_tokens"):
            document = {
                "token": "t" * 32,
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "permissions": {"contents": "read", "metadata": "read"},
                "repository_selection": "selected",
                "repositories": [{"id": 345, "full_name": "SignalOwner/website"}],
            }
        elif url.endswith("/branches/main"):
            document = {"name": "main", "commit": {"sha": "a" * 40}, "protected": True}
        else:
            document = {
                "id": 345,
                "full_name": "SignalOwner/website",
                "private": True,
                "default_branch": "main",
                "archived": False,
                "disabled": False,
            }
        return ProviderEgressResponse(200, "application/json", json.dumps(document).encode())

    provider.request_json.side_effect = request_json
    key = RSAKey.generate_key(parameters={"alg": "RS256", "use": "sig"})
    snapshot = await inspect_github_repository(
        credentials=GitHubAppCredentials(123, key.as_pem(private=True).decode()),
        target=GitHubRepositoryTarget(1234, "SignalOwner", "website", "main", "app/page.tsx"),
        transport=GitHubSharedEgressTransport(provider),
    )
    assert snapshot.repository_id == 345
    assert [call["method"] for call in seen] == ["POST", "GET", "GET"]
    assert all(call["url"].startswith("https://api.github.com/") for call in seen)
    assert json.loads(seen[0]["body"]) == {
        "repositories": ["website"],
        "permissions": {"contents": "read"},
    }
    assert all(call["profile"] == EgressProfile.GITHUB_REST for call in seen)
    assert all(
        ("x-github-api-version", GITHUB_API_VERSION)
        in profile_headers(call["profile"], call["method"], call["authorization"])
        for call in seen
    )
    assert all(
        "user-agent"
        not in dict(profile_headers(call["profile"], call["method"], call["authorization"]))
        for call in seen
    )
    assert all(call["max_response_bytes"] == 256 * 1024 for call in seen)


@pytest.mark.anyio
async def test_transport_rejects_wrong_origin_route_and_policy_before_egress():
    provider = _provider()
    transport = GitHubSharedEgressTransport(provider)
    headers = {
        "accept": "application/vnd.github+json",
        "authorization": "Bearer " + "t" * 32,
        "user-agent": "Signal-SEO-Agent/0.0.0",
        "x-github-api-version": GITHUB_API_VERSION,
    }
    for url in (
        "https://other.example.invalid/repos/SignalOwner/website",
        "https://api.github.com/repos/SignalOwner/website/actions/secrets",
        "https://api.github.com/repos/SignalOwner/website?extra=1",
        "https://api.github.com/repos/SignalOwner/website/git/trees/" + "a" * 40,
        "https://api.github.com/repos/SignalOwner/website/git/trees/" + "a" * 40 + "?recursive=0",
    ):
        with pytest.raises(httpx2.ConnectError):
            await transport.handle_async_request(httpx2.Request("GET", url, headers=headers))
    provider.request_json.assert_not_called()
    provider.request_json.return_value = ProviderEgressResponse(200, "application/json", b"{}")
    tree = "https://api.github.com/repos/SignalOwner/website/git/trees/" + "a" * 40
    response = await transport.handle_async_request(
        httpx2.Request("GET", tree + "?recursive=1", headers=headers)
    )
    assert response.status_code == 200
    assert provider.request_json.call_args.kwargs["url"] == tree + "?recursive=1"
    blob = "https://api.github.com/repos/SignalOwner/website/git/blobs/" + "b" * 40
    response = await transport.handle_async_request(httpx2.Request("GET", blob, headers=headers))
    assert response.status_code == 200
    assert provider.request_json.call_args.kwargs["url"] == blob
    with pytest.raises(httpx2.ConnectError):
        await transport.handle_async_request(
            httpx2.Request("GET", blob + "?recursive=1", headers=headers)
        )
    provider.policy.user_agent = "OtherAgent/1.0"
    with pytest.raises(ValueError):
        GitHubSharedEgressTransport(provider)
    provider.policy.user_agent = "SignalBot/1.0 (+https://signal.example/bot)"
    provider.policy.allowed_origins = ("https://other.example.invalid",)
    with pytest.raises(ValueError):
        GitHubSharedEgressTransport(provider)


@pytest.mark.anyio
@pytest.mark.parametrize("code", ["EGRESS_DEFERRED", "OUTCOME_UNKNOWN", "EGRESS_DENIED"])
async def test_transport_retries_only_admission_deferral_with_the_same_identity(code):
    provider = _provider()
    provider.request_json.side_effect = [
        ProviderEgressUnavailable(code, retryable=True),
        ProviderEgressResponse(200, "application/json", b"{}"),
    ]
    request = httpx2.Request(
        "GET",
        "https://api.github.com/repos/SignalOwner/website",
        headers={
            "accept": "application/vnd.github+json",
            "authorization": "Bearer " + "t" * 32,
            "user-agent": "Signal-SEO-Agent/0.0.0",
            "x-github-api-version": GITHUB_API_VERSION,
        },
    )
    if code == "EGRESS_DEFERRED":
        result = await GitHubSharedEgressTransport(provider).handle_async_request(request)
        assert result.status_code == 200
        first, second = provider.request_json.call_args_list
        assert first.kwargs == second.kwargs
        assert first.kwargs["method"] == "GET"
    else:
        with pytest.raises(httpx2.ConnectError):
            await GitHubSharedEgressTransport(provider).handle_async_request(request)
        provider.request_json.assert_called_once()
