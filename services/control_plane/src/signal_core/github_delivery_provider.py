"""Fixed-route, one-repository read observations of customer-owned delivery."""

import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import httpx2
from anyio import sleep, to_thread

from signal_core.egress_profiles import EgressProfile
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
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider

READ_PERMISSIONS = {
    "contents": "read",
    "pull_requests": "read",
    "checks": "read",
    "deployments": "read",
    "statuses": "read",
}
_SHA = re.compile(r"[0-9a-f]{40}")
_ENVIRONMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_. /-]{0,99}")


class GitHubDeliveryUnavailable(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _id(value: object) -> int:
    if type(value) is not int or not 0 < value < 2**63:
        raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_RESPONSE_REJECTED")
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_RESPONSE_REJECTED")
    return value


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_RESPONSE_REJECTED")
    return value


def _time(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_RESPONSE_REJECTED")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result > datetime.now(UTC):
            raise ValueError
        return result
    except ValueError:
        raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_RESPONSE_REJECTED") from None


class GitHubDeliveryEgressTransport(httpx2.AsyncBaseTransport):
    def __init__(
        self,
        provider: SharedEgressProvider,
        target: GitHubRepositoryTarget,
        *,
        pr_number: int,
        head_sha: str,
        environment: str,
        authority_guard: Callable[[], None],
    ) -> None:
        self.target = _validated_target(target)
        self.pr_number = _id(pr_number)
        self.head_sha = _sha(head_sha)
        if not isinstance(environment, str) or _ENVIRONMENT.fullmatch(environment) is None:
            raise ValueError("An owner-selected deployment environment is required.")
        if (
            not isinstance(provider, SharedEgressProvider)
            or provider.purpose != "connector"
            or provider.policy.allowed_origins != (GITHUB_API_ORIGIN,)
            or provider.policy.max_redirects != 0
            or provider.policy.max_body_bytes > 256 * 1024
            or provider.policy.request_timeout_seconds > 5
            or provider.policy.user_agent != "SignalBot/1.0 (+https://signal.example/bot)"
        ):
            raise ValueError("Delivery observations require the bounded GitHub egress profile.")
        self.provider = provider
        self.environment = environment
        self.merged_sha: str | None = None
        self.deployment_ids: set[int] = set()
        self.evidence: list[dict] = []
        self.authority_guard = authority_guard

    def _route(self, request: httpx2.Request) -> None:
        url = request.url
        repo = f"/repos/{self.target.owner}/{self.target.repository}"
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
        ):
            raise httpx2.ConnectError("Delivery read route rejected.", request=request)
        token_path = f"/app/installations/{self.target.installation_id}/access_tokens"
        if request.method == "POST" and url.path == token_path and not url.query:
            try:
                valid = json.loads(request.content) == {
                    "repositories": [self.target.repository],
                    "permissions": READ_PERMISSIONS,
                }
            except (UnicodeError, ValueError):
                valid = False
            if valid and request.headers.get("content-type") == "application/json":
                return
        if request.method == "GET" and not request.content:
            queries = {
                f"{repo}/commits/{self.head_sha}/check-runs": {
                    "per_page": "100",
                    "filter": "latest",
                },
                f"{repo}/commits/{self.head_sha}/status": {"per_page": "100"},
                f"{repo}/deployments": {"environment": self.environment, "per_page": "20"},
            }
            queries.update(
                {
                    f"{repo}/deployments/{i}/statuses": {"per_page": "20"}
                    for i in self.deployment_ids
                }
            )
            plain = {repo, f"{repo}/pulls/{self.pr_number}"}
            if self.merged_sha:
                plain.add(f"{repo}/git/commits/{self.merged_sha}")
            if url.path in plain and not url.query:
                return
            if (
                url.path in queries
                and len(url.params) == len(queries[url.path])
                and dict(url.params) == queries[url.path]
            ):
                return
        raise httpx2.ConnectError("Delivery read route rejected.", request=request)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self._route(request)
        operation_id = uuid4()
        deadline = time.monotonic() + 5
        while True:
            await to_thread.run_sync(self.authority_guard)
            try:
                result = await to_thread.run_sync(
                    lambda: self.provider.request_json(
                        method=request.method,
                        url=str(request.url),
                        profile=EgressProfile.GITHUB_REST,
                        authorization=request.headers["authorization"],
                        body=request.content,
                        operation_id=operation_id,
                        timeout_seconds=5,
                        max_response_bytes=256 * 1024,
                    )
                )
                break
            except ProviderEgressUnavailable as error:
                if error.code != "EGRESS_DEFERRED" or time.monotonic() >= deadline:
                    raise httpx2.ConnectError(
                        "Delivery read egress unavailable.", request=request
                    ) from None
                await sleep(0.25)
        if request.method == "GET":
            self.evidence.append(
                {
                    "egress_operation_id": str(operation_id),
                    "url": str(request.url),
                    "http_status": result.status_code,
                    "body_sha256": hashlib.sha256(result.body).hexdigest(),
                }
            )
        return httpx2.Response(
            result.status_code,
            headers={"content-type": result.media_type},
            content=result.body,
            request=request,
        )


class GitHubDeliveryProvider:
    def __init__(
        self,
        *,
        credentials: GitHubAppCredentials,
        target: GitHubRepositoryTarget,
        repository_id: int,
        transport: GitHubDeliveryEgressTransport,
    ) -> None:
        if not isinstance(transport, GitHubDeliveryEgressTransport) or transport.target != target:
            raise ValueError("Delivery provider target differs from its read gateway.")
        self.credentials, self.target, self.transport = credentials, target, transport
        self.repository_id = _id(repository_id)
        self.token: str | None = None
        self.repo = f"/repos/{target.owner}/{target.repository}"

    async def _request(
        self,
        path: str,
        *,
        params: dict | None = None,
        token_body: dict | None = None,
        authorization: str | None = None,
    ) -> object:
        token = authorization or self.token
        if token is None:
            raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_TOKEN_UNAVAILABLE")
        try:
            async with httpx2.AsyncClient(
                base_url=GITHUB_API_ORIGIN,
                timeout=5,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    "POST" if token_body else "GET",
                    path,
                    params=params,
                    json=token_body,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {token}",
                        "X-GitHub-Api-Version": GITHUB_API_VERSION,
                        "User-Agent": "Signal-SEO-Agent/0.0.0",
                    },
                ) as response:
                    if response.status_code == 429:
                        raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_RATE_LIMITED")
                    if response.status_code in {401, 403, 404}:
                        raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_AUTHORITY_UNAVAILABLE")
                    if response.status_code not in {200, 201}:
                        raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_UNAVAILABLE")
                    content = await _bounded_json(response)
            return json.loads(content)
        except (httpx2.HTTPError, GitHubAppProtocolError, UnicodeError, ValueError):
            raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_UNAVAILABLE") from None

    async def observe(
        self, *, expected_tree_sha: str, trusted_deployment_actor_id: int, site_origin: str
    ) -> dict:
        _sha(expected_tree_sha)
        _id(trusted_deployment_actor_id)
        app_id, key = _validated_credentials(self.credentials)
        document = await self._request(
            f"/app/installations/{self.target.installation_id}/access_tokens",
            token_body={"repositories": [self.target.repository], "permissions": READ_PERMISSIONS},
            authorization=_app_jwt(app_id=app_id, key=key, now=datetime.now(UTC)),
        )
        try:
            self.token, _, observed_repository_id = _installation_token(
                document, self.target, datetime.now(UTC), READ_PERMISSIONS
            )
        except GitHubAppProtocolError:
            raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_AUTHORITY_UNAVAILABLE") from None
        if observed_repository_id != self.repository_id:
            raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_BINDING_MISMATCH")
        repository = await self._request(self.repo)
        if (
            not isinstance(repository, dict)
            or repository.get("id") != self.repository_id
            or not isinstance(repository.get("full_name"), str)
            or repository["full_name"].casefold()
            != f"{self.target.owner}/{self.target.repository}".casefold()
        ):
            raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_BINDING_MISMATCH")
        pr = await self._request(f"{self.repo}/pulls/{self.transport.pr_number}")
        self._pr(pr)
        checks = await self._request(
            f"{self.repo}/commits/{self.transport.head_sha}/check-runs",
            params={"per_page": "100", "filter": "latest"},
        )
        statuses = await self._request(
            f"{self.repo}/commits/{self.transport.head_sha}/status", params={"per_page": "100"}
        )
        check_summary = self._checks(checks, statuses)
        result = {
            "stage": "checks" if check_summary["observed_count"] else "pr_opened",
            "reason": "WAITING_FOR_CUSTOMER_MERGE",
            "checks": check_summary,
            "merged_sha": None,
            "merged_tree_sha": None,
            "merged_at": None,
            "deployment": None,
        }
        if not pr["merged"]:
            if pr.get("state") == "closed":
                result["reason"] = "CUSTOMER_PR_CLOSED_UNMERGED"
            return result
        merged_sha, merged_at = _sha(pr["merge_commit_sha"]), _time(pr["merged_at"])
        self.transport.merged_sha = merged_sha
        commit = await self._request(f"{self.repo}/git/commits/{merged_sha}")
        if (
            not isinstance(commit, dict)
            or commit.get("sha") != merged_sha
            or not isinstance(commit.get("tree"), dict)
        ):
            raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_RESPONSE_REJECTED")
        merged_tree = _sha(commit["tree"].get("sha"))
        result.update(
            stage="merged",
            reason="WAITING_FOR_CUSTOMER_DEPLOYMENT",
            merged_sha=merged_sha,
            merged_tree_sha=merged_tree,
            merged_at=merged_at.isoformat(),
        )
        if merged_tree != expected_tree_sha:
            result["reason"] = "EC_076_MERGED_TREE_MISMATCH"
            return result
        deployments = await self._request(
            f"{self.repo}/deployments",
            params={"environment": self.transport.environment, "per_page": "20"},
        )
        if (
            not isinstance(deployments, list)
            or len(deployments) > 20
            or any(not isinstance(d, dict) for d in deployments)
        ):
            raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_RESPONSE_REJECTED")
        if not deployments:
            return result
        latest = max(deployments, key=lambda d: (_time(d.get("created_at")), _id(d.get("id"))))
        deployment_id = _id(latest.get("id"))
        if latest.get("sha") != merged_sha:
            result["reason"] = "EC_123_DEPLOYMENT_COMMIT_MISMATCH"
            return result
        if (
            latest.get("environment") != self.transport.environment
            or latest.get("production_environment") is not True
            or latest.get("transient_environment") is not False
            or _object(latest.get("creator")).get("id") != trusted_deployment_actor_id
            or _time(latest.get("created_at")) < merged_at
        ):
            result["reason"] = "EC_123_DEPLOYMENT_IDENTITY_UNAVAILABLE"
            return result
        self.transport.deployment_ids.add(deployment_id)
        states = await self._request(
            f"{self.repo}/deployments/{deployment_id}/statuses", params={"per_page": "20"}
        )
        if (
            not isinstance(states, list)
            or len(states) > 20
            or any(not isinstance(s, dict) for s in states)
        ):
            raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_RESPONSE_REJECTED")
        if not states:
            return result
        status = max(states, key=lambda s: (_time(s.get("created_at")), _id(s.get("id"))))
        if status.get("state") != "success":
            result["reason"] = "CUSTOMER_DEPLOYMENT_NOT_SUCCESSFUL"
            return result
        if (
            status.get("environment") != self.transport.environment
            or status.get("environment_url") not in {site_origin, site_origin + "/"}
            or _object(status.get("creator")).get("id") != trusted_deployment_actor_id
            or status.get("deployment_url")
            != f"{GITHUB_API_ORIGIN}{self.repo}/deployments/{deployment_id}"
            or _time(status.get("created_at")) < _time(latest.get("created_at"))
        ):
            result["reason"] = "EC_123_DEPLOYMENT_ORIGIN_MISMATCH"
            return result
        result.update(
            stage="deployed",
            reason="CORRELATED_CUSTOMER_DEPLOYMENT",
            deployment={
                "id": deployment_id,
                "status_id": _id(status.get("id")),
                "sha": merged_sha,
                "environment": self.transport.environment,
                "actor_id": trusted_deployment_actor_id,
                "environment_url": status["environment_url"],
                "created_at": latest["created_at"],
                "status_created_at": status["created_at"],
            },
        )
        return result

    def _pr(self, pr: object) -> None:
        if (
            not isinstance(pr, dict)
            or pr.get("number") != self.transport.pr_number
            or pr.get("html_url")
            != f"https://github.com/{self.target.owner}/{self.target.repository}/pull/{self.transport.pr_number}"
            or type(pr.get("merged")) is not bool
            or pr.get("state") not in ("open", "closed")
            or (pr.get("merged") and pr.get("state") != "closed")
            or _object(pr.get("head")).get("sha") != self.transport.head_sha
            or _object(_object(pr.get("head")).get("repo")).get("id") != self.repository_id
            or _object(pr.get("base")).get("ref") != self.target.base_branch
            or _object(_object(pr.get("base")).get("repo")).get("id") != self.repository_id
        ):
            raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_PR_DRIFT")

    def _checks(self, checks: object, statuses: object) -> dict:
        if (
            not isinstance(checks, dict)
            or not isinstance(checks.get("check_runs"), list)
            or type(checks.get("total_count")) is not int
            or not 0 <= checks["total_count"] <= 100
            or len(checks["check_runs"]) != checks["total_count"]
        ):
            raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_CHECKS_INCOMPLETE")
        if (
            not isinstance(statuses, dict)
            or statuses.get("sha") != self.transport.head_sha
            or not isinstance(statuses.get("statuses"), list)
            or type(statuses.get("total_count")) is not int
            or not 0 <= statuses["total_count"] <= 100
            or len(statuses["statuses"]) != statuses["total_count"]
        ):
            raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_CHECKS_INCOMPLETE")
        values = []
        for check in checks["check_runs"]:
            if (
                not isinstance(check, dict)
                or check.get("head_sha") != self.transport.head_sha
                or check.get("status") not in ("queued", "in_progress", "completed")
                or check.get("conclusion")
                not in (
                    None,
                    "success",
                    "failure",
                    "neutral",
                    "cancelled",
                    "skipped",
                    "timed_out",
                    "action_required",
                    "stale",
                    "startup_failure",
                )
            ):
                raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_CHECKS_REJECTED")
            values.append(
                {
                    "id": _id(check.get("id")),
                    "kind": "check_run",
                    "state": check["status"],
                    "conclusion": check.get("conclusion"),
                }
            )
        for status in statuses["statuses"]:
            if not isinstance(status, dict) or status.get("state") not in (
                "pending",
                "success",
                "error",
                "failure",
            ):
                raise GitHubDeliveryUnavailable("GITHUB_DELIVERY_CHECKS_REJECTED")
            values.append(
                {
                    "id": _id(status.get("id")),
                    "kind": "commit_status",
                    "state": status["state"],
                    "conclusion": None,
                }
            )
        passed = sum(
            (
                v["kind"] == "check_run"
                and v["state"] == "completed"
                and v["conclusion"] == "success"
            )
            or (v["kind"] == "commit_status" and v["state"] == "success")
            for v in values
        )
        return {
            "head_sha": self.transport.head_sha,
            "observed_count": len(values),
            "passed_count": passed,
            "records": values,
        }
