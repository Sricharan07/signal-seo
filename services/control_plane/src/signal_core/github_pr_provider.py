"""Fixed-route GitHub Git-data and PR adapter behind connector shared egress."""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2
from anyio import sleep, to_thread

from signal_core.crawl_http import CrawlFetchRejected
from signal_core.egress_profiles import EgressProfile, GitHubRepositoryWriteScope
from signal_core.github_app import (
    GITHUB_API_ORIGIN,
    GITHUB_API_VERSION,
    GitHubAppCredentials,
    GitHubAppProtocolError,
    GitHubRepositoryTarget,
    _app_jwt,
    _bounded_json,
    _installation_token,
    _validated_credentials,
    _validated_target,
)
from signal_core.github_pr_patch import GitHubPrPatch
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider

_SHA1 = re.compile(r"[0-9a-f]{40}")
_WRITE_PERMISSIONS = {"contents": "write", "pull_requests": "write"}
_RECONCILE_PERMISSIONS = {"contents": "read", "pull_requests": "read"}


class GitHubPrProviderError(Exception):
    """Provider state, permission, or response fails a closed write contract."""

    def __init__(self, code: str, *, ambiguous: bool = False) -> None:
        self.code = code
        self.ambiguous = ambiguous
        super().__init__(code)


@dataclass(frozen=True)
class GitHubOpenedPullRequest:
    number: int
    url: str
    head_sha: str
    base_sha: str
    body: str


class _FencedFetcher:
    def __init__(self, fetcher: object, check: Callable[[], None]) -> None:
        self.fetcher = fetcher
        self.check = check

    def request(self, request: object, *, policy: object) -> object:
        try:
            self.check()
        except Exception:
            raise CrawlFetchRejected("GitHub write permit denied.") from None
        return self.fetcher.request(request, policy=policy)


