"""Least-privilege GitHub App repository inspection."""

import base64
import binascii
import hashlib
import json
import re
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx2
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import RSAKey

GITHUB_API_ORIGIN = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"

_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{20,4096}")
_SHA1 = re.compile(r"[0-9a-f]{40}")
_MAX_RESPONSE_BYTES = 256 * 1024
_MAX_PRIVATE_KEY_BYTES = 16 * 1024
_MAX_BRANCH_BYTES = 255
_MAX_CONTENT_PATH_BYTES = 1024
_READ_PERMISSIONS = {"contents": "read"}
_PR_PERMISSIONS = {"contents": "read", "pull_requests": "write"}


class GitHubAppProtocolError(Exception):
    """A GitHub App request or response failed one closed protocol check."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True)
class GitHubAppCredentials:
    app_id: int
    private_key_pem: str = field(repr=False)


@dataclass(frozen=True)
class GitHubRepositoryTarget:
    installation_id: int
    owner: str
    repository: str
    base_branch: str
    content_path: str


@dataclass(frozen=True)
class GitHubRepositorySnapshot:
    installation_id: int
    repository_id: int
    owner: str
    repository: str
    full_name: str
    private: bool
    default_branch: str
    base_branch: str
    base_sha: str
    protected: bool
    content_path: str
    credential_expires_at: datetime
    owner_accepted_unprotected: bool = False


@dataclass(frozen=True)
class GitHubTreeEntry:
    path: str
    mode: str
    kind: str
    sha: str


@dataclass(frozen=True)
class GitHubRepositoryInventory:
    snapshot: GitHubRepositorySnapshot
    tree_sha: str
    entries: tuple[GitHubTreeEntry, ...]
    truncated: bool


@dataclass(frozen=True)
class GitHubRepositoryCheckout:
    inventory: GitHubRepositoryInventory
    files: tuple[tuple[str, bytes], ...]


async def inspect_github_repository(
    *,
    credentials: object,
    target: object,
    transport: httpx2.AsyncBaseTransport | None = None,
    verify: ssl.SSLContext | bool = True,
    now: datetime | None = None,
) -> GitHubRepositorySnapshot:
    """Inspect one exact repository with a short-lived, read-only installation token."""
    snapshot, _, _ = await _inspect_repository(
        credentials=credentials,
        target=target,
        transport=transport,
        verify=verify,
        now=now,
        requested_permissions=_READ_PERMISSIONS,
        include_tree=False,
        include_blobs=False,
    )
    return snapshot


async def inspect_github_pr_authority(
    *,
    credentials: object,
    target: object,
    transport: httpx2.AsyncBaseTransport | None = None,
    verify: ssl.SSLContext | bool = True,
    now: datetime | None = None,
) -> GitHubRepositorySnapshot:
    """Observe one repository's PR permission without calling a write endpoint."""
    snapshot, _, _ = await _inspect_repository(
        credentials=credentials,
        target=target,
        transport=transport,
        verify=verify,
        now=now,
        requested_permissions=_PR_PERMISSIONS,
        include_tree=False,
        include_blobs=False,
    )
    return snapshot


