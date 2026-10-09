import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import pytest
from joserfc.jwk import RSAKey
from signal_core.egress_profiles import EgressProfile
from signal_core.github_app import GitHubAppCredentials, GitHubRepositoryTarget
from signal_core.github_pr_delivery import _reconcile_step
from signal_core.github_pr_patch import GitHubPrPatch
from signal_core.github_pr_provider import (
    GitHubPrProvider,
    GitHubPrProviderError,
    GitHubWriteEgressTransport,
)
from signal_core.shared_egress import (
    ProviderEgressResponse,
    ProviderEgressUnavailable,
    SharedEgressProvider,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeFetcher:
    def request(self, request, *, policy):
        return None


def _provider(monkeypatch, *, mutation_status=201, lost_step=None):
    operation_id = uuid4()
    patch = GitHubPrPatch(
        "index.html",
        b"<title>Fixed</title>\n",
        "a" * 64,
        "b" * 40,
        "c" * 40,
        "Fix title\n",
        "2026-09-29T12:00:00Z",
    )
    target = GitHubRepositoryTarget(1234, "SignalOwner", "website", "main", "index.html")
    allowed = []
    calls = []
    provider = SharedEgressProvider(
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
        FakeFetcher(),
        "github-pr-double",
        None,
        None,
        "connector",
    )

    def request_json(self, **kwargs):
        step = kwargs["url"].rsplit("/", 1)[-1]
        calls.append(kwargs)
        self.fetcher.request(None, policy=None)
        if step == lost_step:
            raise httpx2.ConnectError("lost response")
        if step == "access_tokens":
            document = {
                "token": "t" * 32,
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "permissions": {"contents": "write", "pull_requests": "write", "metadata": "read"},
                "repository_selection": "selected",
                "repositories": [{"id": 345, "full_name": "SignalOwner/website"}],
            }
            return ProviderEgressResponse(201, "application/json", json.dumps(document).encode())
        if kwargs["method"] == "GET":
            return ProviderEgressResponse(404, "application/json", b"{}")
        if step == "trees":
            document = {"sha": patch.tree_sha}
        elif step == "commits":
            document = {
                "sha": patch.commit_sha,
                "tree": {"sha": patch.tree_sha},
                "parents": [{"sha": "d" * 40}],
                "message": patch.commit_message,
            }
        elif step == "refs":
            document = {
                "ref": f"refs/heads/signal/{operation_id.hex}",
                "object": {"type": "commit", "sha": patch.commit_sha},
            }
        else:
            document = {
                "number": 7,
                "html_url": "https://github.com/SignalOwner/website/pull/7",
                "head": {"ref": f"signal/{operation_id.hex}", "sha": patch.commit_sha},
                "base": {"ref": "main", "sha": "d" * 40},
                "body": "Exact body",
                "state": "open",
            }
        return ProviderEgressResponse(
            mutation_status, "application/json", json.dumps(document).encode()
        )

    monkeypatch.setattr(SharedEgressProvider, "request_json", request_json)
    transport = GitHubWriteEgressTransport(
        provider,
        target,
        allowed.append,
        operation_id=operation_id,
        patch=patch,
        base_sha="d" * 40,
        base_tree_sha="e" * 40,
        pr_body="Exact body",
    )
    key = RSAKey.generate_key(parameters={"alg": "RS256", "use": "sig"})
    client = GitHubPrProvider(
        credentials=GitHubAppCredentials(123, key.as_pem(private=True).decode()),
        target=target,
        transport=transport,
    )
    return client, patch, operation_id, calls, allowed


@pytest.mark.anyio
async def test_exact_git_objects_branch_and_pr_use_one_bounded_repository(monkeypatch):
    client, patch, operation_id, calls, allowed = _provider(monkeypatch)
    await client.acquire_token()
    assert await client.create_tree(base_tree_sha="e" * 40, patch=patch) == patch.tree_sha
    assert await client.create_commit(base_sha="d" * 40, patch=patch) == patch.commit_sha
    await client.create_branch(f"signal/{operation_id.hex}", patch.commit_sha)
    pr = await client.create_pr(
        branch_name=f"signal/{operation_id.hex}",
        body="Exact body",
        expected_head=patch.commit_sha,
        expected_base="d" * 40,
    )
    assert pr.number == 7
    assert allowed == ["tree", "commit", "branch", "pr"]
    assert all(call["url"].startswith("https://api.github.com/") for call in calls)
    assert all(call["method"] == "POST" for call in calls)
    assert calls[0]["profile"] == EgressProfile.GITHUB_REST
    assert all(call["profile"] == EgressProfile.GITHUB_REPOSITORY_WRITE for call in calls[1:])
    assert all(call["github_write_scope"].full_name == "SignalOwner/website" for call in calls[1:])
    assert all("headers" not in call for call in calls)
    assert json.loads(calls[0]["body"])["permissions"] == {
        "contents": "write",
        "pull_requests": "write",
    }
    assert json.loads(calls[3]["body"])["ref"] == f"refs/heads/signal/{operation_id.hex}"


@pytest.mark.anyio
@pytest.mark.parametrize("step", ["trees", "commits", "refs", "pulls"])
async def test_lost_response_at_each_mutation_is_ambiguous(monkeypatch, step):
    client, patch, operation_id, _, _ = _provider(monkeypatch, lost_step=step)
    await client.acquire_token()
    with pytest.raises(GitHubPrProviderError) as failure:
        if step == "trees":
            await client.create_tree(base_tree_sha="e" * 40, patch=patch)
        elif step == "commits":
            await client.create_commit(base_sha="d" * 40, patch=patch)
        elif step == "refs":
            await client.create_branch(f"signal/{operation_id.hex}", patch.commit_sha)
        else:
            await client.create_pr(
                branch_name=f"signal/{operation_id.hex}",
                body="Exact body",
                expected_head=patch.commit_sha,
                expected_base="d" * 40,
            )
    assert failure.value.ambiguous


@pytest.mark.anyio
async def test_pre_dispatch_spacing_deferral_keeps_identity_and_rechecks_fence(monkeypatch):
    client, patch, _, calls, allowed = _provider(monkeypatch)
    await client.acquire_token()
    original = SharedEgressProvider.request_json
    attempts = []

    def deferred_once(self, **kwargs):
        attempts.append(kwargs["operation_id"])
        if len(attempts) == 1:
            raise ProviderEgressUnavailable("EGRESS_DEFERRED", retryable=True)
        return original(self, **kwargs)

    monkeypatch.setattr(SharedEgressProvider, "request_json", deferred_once)
    await client.create_tree(base_tree_sha="e" * 40, patch=patch)
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert allowed == ["tree"]
    assert len(calls) == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,code",
    [
        (409, "GITHUB_WRITE_CONFLICT"),
        (422, "GITHUB_WRITE_CONFLICT"),
        (429, "GITHUB_RATE_LIMITED"),
        (500, "GITHUB_PROVIDER_UNAVAILABLE"),
    ],
)
async def test_rejected_and_retryable_write_responses_never_imply_not_applied(
    monkeypatch, status, code
):
    client, patch, _, _, _ = _provider(monkeypatch, mutation_status=status)
    await client.acquire_token()
    with pytest.raises(GitHubPrProviderError) as failure:
        await client.create_tree(base_tree_sha="e" * 40, patch=patch)
    assert failure.value.code == code and failure.value.ambiguous


@pytest.mark.anyio
async def test_foreign_pr_on_the_deterministic_branch_cannot_be_reconciled(monkeypatch):
    client, patch, operation_id, _, _ = _provider(monkeypatch)
    branch = f"signal/{operation_id.hex}"

    async def existing_pulls(requested_branch):
        assert requested_branch == branch
        return (
            {
                "number": 7,
                "html_url": "https://github.com/SignalOwner/website/pull/7",
                "head": {"ref": branch, "sha": patch.commit_sha},
                "base": {"ref": "main", "sha": "d" * 40},
                "body": "Different operation",
                "state": "open",
            },
        )

    monkeypatch.setattr(client, "list_pulls", existing_pulls)
    with pytest.raises(GitHubPrProviderError) as failure:
        await _reconcile_step(client, "pr", branch, "d" * 40, patch, "Exact body")
    assert failure.value.code == "GITHUB_PR_MISMATCH"