class GitHubWriteEgressTransport(httpx2.AsyncBaseTransport):
    """Only one repository's Git-object/ref and PR routes can reach the gateway."""

    def __init__(
        self,
        provider: SharedEgressProvider,
        target: GitHubRepositoryTarget,
        permit_check: Callable[[str], None],
        *,
        operation_id: UUID,
        patch: GitHubPrPatch,
        base_sha: str,
        base_tree_sha: str,
        pr_body: str,
        read_guard: Callable[[], None] | None = None,
    ) -> None:
        if not isinstance(provider, SharedEgressProvider) or provider.purpose != "connector":
            raise ValueError("GitHub writes require connector shared egress.")
        if (
            provider.policy.allowed_origins != (GITHUB_API_ORIGIN,)
            or provider.policy.user_agent != "SignalBot/1.0 (+https://signal.example/bot)"
            or provider.policy.max_redirects != 0
            or provider.policy.max_body_bytes > 256 * 1024
            or provider.policy.request_timeout_seconds > 5
        ):
            raise ValueError("GitHub write egress profile is not exact and bounded.")
        self.provider = provider
        self.target = _validated_target(target)
        self.permit_check = permit_check
        self.operation_id = operation_id
        self.patch = patch
        self.base_sha = base_sha
        self.base_tree_sha = base_tree_sha
        self.pr_body = pr_body
        self.read_guard = read_guard
        self.permissions = _WRITE_PERMISSIONS if read_guard is None else _RECONCILE_PERMISSIONS
        self.last_egress_operation_id: UUID | None = None
        self.write_scope: GitHubRepositoryWriteScope | None = None

    def _expected_body(self, step: str | None) -> dict:
        if step is None:
            return {
                "repositories": [self.target.repository],
                "permissions": self.permissions,
            }
        if step == "tree":
            return {
                "base_tree": self.base_tree_sha,
                "tree": [
                    {
                        "path": self.patch.path,
                        "mode": "100644",
                        "type": "blob",
                        "content": self.patch.content.decode("utf-8"),
                    }
                ],
            }
        if step == "commit":
            actor = {
                "name": "Signal",
                "email": "signal@users.noreply.github.com",
                "date": self.patch.commit_date,
            }
            return {
                "message": self.patch.commit_message,
                "tree": self.patch.tree_sha,
                "parents": [self.base_sha],
                "author": actor,
                "committer": actor,
            }
        if step == "branch":
            return {
                "ref": f"refs/heads/signal/{self.operation_id.hex}",
                "sha": self.patch.commit_sha,
            }
        return {
            "title": "Technical SEO fix",
            "head": f"signal/{self.operation_id.hex}",
            "base": self.target.base_branch,
            "body": self.pr_body,
            "draft": False,
            "maintainer_can_modify": False,
        }

    def _check_body(self, request: httpx2.Request, step: str | None) -> None:
        try:
            document = json.loads(request.content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise httpx2.ConnectError("GitHub write body rejected.", request=request) from None
        if document != self._expected_body(step):
            raise httpx2.ConnectError("GitHub write body rejected.", request=request)

    def _route(self, request: httpx2.Request) -> str | None:
        url = request.url
        repo = f"/repos/{self.target.owner}/{self.target.repository}"
        path = url.path
        if (
            url.scheme != "https"
            or url.host != "api.github.com"
            or url.port not in {None, 443}
            or url.username
            or url.password
            or url.fragment
            or request.headers.get("x-github-api-version") != GITHUB_API_VERSION
            or request.headers.get("user-agent") != "Signal-SEO-Agent/0.0.0"
            or request.headers.get("accept") != "application/vnd.github+json"
            or not request.headers.get("authorization", "").startswith("Bearer ")
            or len(request.content) > 128 * 1024
        ):
            raise httpx2.ConnectError("GitHub write route rejected.", request=request)
        token_path = f"/app/installations/{self.target.installation_id}/access_tokens"
        if request.method == "POST" and path == token_path and not url.query:
            self._check_body(request, None)
            return None
        if not path.startswith(repo + "/"):
            raise httpx2.ConnectError("GitHub write route rejected.", request=request)
        suffix = path[len(repo) :]
        writes = {
            "/git/trees": "tree",
            "/git/commits": "commit",
            "/git/refs": "branch",
            "/pulls": "pr",
        }
        if request.method == "POST" and suffix in writes and not url.query:
            if self.read_guard is not None:
                raise httpx2.ConnectError(
                    "Read-only reconciliation rejects writes.", request=request
                )
            step = writes[suffix]
            self._check_body(request, step)
            return step
        if request.method == "GET":
            if suffix == "/pulls":
                if len(url.params) != 4 or dict(url.params) != {
                    "head": f"{self.target.owner}:signal/{self.operation_id.hex}",
                    "base": self.target.base_branch,
                    "state": "all",
                    "per_page": "100",
                }:
                    raise httpx2.ConnectError("GitHub PR query rejected.", request=request)
                return None
            if url.query:
                raise httpx2.ConnectError("GitHub write route rejected.", request=request)
            if suffix in {
                f"/git/trees/{self.patch.tree_sha}",
                f"/git/commits/{self.patch.commit_sha}",
                f"/git/ref/heads/signal/{self.operation_id.hex}",
            }:
                return None
        raise httpx2.ConnectError("GitHub write route rejected.", request=request)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        step = self._route(request)
        if request.method == "POST" and request.headers.get("content-type") != "application/json":
            raise httpx2.ConnectError("GitHub write content type rejected.", request=request)
        token_exchange = request.url.path.startswith("/app/installations/")
        if not token_exchange and self.write_scope is None:
            raise httpx2.ConnectError("Validated installation token required.", request=request)
        operation_id = uuid4()
        self.last_egress_operation_id = operation_id
        provider = self.provider
        if self.read_guard is not None:
            provider = replace(provider, fetcher=_FencedFetcher(provider.fetcher, self.read_guard))
        if step is not None:
            provider = replace(
                provider,
                fetcher=_FencedFetcher(provider.fetcher, lambda: self.permit_check(step)),
            )
        deadline = time.monotonic() + 5
        while True:
            try:
                result = await to_thread.run_sync(
                    lambda: provider.request_json(
                        method=request.method,
                        url=str(request.url),
                        profile=EgressProfile.GITHUB_REST
                        if token_exchange or self.read_guard is not None
                        else EgressProfile.GITHUB_REPOSITORY_WRITE,
                        authorization=request.headers["authorization"],
                        github_write_scope=None
                        if token_exchange or self.read_guard is not None
                        else self.write_scope,
                        body=request.content,
                        operation_id=operation_id,
                        timeout_seconds=5.0,
                        max_response_bytes=256 * 1024,
                    )
                )
                break
            except ProviderEgressUnavailable as error:
                if error.code == "EGRESS_DEFERRED" and time.monotonic() < deadline:
                    await sleep(0.25)
                    continue
                raise httpx2.ConnectError(
                    f"GitHub write egress unavailable ({error.code}).", request=request
                ) from None
            except ValueError:
                raise httpx2.ConnectError(
                    "GitHub write egress unavailable.", request=request
                ) from None
        return httpx2.Response(
            result.status_code,
            headers={"content-type": result.media_type},
            content=result.body,
            request=request,
        )


class GitHubPrProvider:
    def __init__(
        self,
        *,
        credentials: GitHubAppCredentials,
        target: GitHubRepositoryTarget,
        transport: GitHubWriteEgressTransport,
    ) -> None:
        if not isinstance(transport, GitHubWriteEgressTransport):
            raise ValueError("GitHub PR provider requires fixed-route shared egress.")
        if transport.target != target:
            raise ValueError("GitHub PR provider target differs from its egress route.")
        self.credentials = credentials
        self.target = target
        self.transport = transport
        self.repo_path = f"/repos/{target.owner}/{target.repository}"
        self.token: str | None = None

    async def acquire_token(self) -> None:
        app_id, key = _validated_credentials(self.credentials)
        app_jwt = _app_jwt(app_id=app_id, key=key, now=datetime.now(UTC))
        document = await self._request(
            "POST",
            f"/app/installations/{self.target.installation_id}/access_tokens",
            authorization=app_jwt,
            body={
                "repositories": [self.target.repository],
                "permissions": self.transport.permissions,
            },
        )
        try:
            self.token = _installation_token(
                document, self.target, datetime.now(UTC), self.transport.permissions
            )[0]
            self.transport.write_scope = GitHubRepositoryWriteScope(
                self.target.owner, self.target.repository, self.token
            )
        except GitHubAppProtocolError as error:
            raise GitHubPrProviderError(error.code) from None

    async def create_tree(self, *, base_tree_sha: str, patch: GitHubPrPatch) -> str:
        document = await self._request(
            "POST",
            f"{self.repo_path}/git/trees",
            body={
                "base_tree": base_tree_sha,
                "tree": [
                    {
                        "path": patch.path,
                        "mode": "100644",
                        "type": "blob",
                        "content": patch.content.decode("utf-8"),
                    }
                ],
            },
            mutation=True,
        )
        sha = self._sha(document)
        if sha != patch.tree_sha:
            raise GitHubPrProviderError("GITHUB_TREE_MISMATCH", ambiguous=True)
        return sha

    async def create_commit(self, *, base_sha: str, patch: GitHubPrPatch) -> str:
        actor = {
            "name": "Signal",
            "email": "signal@users.noreply.github.com",
            "date": patch.commit_date,
        }
        document = await self._request(
            "POST",
            f"{self.repo_path}/git/commits",
            body={
                "message": patch.commit_message,
                "tree": patch.tree_sha,
                "parents": [base_sha],
                "author": actor,
                "committer": actor,
            },
            mutation=True,
        )
        sha = self._sha(document)
        self._verify_commit(document, sha, base_sha, patch)
        return sha

    async def create_branch(self, branch_name: str, commit_sha: str) -> None:
        document = await self._request(
            "POST",
            f"{self.repo_path}/git/refs",
            body={"ref": f"refs/heads/{branch_name}", "sha": commit_sha},
            mutation=True,
        )
        self._verify_ref(document, branch_name, commit_sha)

    async def create_pr(
        self, *, branch_name: str, body: str, expected_head: str, expected_base: str
    ) -> GitHubOpenedPullRequest:
        document = await self._request(
            "POST",
            f"{self.repo_path}/pulls",
            body={
                "title": "Technical SEO fix",
                "head": branch_name,
                "base": self.target.base_branch,
                "body": body,
                "draft": False,
                "maintainer_can_modify": False,
            },
            mutation=True,
        )
        return self._verify_pr(document, branch_name, expected_head, expected_base, body)

    async def get_tree(self, sha: str) -> bool:
        document = await self._request("GET", f"{self.repo_path}/git/trees/{sha}", missing=True)
        return document is not None and self._sha(document) == sha

    async def get_commit(self, sha: str, base_sha: str, patch: GitHubPrPatch) -> bool:
        document = await self._request("GET", f"{self.repo_path}/git/commits/{sha}", missing=True)
        if document is None:
            return False
        self._verify_commit(document, sha, base_sha, patch)
        return True

    async def get_branch(self, branch_name: str) -> str | None:
        document = await self._request(
            "GET", f"{self.repo_path}/git/ref/heads/{branch_name}", missing=True
        )
        if document is None:
            return None
        value = document.get("object") if isinstance(document, dict) else None
        if (
            not isinstance(value, dict)
            or value.get("type") != "commit"
            or not isinstance(value.get("sha"), str)
            or _SHA1.fullmatch(value["sha"]) is None
        ):
            raise GitHubPrProviderError("GITHUB_PROVIDER_RESPONSE_REJECTED")
        self._verify_ref(document, branch_name, value["sha"])
        return value["sha"]

    async def list_pulls(self, branch_name: str) -> tuple[dict, ...]:
        async with self._client() as client:
            document = await self._request_on_client(
                client,
                "GET",
                f"{self.repo_path}/pulls",
                params={
                    "head": f"{self.target.owner}:{branch_name}",
                    "base": self.target.base_branch,
                    "state": "all",
                    "per_page": "100",
                },
            )
        if (
            not isinstance(document, list)
            or len(document) > 100
            or any(not isinstance(x, dict) for x in document)
        ):
            raise GitHubPrProviderError("GITHUB_PROVIDER_RESPONSE_REJECTED")
        return tuple(document)

    def parse_existing_pr(
        self,
        document: dict,
        branch_name: str,
        expected_head: str,
        expected_base: str,
        body: str,
    ) -> GitHubOpenedPullRequest:
        return self._verify_pr(document, branch_name, expected_head, expected_base, body)

    @staticmethod
    def _sha(document: object) -> str:
        if (
            not isinstance(document, dict)
            or not isinstance(document.get("sha"), str)
            or _SHA1.fullmatch(document["sha"]) is None
        ):
            raise GitHubPrProviderError("GITHUB_PROVIDER_RESPONSE_REJECTED")
        return document["sha"]

    @staticmethod
    def _verify_commit(document: object, sha: str, base_sha: str, patch: GitHubPrPatch) -> None:
        tree = document.get("tree") if isinstance(document, dict) else None
        parents = document.get("parents") if isinstance(document, dict) else None
        if (
            not isinstance(document, dict)
            or document.get("sha") != sha
            or sha != patch.commit_sha
            or not isinstance(tree, dict)
            or tree.get("sha") != patch.tree_sha
            or not isinstance(parents, list)
            or [parent.get("sha") if isinstance(parent, dict) else None for parent in parents]
            != [base_sha]
            or document.get("message") != patch.commit_message
        ):
            raise GitHubPrProviderError("GITHUB_COMMIT_MISMATCH", ambiguous=True)

    @staticmethod
    def _verify_ref(document: object, branch_name: str, commit_sha: str) -> None:
        if (
            not isinstance(document, dict)
            or document.get("ref") != f"refs/heads/{branch_name}"
            or document.get("object", {}).get("sha") != commit_sha
            or document.get("object", {}).get("type") != "commit"
        ):
            raise GitHubPrProviderError("GITHUB_BRANCH_MISMATCH", ambiguous=True)

    def _verify_pr(
        self,
        document: object,
        branch_name: str,
        expected_head: str,
        expected_base: str,
        body: str,
    ) -> GitHubOpenedPullRequest:
        if not isinstance(document, dict):
            raise GitHubPrProviderError("GITHUB_PR_MISMATCH", ambiguous=True)
        number, url = document.get("number"), document.get("html_url")
        if (
            type(number) is not int
            or number < 1
            or url
            != f"https://github.com/{self.target.owner}/{self.target.repository}/pull/{number}"
            or document.get("head", {}).get("ref") != branch_name
            or document.get("head", {}).get("sha") != expected_head
            or document.get("base", {}).get("ref") != self.target.base_branch
            or document.get("base", {}).get("sha") != expected_base
            or document.get("body") != body
            or document.get("state") != "open"
        ):
            raise GitHubPrProviderError("GITHUB_PR_MISMATCH", ambiguous=True)
        return GitHubOpenedPullRequest(number, url, expected_head, expected_base, body)

    def _client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            base_url=GITHUB_API_ORIGIN,
            timeout=httpx2.Timeout(5.0),
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        authorization: str | None = None,
        body: dict | None = None,
        missing: bool = False,
        mutation: bool = False,
    ) -> object:
        async with self._client() as client:
            return await self._request_on_client(
                client,
                method,
                path,
                authorization=authorization,
                body=body,
                missing=missing,
                mutation=mutation,
            )

    async def _request_on_client(
        self,
        client: httpx2.AsyncClient,
        method: str,
        path: str,
        *,
        authorization: str | None = None,
        body: dict | None = None,
        params: dict[str, str] | None = None,
        missing: bool = False,
        mutation: bool = False,
    ) -> object:
        token = authorization or self.token
        if token is None:
            raise GitHubPrProviderError("GITHUB_WRITE_TOKEN_UNAVAILABLE")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Signal-SEO-Agent/0.0.0",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        try:
            async with client.stream(
                method, path, headers=headers, json=body, params=params
            ) as response:
                if missing and response.status_code == 404:
                    return None
                if response.status_code in {401, 403, 404}:
                    raise GitHubPrProviderError("GITHUB_AUTHORIZATION_REJECTED", ambiguous=mutation)
                if response.status_code in {409, 422}:
                    raise GitHubPrProviderError("GITHUB_WRITE_CONFLICT", ambiguous=mutation)
                if response.status_code == 429:
                    raise GitHubPrProviderError("GITHUB_RATE_LIMITED", ambiguous=mutation)
                if response.status_code >= 500:
                    raise GitHubPrProviderError("GITHUB_PROVIDER_UNAVAILABLE", ambiguous=mutation)
                if response.status_code not in {200, 201}:
                    raise GitHubPrProviderError(
                        "GITHUB_PROVIDER_RESPONSE_REJECTED", ambiguous=mutation
                    )
                content = await _bounded_json(response)
            return json.loads(content)
        except (httpx2.HTTPError, UnicodeDecodeError, json.JSONDecodeError):
            raise GitHubPrProviderError(
                "GITHUB_OUTCOME_UNKNOWN" if mutation else "GITHUB_PROVIDER_UNAVAILABLE",
                ambiguous=mutation,
            ) from None
        except GitHubAppProtocolError:
            raise GitHubPrProviderError(
                "GITHUB_PROVIDER_RESPONSE_REJECTED", ambiguous=mutation
            ) from None