async def inspect_github_installation_permissions(
    *,
    credentials: object,
    target: object,
    transport: httpx2.AsyncBaseTransport,
    verify: ssl.SSLContext | bool = True,
) -> str:
    """Fingerprint the actual App installation, not a reduced token's permissions."""
    app_id, key = _validated_credentials(credentials)
    selected = _validated_target(target)
    try:
        async with httpx2.AsyncClient(
            base_url=GITHUB_API_ORIGIN,
            timeout=httpx2.Timeout(5.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            verify=_trusted_tls_verifier(verify),
        ) as client:
            document = await _request_json(
                client,
                "GET",
                f"/app/installations/{selected.installation_id}",
                authorization=_app_jwt(app_id=app_id, key=key, now=_utc_now(None)),
            )
    except httpx2.HTTPError:
        raise GitHubAppProtocolError("GITHUB_PROVIDER_UNAVAILABLE", retryable=True) from None
    permissions = document.get("permissions") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("id") != selected.installation_id
        or document.get("app_id") != app_id
        or "suspended_at" not in document
        or document["suspended_at"] is not None
        or document.get("repository_selection") != "selected"
        or not isinstance(permissions, dict)
        or not 1 <= len(permissions) <= 128
        or any(
            not isinstance(k, str)
            or re.fullmatch(r"[a-z_]{1,80}", k) is None
            or not isinstance(v, str)
            or v not in {"read", "write", "admin"}
            for k, v in permissions.items()
        )
    ):
        raise GitHubAppProtocolError("GITHUB_SCOPE_REJECTED")
    return hashlib.sha256(
        json.dumps(permissions, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def inspect_github_repository_inventory(
    *,
    credentials: object,
    target: object,
    transport: httpx2.AsyncBaseTransport | None = None,
    verify: ssl.SSLContext | bool = True,
    now: datetime | None = None,
) -> GitHubRepositoryInventory:
    """Read one exact base tree with a contents-read token and no file bodies."""
    _, inventory, _ = await _inspect_repository(
        credentials=credentials,
        target=target,
        transport=transport,
        verify=verify,
        now=now,
        requested_permissions=_READ_PERMISSIONS,
        include_tree=True,
        include_blobs=False,
    )
    if inventory is None:
        raise RuntimeError("Repository inventory was not returned.")
    return inventory


async def checkout_github_repository(
    *,
    credentials: object,
    target: object,
    transport: httpx2.AsyncBaseTransport | None = None,
    verify: ssl.SSLContext | bool = True,
    now: datetime | None = None,
) -> GitHubRepositoryCheckout:
    """Read a bounded exact commit as verified regular blobs, never as a host clone."""
    _, inventory, files = await _inspect_repository(
        credentials=credentials,
        target=target,
        transport=transport,
        verify=verify,
        now=now,
        requested_permissions=_READ_PERMISSIONS,
        include_tree=True,
        include_blobs=True,
    )
    if inventory is None or files is None:
        raise RuntimeError("Repository checkout was not returned.")
    return GitHubRepositoryCheckout(inventory, files)


async def _inspect_repository(
    *,
    credentials: object,
    target: object,
    transport: httpx2.AsyncBaseTransport | None,
    verify: ssl.SSLContext | bool,
    now: datetime | None,
    requested_permissions: dict[str, str],
    include_tree: bool,
    include_blobs: bool,
) -> tuple[
    GitHubRepositorySnapshot,
    GitHubRepositoryInventory | None,
    tuple[tuple[str, bytes], ...] | None,
]:
    app_id, key = _validated_credentials(credentials)
    selected = _validated_target(target)
    verifier = _trusted_tls_verifier(verify)
    observed_at = _utc_now(now)
    app_token = _app_jwt(app_id=app_id, key=key, now=observed_at)
    try:
        async with httpx2.AsyncClient(
            base_url=GITHUB_API_ORIGIN,
            timeout=httpx2.Timeout(5.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            verify=verifier,
        ) as client:
            installation = await _request_json(
                client,
                "POST",
                f"/app/installations/{selected.installation_id}/access_tokens",
                authorization=app_token,
                body={
                    "repositories": [selected.repository],
                    "permissions": requested_permissions,
                },
            )
            token, expires_at, scoped_repository_id = _installation_token(
                installation,
                selected,
                observed_at,
                requested_permissions,
            )
            repository = await _request_json(
                client,
                "GET",
                f"/repos/{quote(selected.owner, safe='')}/{quote(selected.repository, safe='')}",
                authorization=token,
            )
            repository_id, full_name, private, default_branch = _repository(
                repository,
                selected,
                scoped_repository_id,
            )
            branch = await _request_json(
                client,
                "GET",
                f"/repos/{quote(selected.owner, safe='')}/{quote(selected.repository, safe='')}"
                f"/branches/{quote(selected.base_branch, safe='')}",
                authorization=token,
            )
            base_sha, protected = _branch(branch, selected)
            if include_tree:
                repo_path = (
                    f"/repos/{quote(selected.owner, safe='')}/{quote(selected.repository, safe='')}"
                )
                commit = await _request_json(
                    client, "GET", f"{repo_path}/git/commits/{base_sha}", authorization=token
                )
                tree_sha = _commit_tree_sha(commit, base_sha)
                tree = await _request_json(
                    client,
                    "GET",
                    f"{repo_path}/git/trees/{tree_sha}?recursive=1",
                    authorization=token,
                )
                entries, truncated = _tree_entries(tree, tree_sha)
                if include_blobs:
                    if truncated or len(entries) > 256:
                        raise GitHubAppProtocolError("GITHUB_REPOSITORY_SIZE_REJECTED")
                    blobs = [entry for entry in entries if entry.kind == "blob"]
                    if any(
                        entry.kind == "commit" or (entry.kind == "blob" and entry.mode != "100644")
                        for entry in entries
                    ):
                        raise GitHubAppProtocolError("GITHUB_REPOSITORY_TYPE_REJECTED")
                    total = 0
                    files = []
                    for entry in blobs:
                        blob = await _request_json(
                            client,
                            "GET",
                            f"{repo_path}/git/blobs/{entry.sha}",
                            authorization=token,
                        )
                        content = _blob_bytes(blob, entry.sha)
                        total += len(content)
                        if total > 8 * 1024 * 1024:
                            raise GitHubAppProtocolError("GITHUB_REPOSITORY_SIZE_REJECTED")
                        files.append((entry.path, content))
    except GitHubAppProtocolError:
        raise
    except httpx2.HTTPError:
        raise GitHubAppProtocolError("GITHUB_PROVIDER_UNAVAILABLE", retryable=True) from None

    canonical_owner, canonical_repository = full_name.split("/", 1)
    snapshot = GitHubRepositorySnapshot(
        installation_id=selected.installation_id,
        repository_id=repository_id,
        owner=canonical_owner,
        repository=canonical_repository,
        full_name=full_name,
        private=private,
        default_branch=default_branch,
        base_branch=selected.base_branch,
        base_sha=base_sha,
        protected=protected,
        content_path=selected.content_path,
        credential_expires_at=expires_at,
    )
    inventory = (
        GitHubRepositoryInventory(snapshot, tree_sha, entries, truncated) if include_tree else None
    )
    return snapshot, inventory, tuple(files) if include_blobs else None


def _blob_bytes(document: object, expected_sha: str) -> bytes:
    if not isinstance(document, dict) or document.get("sha") != expected_sha:
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    size = document.get("size")
    encoded = document.get("content")
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or not 0 <= size <= 128 * 1024
        or document.get("encoding") != "base64"
        or not isinstance(encoded, str)
    ):
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    try:
        content = base64.b64decode(encoded.replace("\n", ""), validate=True)
    except (binascii.Error, ValueError):
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED") from None
    if (
        len(content) != size
        or hashlib.sha1(f"blob {size}\0".encode("ascii") + content).hexdigest() != expected_sha
    ):
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    return content


def _commit_tree_sha(document: object, expected_commit_sha: str) -> str:
    if not isinstance(document, dict) or document.get("sha") != expected_commit_sha:
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    tree = document.get("tree")
    if not isinstance(tree, dict) or not isinstance(tree.get("sha"), str):
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    tree_sha = tree["sha"]
    if _SHA1.fullmatch(tree_sha) is None:
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    return tree_sha


def _tree_entries(
    document: object, expected_tree_sha: str
) -> tuple[tuple[GitHubTreeEntry, ...], bool]:
    if (
        not isinstance(document, dict)
        or document.get("sha") != expected_tree_sha
        or not isinstance(document.get("truncated"), bool)
        or not isinstance(document.get("tree"), list)
        or len(document["tree"]) > 5000
    ):
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    entries = []
    seen = set()
    for item in document["tree"]:
        if not isinstance(item, dict):
            raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
        path = item.get("path")
        mode = item.get("mode")
        kind = item.get("type")
        sha = item.get("sha")
        try:
            path_bytes = path.encode("utf-8") if isinstance(path, str) else b""
        except UnicodeError:
            raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED") from None
        if (
            not isinstance(path, str)
            or not 1 <= len(path_bytes) <= 1024
            or path.startswith("/")
            or "\\" in path
            or any(segment in {"", ".", ".."} for segment in path.split("/"))
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
            or path in seen
            or kind not in {"blob", "tree", "commit"}
            or (kind == "blob" and mode not in {"100644", "100755", "120000"})
            or (kind == "tree" and mode != "040000")
            or (kind == "commit" and mode != "160000")
            or not isinstance(sha, str)
            or _SHA1.fullmatch(sha) is None
        ):
            raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
        seen.add(path)
        entries.append(GitHubTreeEntry(path, mode, kind, sha))
    return tuple(entries), document["truncated"]


async def _request_json(
    client: httpx2.AsyncClient,
    method: str,
    path: str,
    *,
    authorization: str,
    body: dict[str, object] | None = None,
) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {authorization}",
        "User-Agent": "Signal-SEO-Agent/0.0.0",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    async with client.stream(method, path, headers=headers, json=body) as response:
        if response.status_code in {401, 403, 404}:
            raise GitHubAppProtocolError("GITHUB_AUTHORIZATION_REJECTED")
        if response.status_code == 409:
            raise GitHubAppProtocolError("GITHUB_REPOSITORY_STATE_REJECTED")
        if response.status_code == 422:
            raise GitHubAppProtocolError("GITHUB_SCOPE_REJECTED")
        if response.status_code == 429 or response.status_code >= 500:
            raise GitHubAppProtocolError("GITHUB_PROVIDER_UNAVAILABLE", retryable=True)
        if response.status_code not in {200, 201}:
            raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
        payload = await _bounded_json(response)
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED") from None


async def _bounded_json(response: httpx2.Response) -> bytes:
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "application/vnd.github+json"}:
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) < 0 or int(declared) > _MAX_RESPONSE_BYTES:
                raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
        except ValueError:
            raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED") from None
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > _MAX_RESPONSE_BYTES:
            raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    return bytes(content)


