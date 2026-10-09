import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx2
import pytest
from joserfc.jwk import RSAKey
from signal_core.egress_profiles import EgressProfile
from signal_core.github_app import GitHubAppCredentials, GitHubRepositoryTarget
from signal_core.github_delivery_provider import (
    READ_PERMISSIONS,
    GitHubDeliveryEgressTransport,
    GitHubDeliveryProvider,
    GitHubDeliveryUnavailable,
)
from signal_core.shared_egress import ProviderEgressResponse, SharedEgressProvider


@pytest.fixture
def anyio_backend():
    return "asyncio"


def provider(monkeypatch):
    target = GitHubRepositoryTarget(1234, "SignalOwner", "website", "main", "index.html")
    egress = SharedEgressProvider(
        None,
        None,
        None,
        None,
        SimpleNamespace(
            allowed_origins=("https://api.github.com",),
            user_agent="SignalBot/1.0 (+https://signal.example/bot)",
            max_redirects=0,
            max_body_bytes=256 * 1024,
            request_timeout_seconds=5,
        ),
        None,
        "delivery.double",
        None,
        None,
        "connector",
    )
    now = datetime.now(UTC) - timedelta(minutes=5)
    documents = {
        "/app/installations/1234/access_tokens": {
            "token": "t" * 32,
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "permissions": {**READ_PERMISSIONS, "metadata": "read"},
            "repository_selection": "selected",
            "repositories": [{"id": 345, "full_name": "SignalOwner/website"}],
        },
        "/repos/SignalOwner/website": {"id": 345, "full_name": "SignalOwner/website"},
        "/repos/SignalOwner/website/pulls/7": {
            "number": 7,
            "html_url": "https://github.com/SignalOwner/website/pull/7",
            "head": {"sha": "a" * 40, "repo": {"id": 345}},
            "base": {"ref": "main", "repo": {"id": 345}},
            "state": "closed",
            "merged": True,
            "merge_commit_sha": "b" * 40,
            "merged_at": now.isoformat(),
        },
        "/repos/SignalOwner/website/commits/" + "a" * 40 + "/check-runs": {
            "total_count": 1,
            "check_runs": [
                {"id": 91, "head_sha": "a" * 40, "status": "completed", "conclusion": "success"}
            ],
        },
        "/repos/SignalOwner/website/commits/" + "a" * 40 + "/status": {
            "sha": "a" * 40,
            "total_count": 0,
            "statuses": [],
        },
        "/repos/SignalOwner/website/git/commits/" + "b" * 40: {
            "sha": "b" * 40,
            "tree": {"sha": "c" * 40},
        },
        "/repos/SignalOwner/website/deployments": [
            {
                "id": 81,
                "sha": "b" * 40,
                "environment": "production",
                "production_environment": True,
                "transient_environment": False,
                "creator": {"id": 56},
                "created_at": (now + timedelta(seconds=1)).isoformat(),
            }
        ],
        "/repos/SignalOwner/website/deployments/81/statuses": [
            {
                "id": 82,
                "state": "success",
                "environment": "production",
                "environment_url": "https://site.example/",
                "deployment_url": "https://api.github.com/repos/SignalOwner/website/deployments/81",
                "creator": {"id": 56},
                "created_at": (now + timedelta(seconds=2)).isoformat(),
            }
        ],
    }
    calls, guards = [], []

    def request_json(self, **kw):
        calls.append(kw)
        value = documents[urlsplit(kw["url"]).path]
        if isinstance(value, Exception):
            raise value
        status, value = (
            value if isinstance(value, tuple) else (201 if kw["method"] == "POST" else 200, value)
        )
        return ProviderEgressResponse(status, "application/json", json.dumps(value).encode())

    monkeypatch.setattr(SharedEgressProvider, "request_json", request_json)
    transport = GitHubDeliveryEgressTransport(
        egress,
        target,
        pr_number=7,
        head_sha="a" * 40,
        environment="production",
        authority_guard=lambda: guards.append(True),
    )
    key = RSAKey.generate_key(parameters={"alg": "RS256", "use": "sig"})
    client = GitHubDeliveryProvider(
        credentials=GitHubAppCredentials(123, key.as_pem(private=True).decode()),
        target=target,
        repository_id=345,
        transport=transport,
    )
    return client, documents, calls, guards


async def observe(client):
    return await client.observe(
        expected_tree_sha="c" * 40,
        trusted_deployment_actor_id=56,
        site_origin="https://site.example",
    )


