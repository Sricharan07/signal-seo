import json
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey
from signal_core.github_app import (
    GITHUB_API_ORIGIN,
    GITHUB_API_VERSION,
    GitHubAppCredentials,
    GitHubAppProtocolError,
    GitHubRepositoryTarget,
    inspect_github_pr_authority,
    inspect_github_repository,
    inspect_github_repository_inventory,
)

NOW = datetime(2026, 9, 21, 2, 0, tzinfo=UTC)
APP_ID = 123456
INSTALLATION_ID = 987654
OWNER = "Acme-Search"
REPOSITORY = "website"
INSTALLATION_TOKEN = "ghs_123456_" + "a" * 24 + "." + "b" * 24 + "-v2"
BASE_SHA = "a" * 40
KEY = RSAKey.generate_key(parameters={"alg": "RS256", "use": "sig"})
CREDENTIALS = GitHubAppCredentials(
    app_id=APP_ID,
    private_key_pem=KEY.as_pem(private=True).decode("ascii"),
)
TARGET = GitHubRepositoryTarget(
    installation_id=INSTALLATION_ID,
    owner=OWNER,
    repository=REPOSITORY,
    base_branch="main",
    content_path="app/page.tsx",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def provider_response(status: int, document: object) -> httpx2.Response:
    return httpx2.Response(
        status,
        headers={"content-type": "application/json"},
        json=document,
    )


def token_document(**overrides) -> dict:
    document = {
        "token": INSTALLATION_TOKEN,
        "expires_at": (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "permissions": {"contents": "read", "metadata": "read"},
        "repository_selection": "selected",
        "repositories": [
            {"id": 321, "full_name": f"{OWNER}/{REPOSITORY}"},
        ],
    }
    document.update(overrides)
    return document


def repository_document(**overrides) -> dict:
    document = {
        "id": 321,
        "full_name": f"{OWNER}/{REPOSITORY}",
        "private": True,
        "default_branch": "main",
        "archived": False,
        "disabled": False,
    }
    document.update(overrides)
    return document


def branch_document(**overrides) -> dict:
    document = {"name": "main", "commit": {"sha": BASE_SHA}, "protected": True}
    document.update(overrides)
    return document


@pytest.mark.anyio
async def test_inspects_exact_repository_with_downscoped_installation_token():
    calls = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        assert request.headers["accept"] == "application/vnd.github+json"
        assert request.headers["x-github-api-version"] == GITHUB_API_VERSION
        assert request.headers["user-agent"] == "Signal-SEO-Agent/0.0.0"
        if request.url.path == f"/app/installations/{INSTALLATION_ID}/access_tokens":
            encoded = request.headers["authorization"].removeprefix("Bearer ")
            decoded = jwt.decode(encoded, KEY, algorithms=["RS256"])
            assert decoded.claims["iss"] == str(APP_ID)
            assert decoded.claims["iat"] == int((NOW - timedelta(seconds=60)).timestamp())
            assert decoded.claims["exp"] == int((NOW + timedelta(minutes=9)).timestamp())
            assert json.loads(request.content) == {
                "repositories": [REPOSITORY],
                "permissions": {"contents": "read"},
            }
            return provider_response(201, token_document())
        assert request.headers["authorization"] == f"Bearer {INSTALLATION_TOKEN}"
        if request.url.path == f"/repos/{OWNER}/{REPOSITORY}":
            return provider_response(200, repository_document())
        if request.url.path == f"/repos/{OWNER}/{REPOSITORY}/branches/main":
            return provider_response(200, branch_document())
        pytest.fail(f"Unexpected provider path: {request.url.path}")

    snapshot = await inspect_github_repository(
        credentials=CREDENTIALS,
        target=TARGET,
        now=NOW,
        transport=httpx2.MockTransport(handler),
    )

    assert len(calls) == 3
    assert snapshot.installation_id == INSTALLATION_ID
    assert snapshot.repository_id == 321
    assert snapshot.full_name == f"{OWNER}/{REPOSITORY}"
    assert snapshot.private is True
    assert snapshot.default_branch == "main"
    assert snapshot.base_branch == "main"
    assert snapshot.base_sha == BASE_SHA
    assert snapshot.protected is True
    assert snapshot.content_path == "app/page.tsx"
    assert snapshot.credential_expires_at == NOW + timedelta(hours=1)
    assert INSTALLATION_TOKEN not in repr(snapshot)
    assert CREDENTIALS.private_key_pem not in repr(snapshot)


@pytest.mark.anyio
async def test_pr_permission_inspection_requests_only_one_repository_and_never_writes():
    calls = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/access_tokens"):
            assert json.loads(request.content) == {
                "repositories": [REPOSITORY],
                "permissions": {"contents": "read", "pull_requests": "write"},
            }
            return provider_response(
                201,
                token_document(
                    permissions={
                        "contents": "read",
                        "pull_requests": "write",
                        "metadata": "read",
                    }
                ),
            )
        if request.url.path.endswith("/branches/main"):
            return provider_response(200, branch_document())
        return provider_response(200, repository_document())

    observed = await inspect_github_pr_authority(
        credentials=CREDENTIALS,
        target=TARGET,
        now=NOW,
        transport=httpx2.MockTransport(handler),
    )
    assert observed.repository_id == 321
    assert calls == [
        ("POST", f"/app/installations/{INSTALLATION_ID}/access_tokens"),
        ("GET", f"/repos/{OWNER}/{REPOSITORY}"),
        ("GET", f"/repos/{OWNER}/{REPOSITORY}/branches/main"),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "permissions",
    [
        {"contents": "read"},
        {"contents": "write", "pull_requests": "write"},
        {"contents": "read", "pull_requests": "read"},
        {"contents": "read", "pull_requests": "write", "checks": "read"},
        {"contents": "read", "pull_requests": "write", "metadata": "write"},
    ],
)
async def test_pr_permission_inspection_rejects_missing_or_broadened_token(permissions):
    with pytest.raises(GitHubAppProtocolError, match="GITHUB_SCOPE_REJECTED"):
        await inspect_github_pr_authority(
            credentials=CREDENTIALS,
            target=TARGET,
            now=NOW,
            transport=httpx2.MockTransport(
                lambda _: provider_response(201, token_document(permissions=permissions))
            ),
        )


@pytest.mark.anyio
async def test_read_only_inventory_binds_commit_and_tree_with_bounded_paths():
    calls = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append((request.method, request.url.path, request.url.query))
        if request.url.path.endswith("/access_tokens"):
            assert json.loads(request.content)["permissions"] == {"contents": "read"}
            return provider_response(201, token_document())
        if request.url.path.endswith("/branches/main"):
            return provider_response(200, branch_document())
        if request.url.path.endswith("/git/commits/" + BASE_SHA):
            return provider_response(200, {"sha": BASE_SHA, "tree": {"sha": "b" * 40}})
        if request.url.path.endswith("/git/trees/" + "b" * 40):
            assert request.url.query == b"recursive=1"
            return provider_response(
                200,
                {
                    "sha": "b" * 40,
                    "truncated": False,
                    "tree": [
                        {
                            "path": "next.config.mjs",
                            "mode": "100644",
                            "type": "blob",
                            "sha": "c" * 40,
                        },
                        {"path": "app/page.tsx", "mode": "100644", "type": "blob", "sha": "d" * 40},
                    ],
                },
            )
        return provider_response(200, repository_document())

    inventory = await inspect_github_repository_inventory(
        credentials=CREDENTIALS,
        target=TARGET,
        now=NOW,
        transport=httpx2.MockTransport(handler),
    )
    assert inventory.snapshot.base_sha == BASE_SHA
    assert inventory.tree_sha == "b" * 40
    assert not inventory.truncated
    assert [entry.path for entry in inventory.entries] == ["next.config.mjs", "app/page.tsx"]
    assert len(calls) == 5
    assert all(method == "GET" for method, _, _ in calls[1:])


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_tree",
    [
        {"sha": "e" * 40, "truncated": False, "tree": []},
        {"sha": "b" * 40, "truncated": "false", "tree": []},
        {
            "sha": "b" * 40,
            "truncated": False,
            "tree": [{"path": "../outside", "mode": "100644", "type": "blob", "sha": "c" * 40}],
        },
    ],
)
async def test_inventory_rejects_wrong_tree_identity_or_unsafe_paths(bad_tree):
    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/access_tokens"):
            return provider_response(201, token_document())
        if request.url.path.endswith("/branches/main"):
            return provider_response(200, branch_document())
        if request.url.path.endswith("/git/commits/" + BASE_SHA):
            return provider_response(200, {"sha": BASE_SHA, "tree": {"sha": "b" * 40}})
        if "/git/trees/" in request.url.path:
            return provider_response(200, bad_tree)
        return provider_response(200, repository_document())

    with pytest.raises(GitHubAppProtocolError, match="GITHUB_PROVIDER_RESPONSE_REJECTED"):
        await inspect_github_repository_inventory(
            credentials=CREDENTIALS,
            target=TARGET,
            now=NOW,
            transport=httpx2.MockTransport(handler),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "token_override",
    [
        {"permissions": {"contents": "write", "metadata": "read"}},
        {"permissions": {"contents": "read", "issues": "read"}},
        {"repository_selection": "all"},
        {"repositories": []},
        {"repositories": [{"id": 321, "full_name": "Other/site"}]},
        {"expires_at": (NOW + timedelta(seconds=30)).isoformat()},
    ],
)
async def test_rejects_broadened_or_incoherent_installation_tokens(token_override: dict):
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path.startswith("/app/installations/")
        return provider_response(201, token_document(**token_override))

    with pytest.raises(GitHubAppProtocolError, match="GITHUB_SCOPE_REJECTED"):
        await inspect_github_repository(
            credentials=CREDENTIALS,
            target=TARGET,
            now=NOW,
            transport=httpx2.MockTransport(handler),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("document", "code"),
    [
        (repository_document(id=999), "GITHUB_PROVIDER_RESPONSE_REJECTED"),
        (repository_document(archived=True), "GITHUB_REPOSITORY_STATE_REJECTED"),
        (repository_document(disabled=True), "GITHUB_REPOSITORY_STATE_REJECTED"),
        (repository_document(default_branch="bad..branch"), "GITHUB_PROVIDER_RESPONSE_REJECTED"),
    ],
)
async def test_rejects_wrong_or_ineligible_repository_state(document: dict, code: str):
    call = 0

    async def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal call
        call += 1
        if call == 1:
            return provider_response(201, token_document())
        return provider_response(200, document)

    with pytest.raises(GitHubAppProtocolError, match=code):
        await inspect_github_repository(
            credentials=CREDENTIALS,
            target=TARGET,
            now=NOW,
            transport=httpx2.MockTransport(handler),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "document",
    [
        branch_document(name="develop"),
        branch_document(commit={"sha": "short"}),
        branch_document(protected="yes"),
    ],
)
async def test_rejects_branch_identity_that_does_not_match_target(document: dict):
    call = 0

    async def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal call
        call += 1
        if call == 1:
            return provider_response(201, token_document())
        if call == 2:
            return provider_response(200, repository_document())
        return provider_response(200, document)

    with pytest.raises(GitHubAppProtocolError, match="GITHUB_PROVIDER_RESPONSE_REJECTED"):
        await inspect_github_repository(
            credentials=CREDENTIALS,
            target=TARGET,
            now=NOW,
            transport=httpx2.MockTransport(handler),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_response_value", "code", "retryable"),
    [
        (
            provider_response(401, {"message": "secret is never returned"}),
            "GITHUB_AUTHORIZATION_REJECTED",
            False,
        ),
        (provider_response(403, {"message": "denied"}), "GITHUB_AUTHORIZATION_REJECTED", False),
        (provider_response(404, {"message": "missing"}), "GITHUB_AUTHORIZATION_REJECTED", False),
        (provider_response(409, {"message": "empty"}), "GITHUB_REPOSITORY_STATE_REJECTED", False),
        (provider_response(422, {"message": "scope"}), "GITHUB_SCOPE_REJECTED", False),
        (provider_response(429, {"message": "rate"}), "GITHUB_PROVIDER_UNAVAILABLE", True),
        (provider_response(503, {"message": "down"}), "GITHUB_PROVIDER_UNAVAILABLE", True),
        (
            httpx2.Response(302, headers={"location": "https://attacker.example"}),
            "GITHUB_PROVIDER_RESPONSE_REJECTED",
            False,
        ),
        (
            httpx2.Response(201, headers={"content-type": "text/html"}, text="no"),
            "GITHUB_PROVIDER_RESPONSE_REJECTED",
            False,
        ),
    ],
)
async def test_maps_provider_failures_to_fixed_nonsecret_codes(
    provider_response_value: httpx2.Response,
    code: str,
    retryable: bool,
):
    with pytest.raises(GitHubAppProtocolError, match=code) as raised:
        await inspect_github_repository(
            credentials=CREDENTIALS,
            target=TARGET,
            now=NOW,
            transport=httpx2.MockTransport(lambda _: provider_response_value),
        )
    assert raised.value.retryable is retryable
    assert "secret" not in str(raised.value)
    assert INSTALLATION_TOKEN not in str(raised.value)
    assert CREDENTIALS.private_key_pem not in str(raised.value)


@pytest.mark.anyio
async def test_transport_failure_is_retryable_and_does_not_leak_credentials():
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("provider unavailable", request=request)

    with pytest.raises(GitHubAppProtocolError, match="GITHUB_PROVIDER_UNAVAILABLE") as raised:
        await inspect_github_repository(
            credentials=CREDENTIALS,
            target=TARGET,
            now=NOW,
            transport=httpx2.MockTransport(handler),
        )
    assert raised.value.retryable is True
    assert CREDENTIALS.private_key_pem not in str(raised.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("credentials", "target", "code"),
    [
        (
            GitHubAppCredentials(0, CREDENTIALS.private_key_pem),
            TARGET,
            "GITHUB_CREDENTIALS_REJECTED",
        ),
        (GitHubAppCredentials(APP_ID, "not a private key"), TARGET, "GITHUB_CREDENTIALS_REJECTED"),
        (
            CREDENTIALS,
            GitHubRepositoryTarget(0, OWNER, REPOSITORY, "main", "app/page.tsx"),
            "GITHUB_TARGET_REJECTED",
        ),
        (
            CREDENTIALS,
            GitHubRepositoryTarget(
                INSTALLATION_ID, "bad_owner", REPOSITORY, "main", "app/page.tsx"
            ),
            "GITHUB_TARGET_REJECTED",
        ),
        (
            CREDENTIALS,
            GitHubRepositoryTarget(INSTALLATION_ID, OWNER, REPOSITORY, "../main", "app/page.tsx"),
            "GITHUB_TARGET_REJECTED",
        ),
        (
            CREDENTIALS,
            GitHubRepositoryTarget(
                INSTALLATION_ID, OWNER, REPOSITORY, "main", ".github/workflows/release.yml"
            ),
            "GITHUB_TARGET_REJECTED",
        ),
        (
            CREDENTIALS,
            GitHubRepositoryTarget(INSTALLATION_ID, OWNER, REPOSITORY, "main", "../secrets"),
            "GITHUB_TARGET_REJECTED",
        ),
    ],
)
async def test_rejects_invalid_credentials_and_targets_before_network_access(
    credentials: GitHubAppCredentials,
    target: GitHubRepositoryTarget,
    code: str,
):
    with pytest.raises(GitHubAppProtocolError, match=code):
        await inspect_github_repository(
            credentials=credentials,
            target=target,
            now=NOW,
            transport=httpx2.MockTransport(lambda _: pytest.fail("No request is allowed")),
        )


@pytest.mark.anyio
async def test_rejects_disabled_tls_verification_before_network_access():
    with pytest.raises(GitHubAppProtocolError, match="GITHUB_TLS_CONFIGURATION_REJECTED"):
        await inspect_github_repository(
            credentials=CREDENTIALS,
            target=TARGET,
            now=NOW,
            verify=False,
            transport=httpx2.MockTransport(lambda _: pytest.fail("No request is allowed")),
        )


@pytest.mark.anyio
async def test_rejects_oversized_provider_document():
    document = json.dumps({"padding": "x" * (256 * 1024)}).encode()
    response = httpx2.Response(
        201,
        headers={"content-type": "application/json"},
        content=document,
    )
    with pytest.raises(GitHubAppProtocolError, match="GITHUB_PROVIDER_RESPONSE_REJECTED"):
        await inspect_github_repository(
            credentials=CREDENTIALS,
            target=TARGET,
            now=NOW,
            transport=httpx2.MockTransport(lambda _: response),
        )


def test_provider_origin_is_fixed_to_official_github_api():
    assert GITHUB_API_ORIGIN == "https://api.github.com"
    assert CREDENTIALS.private_key_pem not in repr(CREDENTIALS)