def _installation_token(
    document: object,
    target: GitHubRepositoryTarget,
    now: datetime,
    requested_permissions: dict[str, str],
) -> tuple[str, datetime, int]:
    if not isinstance(document, dict):
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    token = document.get("token")
    expires_at = _timestamp(document.get("expires_at"))
    permissions = document.get("permissions")
    repositories = document.get("repositories")
    selection = document.get("repository_selection")
    if (
        not isinstance(token, str)
        or _TOKEN.fullmatch(token) is None
        or expires_at <= now + timedelta(minutes=1)
        or expires_at > now + timedelta(hours=2)
        or selection != "selected"
        or not isinstance(permissions, dict)
        or set(permissions) - (set(requested_permissions) | {"metadata"})
        or any(permissions.get(key) != value for key, value in requested_permissions.items())
        or ("metadata" in permissions and permissions["metadata"] != "read")
        or not isinstance(repositories, list)
        or len(repositories) != 1
    ):
        raise GitHubAppProtocolError("GITHUB_SCOPE_REJECTED")
    repository = repositories[0]
    if not isinstance(repository, dict):
        raise GitHubAppProtocolError("GITHUB_SCOPE_REJECTED")
    repository_id = repository.get("id")
    full_name = repository.get("full_name")
    expected = f"{target.owner}/{target.repository}".casefold()
    if (
        not isinstance(repository_id, int)
        or isinstance(repository_id, bool)
        or repository_id <= 0
        or not isinstance(full_name, str)
        or full_name.casefold() != expected
    ):
        raise GitHubAppProtocolError("GITHUB_SCOPE_REJECTED")
    return token, expires_at, repository_id