@pytest.mark.anyio
async def test_exact_merged_tree_and_latest_customer_deployment_are_correlated(monkeypatch):
    client, documents, calls, guards = provider(monkeypatch)
    result = await observe(client)
    assert result["stage"] == "deployed" and result["deployment"]["sha"] == "b" * 40
    assert result["checks"]["passed_count"] == 1
    assert len(guards) == len(calls) == 8
    assert len(client.transport.evidence) == 7
    assert json.loads(calls[0]["body"])["permissions"] == READ_PERMISSIONS
    assert all(call["method"] == "GET" for call in calls[1:])
    assert all(call["profile"] == EgressProfile.GITHUB_REST for call in calls)
    assert all("headers" not in call and "github_write_scope" not in call for call in calls)
    assert not any("authorization" in evidence for evidence in client.transport.evidence)
    documents["/repos/SignalOwner/website/pulls/7"].update(merged=False, state="open")
    result = await observe(client)
    assert result["stage"] == "checks" and result["deployment"] is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("commit", "EC_123_DEPLOYMENT_COMMIT_MISMATCH"),
        ("tree", "EC_076_MERGED_TREE_MISMATCH"),
        ("actor", "EC_123_DEPLOYMENT_IDENTITY_UNAVAILABLE"),
        ("preview", "EC_123_DEPLOYMENT_IDENTITY_UNAVAILABLE"),
        ("origin", "EC_123_DEPLOYMENT_ORIGIN_MISMATCH"),
        ("pending", "CUSTOMER_DEPLOYMENT_NOT_SUCCESSFUL"),
    ],
)
async def test_unrelated_preview_untrusted_or_pending_deployment_is_not_delivery(
    monkeypatch, mutation, reason
):
    client, documents, _, _ = provider(monkeypatch)
    deployment = documents["/repos/SignalOwner/website/deployments"][0]
    status = documents["/repos/SignalOwner/website/deployments/81/statuses"][0]
    if mutation == "commit":
        deployment["sha"] = "d" * 40
    elif mutation == "tree":
        documents["/repos/SignalOwner/website/git/commits/" + "b" * 40]["tree"]["sha"] = "d" * 40
    elif mutation == "actor":
        deployment["creator"]["id"] = 57
    elif mutation == "preview":
        deployment["transient_environment"] = True
    elif mutation == "origin":
        status["environment_url"] = "https://other.example/"
    else:
        status["state"] = "pending"
    result = await observe(client)
    assert result["stage"] == "merged" and result["reason"] == reason


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,code",
    [
        (429, "GITHUB_DELIVERY_RATE_LIMITED"),
        (503, "GITHUB_DELIVERY_UNAVAILABLE"),
        (403, "GITHUB_DELIVERY_AUTHORITY_UNAVAILABLE"),
    ],
)
async def test_provider_failures_are_explicit(monkeypatch, status, code):
    client, documents, _, _ = provider(monkeypatch)
    documents["/repos/SignalOwner/website/pulls/7"] = (status, {})
    with pytest.raises(GitHubDeliveryUnavailable) as error:
        await observe(client)
    assert error.value.code == code


@pytest.mark.anyio
async def test_lost_response_and_authority_revocation_prevent_new_reads(monkeypatch):
    client, documents, calls, _ = provider(monkeypatch)
    documents["/repos/SignalOwner/website/pulls/7"] = httpx2.ReadTimeout("lost response")
    with pytest.raises(GitHubDeliveryUnavailable):
        await observe(client)
    count = len(calls)

    def denied():
        raise GitHubDeliveryUnavailable("REVOKED")

    client.transport.authority_guard = denied
    with pytest.raises(GitHubDeliveryUnavailable, match="REVOKED"):
        await observe(client)
    assert len(calls) == count


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/repos/SignalOwner/other",
        "/repos/SignalOwner/website/merge",
        "/repos/SignalOwner/website/actions/secrets",
        "/repos/SignalOwner/website/deployments",
    ],
)
async def test_gateway_never_allows_writes_or_other_repositories(monkeypatch, path):
    client, _, calls, _ = provider(monkeypatch)
    with pytest.raises(httpx2.ConnectError):
        await client.transport.handle_async_request(
            httpx2.Request(
                "POST",
                "https://api.github.com" + path,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": "Bearer synthetic",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "Signal-SEO-Agent/0.0.0",
                },
                json={},
            )
        )
    assert calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["repository", "head", "malformed_check", "incomplete_checks"])
async def test_binding_and_malformed_provider_evidence_fail_closed(monkeypatch, mutation):
    client, documents, _, _ = provider(monkeypatch)
    if mutation == "repository":
        documents["/repos/SignalOwner/website"]["id"] = 346
    elif mutation == "head":
        documents["/repos/SignalOwner/website/pulls/7"]["head"]["sha"] = "d" * 40
    else:
        checks = documents["/repos/SignalOwner/website/commits/" + "a" * 40 + "/check-runs"]
        if mutation == "malformed_check":
            checks["check_runs"][0]["conclusion"] = {"untrusted": "success"}
        else:
            checks["total_count"] = 2
    with pytest.raises(GitHubDeliveryUnavailable):
        await observe(client)


@pytest.mark.anyio
async def test_latest_unrelated_deployment_cannot_be_hidden_by_an_old_success(monkeypatch):
    client, documents, _, _ = provider(monkeypatch)
    deployments = documents["/repos/SignalOwner/website/deployments"]
    later = {
        **deployments[0],
        "id": 83,
        "sha": "d" * 40,
        "created_at": datetime.now(UTC).isoformat(),
    }
    deployments.append(later)
    result = await observe(client)
    assert result["stage"] == "merged" and result["reason"] == "EC_123_DEPLOYMENT_COMMIT_MISMATCH"