def _repository(
    document: object,
    target: GitHubRepositoryTarget,
    scoped_repository_id: int,
) -> tuple[int, str, bool, str]:
    if not isinstance(document, dict):
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    repository_id = document.get("id")
    full_name = document.get("full_name")
    private = document.get("private")
    default_branch = document.get("default_branch")
    archived = document.get("archived")
    disabled = document.get("disabled")
    expected = f"{target.owner}/{target.repository}".casefold()
    if (
        repository_id != scoped_repository_id
        or isinstance(repository_id, bool)
        or not isinstance(full_name, str)
        or full_name.casefold() != expected
        or not isinstance(private, bool)
        or not _valid_branch(default_branch)
        or not isinstance(archived, bool)
        or not isinstance(disabled, bool)
    ):
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    if archived or disabled:
        raise GitHubAppProtocolError("GITHUB_REPOSITORY_STATE_REJECTED")
    return repository_id, full_name, private, default_branch


def _branch(document: object, target: GitHubRepositoryTarget) -> tuple[str, bool]:
    if not isinstance(document, dict):
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    name = document.get("name")
    commit = document.get("commit")
    protected = document.get("protected")
    if (
        name != target.base_branch
        or not isinstance(commit, dict)
        or _SHA1.fullmatch(str(commit.get("sha", ""))) is None
        or not isinstance(protected, bool)
    ):
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    return str(commit["sha"]), protected


def _validated_credentials(value: object) -> tuple[int, RSAKey]:
    if not isinstance(value, GitHubAppCredentials):
        raise GitHubAppProtocolError("GITHUB_CREDENTIALS_REJECTED")
    if (
        not isinstance(value.app_id, int)
        or isinstance(value.app_id, bool)
        or value.app_id <= 0
        or value.app_id > 2**63 - 1
        or not isinstance(value.private_key_pem, str)
        or not 1 <= len(value.private_key_pem.encode("utf-8")) <= _MAX_PRIVATE_KEY_BYTES
    ):
        raise GitHubAppProtocolError("GITHUB_CREDENTIALS_REJECTED")
    try:
        key = RSAKey.import_key(
            value.private_key_pem,
            parameters={"alg": "RS256", "use": "sig"},
        )
    except (JoseError, TypeError, ValueError):
        raise GitHubAppProtocolError("GITHUB_CREDENTIALS_REJECTED") from None
    if not key.is_private:
        raise GitHubAppProtocolError("GITHUB_CREDENTIALS_REJECTED")
    return value.app_id, key


def _validated_target(value: object) -> GitHubRepositoryTarget:
    if (
        not isinstance(value, GitHubRepositoryTarget)
        or not isinstance(value.installation_id, int)
        or isinstance(value.installation_id, bool)
        or value.installation_id <= 0
        or value.installation_id > 2**63 - 1
        or _OWNER.fullmatch(value.owner) is None
        or _REPOSITORY.fullmatch(value.repository) is None
        or not _valid_branch(value.base_branch)
        or not _valid_content_path(value.content_path)
    ):
        raise GitHubAppProtocolError("GITHUB_TARGET_REJECTED")
    return value


def _valid_branch(value: object) -> bool:
    if not isinstance(value, str):
        return False
    encoded = value.encode("utf-8")
    return not (
        not 1 <= len(encoded) <= _MAX_BRANCH_BYTES
        or value.startswith(("/", "."))
        or value.endswith(("/", ".", ".lock"))
        or "//" in value
        or ".." in value
        or "@{" in value
        or any(character in value for character in " ~^:?*[\\\x00\x7f")
    )


def _valid_content_path(value: object) -> bool:
    if not isinstance(value, str) or not value.isascii():
        return False
    encoded = value.encode("ascii")
    segments = value.split("/")
    return (
        1 <= len(encoded) <= _MAX_CONTENT_PATH_BYTES
        and not value.startswith(("/", "."))
        and not value.endswith("/")
        and all(segment not in {"", ".", ".."} for segment in segments)
        and all(not segment.startswith(".") for segment in segments)
        and all(all(32 < ord(character) < 127 for character in segment) for segment in segments)
    )


def _app_jwt(*, app_id: int, key: RSAKey, now: datetime) -> str:
    try:
        return jwt.encode(
            {"alg": "RS256", "typ": "JWT"},
            {
                "iat": int((now - timedelta(seconds=60)).timestamp()),
                "exp": int((now + timedelta(minutes=9)).timestamp()),
                "iss": str(app_id),
            },
            key,
            algorithms=["RS256"],
        )
    except JoseError:
        raise GitHubAppProtocolError("GITHUB_CREDENTIALS_REJECTED") from None


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED") from None
    if parsed.tzinfo is None:
        raise GitHubAppProtocolError("GITHUB_PROVIDER_RESPONSE_REJECTED")
    return parsed.astimezone(UTC)


def _utc_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise GitHubAppProtocolError("GITHUB_TIME_REJECTED")
    return value.astimezone(UTC)


def _trusted_tls_verifier(value: ssl.SSLContext | bool) -> ssl.SSLContext | bool:
    if value is not True and not isinstance(value, ssl.SSLContext):
        raise GitHubAppProtocolError("GITHUB_TLS_CONFIGURATION_REJECTED")
    return value
