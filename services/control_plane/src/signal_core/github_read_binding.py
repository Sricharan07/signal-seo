"""Owner-attributed durable GitHub read binding and current-provider checks."""

import hashlib
import re
import ssl
import time
from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

import httpx2
from anyio import sleep, to_thread
from psycopg import Connection

from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.connector_framework import require_outcome
from signal_core.connector_secrets import kv_document
from signal_core.database import _clean_transaction
from signal_core.egress_profiles import EgressProfile
from signal_core.github_app import (
    GITHUB_API_VERSION,
    GitHubAppCredentials,
    GitHubAppProtocolError,
    GitHubRepositorySnapshot,
    GitHubRepositoryTarget,
    _validated_target,
    inspect_github_installation_permissions,
    inspect_github_repository,
)
from signal_core.openbao_http import (
    OpenBaoDocumentError,
    OpenBaoTlsConfigurationError,
    OpenBaoTransportError,
    request,
    valid_base_url,
    valid_mount,
    valid_token,
)
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import session_token_hasher
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider

_GITHUB_READ_PATH = re.compile(
    r"(?:/app/installations/[1-9][0-9]*(?:/access_tokens)?"
    r"|/repos/[^/]+/[^/]+(?:/branches/[^?#]+"
    r"|/git/commits/[0-9a-f]{40}|/git/trees/[0-9a-f]{40}"
    r"|/git/blobs/[0-9a-f]{40})?)"
)


class GitHubBindingConflict(Exception):
    """The selected repository conflicts with a current durable intent."""


class GitHubBindingUnavailable(Exception):
    """The selected repository is not currently usable under read authority."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GitHubSharedEgressTransport(httpx2.AsyncBaseTransport):
    """Route the exact App inspection endpoints through durable connector egress."""

    def __init__(self, provider: SharedEgressProvider) -> None:
        if not isinstance(provider, SharedEgressProvider) or provider.purpose != "connector":
            raise ValueError("GitHub read transport requires connector shared egress.")
        if (
            provider.policy.allowed_origins != ("https://api.github.com",)
            or provider.policy.user_agent != "SignalBot/1.0 (+https://signal.example/bot)"
            or provider.policy.max_redirects != 0
            or provider.policy.max_body_bytes > 256 * 1024
            or provider.policy.request_timeout_seconds > 5
        ):
            raise ValueError("GitHub connector egress policy is not exact and bounded.")
        self.provider = provider

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if (
            request.url.scheme != "https"
            or request.url.host != "api.github.com"
            or request.url.port not in {None, 443}
            or bool(request.url.username)
            or bool(request.url.password)
            or request.url.fragment
            or (
                request.url.query != b"recursive=1"
                if "/git/trees/" in path
                else bool(request.url.query)
            )
            or request.method not in {"GET", "POST"}
            or _GITHUB_READ_PATH.fullmatch(path) is None
            or (request.method == "POST") != path.endswith("/access_tokens")
            or request.headers.get("x-github-api-version") != GITHUB_API_VERSION
            or request.headers.get("user-agent") != "Signal-SEO-Agent/0.0.0"
            or request.headers.get("accept") != "application/vnd.github+json"
            or not request.headers.get("authorization", "").startswith("Bearer ")
        ):
            raise httpx2.ConnectError("GitHub connector request rejected.", request=request)
        if request.method == "POST":
            if request.headers.get("content-type") != "application/json":
                raise httpx2.ConnectError("GitHub connector request rejected.", request=request)
        operation_id = uuid4()
        deadline = time.monotonic() + 5
        while True:
            try:
                result = await to_thread.run_sync(
                    lambda: self.provider.request_json(
                        method=request.method,
                        url=str(request.url),
                        profile=EgressProfile.GITHUB_REST,
                        authorization=request.headers["authorization"],
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
                    "GitHub connector egress unavailable.", request=request
                ) from None
        return httpx2.Response(
            result.status_code,
            headers={"content-type": result.media_type},
            content=result.body,
            request=request,
        )


@dataclass(frozen=True)
class OpenBaoGitHubAppCredential:
    """Read the single GitHub App key from a fixed OpenBao KV v2 path."""

    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-github"

    def __post_init__(self) -> None:
        if not valid_base_url(self.base_url) or not valid_token(self.token):
            raise ValueError("GitHub credential store configuration is invalid.")
        if not valid_mount(self.mount):
            raise ValueError("GitHub credential mount is invalid.")

    async def credentials(
        self,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> GitHubAppCredentials:
        try:
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method="GET",
                path=f"/{self.mount}/data/github/app",
                transport=transport,
                verify=verify,
            )
        except (OpenBaoTlsConfigurationError, OpenBaoTransportError):
            raise GitHubBindingUnavailable("GITHUB_CREDENTIAL_UNAVAILABLE") from None
        if response.status_code != 200:
            raise GitHubBindingUnavailable("GITHUB_CREDENTIAL_UNAVAILABLE")
        try:
            data, _ = kv_document(response, minimum_version=1)
        except (OpenBaoDocumentError, ValueError):
            raise GitHubBindingUnavailable("GITHUB_CREDENTIAL_UNAVAILABLE") from None
        if (
            set(data) != {"app_id", "private_key_pem"}
            or not isinstance(data.get("app_id"), int)
            or isinstance(data.get("app_id"), bool)
            or not 0 < data["app_id"] < 2**63
            or not isinstance(data.get("private_key_pem"), str)
        ):
            raise GitHubBindingUnavailable("GITHUB_CREDENTIAL_UNAVAILABLE")
        return GitHubAppCredentials(data["app_id"], data["private_key_pem"])


@dataclass(frozen=True)
class GitHubReadBinding:
    id: UUID
    site_id: UUID
    status: str
    target: GitHubRepositoryTarget
    repository_id: int | None = None
    observed_full_name: str | None = None
    base_sha: str | None = None
    replayed: bool = False
    protected: bool | None = None
    default_branch: str | None = None
    risk_monitored: bool = False
    owner_accepted_unprotected: bool = False


def prepare_github_read_binding(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    idempotency_key: UUID,
    target: GitHubRepositoryTarget,
) -> GitHubReadBinding:
    _validated_target(target)
    if not isinstance(site_id, UUID) or not isinstance(idempotency_key, UUID):
        raise ValueError("GitHub binding identities are invalid.")
    generation = validate_recovery_generation(current_recovery_generation)
    token_hash = _token_hash(session_token)
    payload = "\n".join(
        (
            str(site_id),
            str(target.installation_id),
            target.owner,
            target.repository,
            target.base_branch,
            target.content_path,
        )
    )
    request_hash = hashlib.sha256(payload.encode("utf-8")).digest()
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT binding_id, binding_status, replayed, outcome "
            "FROM control.prepare_github_read_binding(%s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s)",
            (
                token_hash,
                site_id,
                generation,
                uuid4(),
                uuid4(),
                idempotency_key,
                request_hash,
                target.installation_id,
                target.owner,
                target.repository,
                target.base_branch,
                target.content_path,
            ),
        ).fetchone()
        _require_outcome(row[3], success="prepared")
    return GitHubReadBinding(row[0], site_id, row[1], target, replayed=row[2])


def finish_github_read_binding(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    prepared: GitHubReadBinding,
    snapshot: GitHubRepositorySnapshot | None = None,
    failure_code: str | None = None,
) -> str:
    if not isinstance(prepared, GitHubReadBinding) or prepared.status != "prepared":
        raise GitHubBindingConflict()
    if (snapshot is None) == (failure_code is None):
        raise ValueError("Exactly one GitHub binding observation is required.")
    if snapshot is not None and (
        snapshot.installation_id != prepared.target.installation_id
        or snapshot.full_name.casefold()
        != f"{prepared.target.owner}/{prepared.target.repository}".casefold()
        or snapshot.base_branch != prepared.target.base_branch
        or snapshot.content_path != prepared.target.content_path
        or not snapshot.protected
    ):
        raise GitHubBindingConflict()
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control.finish_github_read_binding(%s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s)",
            (
                _token_hash(session_token),
                prepared.site_id,
                validate_recovery_generation(current_recovery_generation),
                prepared.id,
                uuid4(),
                "observed" if snapshot is not None else failure_code,
                snapshot.repository_id if snapshot else None,
                snapshot.full_name if snapshot else None,
                snapshot.default_branch if snapshot else None,
                snapshot.base_sha if snapshot else None,
                snapshot.private if snapshot else None,
                snapshot.protected if snapshot else None,
            ),
        ).fetchone()[0]
        _require_outcome(outcome, success="active" if snapshot else "failed")
    return outcome


def read_github_read_binding(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    binding_id: UUID,
) -> GitHubReadBinding:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.read_github_read_binding(%s, %s, %s, %s)",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(current_recovery_generation),
                binding_id,
            ),
        ).fetchone()
        _require_outcome(row[-1], success="found")
    with _clean_transaction(connection):
        risk = connection.execute(
            "SELECT control.read_github_base_risk(%s,%s,%s,%s)",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(current_recovery_generation),
                binding_id,
            ),
        ).fetchone()[0]
    if not isinstance(risk, dict):
        raise GitHubBindingUnavailable("GITHUB_BINDING_UNAVAILABLE")
    return GitHubReadBinding(
        id=row[0],
        site_id=site_id,
        status=row[1],
        target=GitHubRepositoryTarget(row[2], row[3], row[4], row[5], row[6]),
        repository_id=row[7],
        observed_full_name=row[8],
        base_sha=row[9],
        protected=risk["protected"],
        default_branch=risk["default_branch"],
        risk_monitored=risk["risk_monitored"],
        owner_accepted_unprotected=risk["owner_accepted"],
    )


def record_github_binding_state(
    connection,
    *,
    session_token,
    current_recovery_generation,
    binding,
    snapshot=None,
    permissions_sha256=None,
):
    """Persist drift before reporting denial so restoration cannot revive acceptance."""
    with _clean_transaction(connection):
        return connection.execute(
            "SELECT control.record_github_binding_state(" + ",".join(["%s"] * 11) + ")",
            (
                _token_hash(session_token),
                binding.site_id,
                validate_recovery_generation(current_recovery_generation),
                binding.id,
                snapshot.repository_id if snapshot else None,
                snapshot.full_name if snapshot else None,
                snapshot.installation_id if snapshot else None,
                snapshot.default_branch if snapshot else None,
                snapshot.base_branch if snapshot else None,
                snapshot.protected if snapshot else None,
                bytes.fromhex(permissions_sha256) if permissions_sha256 else None,
            ),
        ).fetchone()[0]


async def check_github_binding_snapshot(
    connection,
    *,
    session_token,
    current_recovery_generation,
    binding,
    snapshot,
    credentials,
    github_transport,
):
    permissions = None
    try:
        if binding.risk_monitored:
            permissions = await inspect_github_installation_permissions(
                credentials=credentials,
                target=binding.target,
                transport=github_transport,
            )
    except GitHubAppProtocolError:
        record_github_binding_state(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            binding=binding,
        )
        raise
    allowed = record_github_binding_state(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        binding=binding,
        snapshot=snapshot,
        permissions_sha256=permissions,
    )
    if not allowed:
        raise GitHubBindingUnavailable("GITHUB_BINDING_CHANGED")
    return replace(snapshot, owner_accepted_unprotected=not snapshot.protected)


async def accept_github_unprotected_base(
    connection,
    *,
    session_token,
    current_recovery_generation,
    site_id,
    binding_id,
    credential,
    github_transport,
    openbao_transport=None,
    openbao_verify=True,
):
    """Explicit dashboard command; provider data is never accepted from the browser."""
    if not isinstance(github_transport, GitHubSharedEgressTransport):
        raise GitHubBindingUnavailable("GITHUB_EGRESS_UNAVAILABLE")
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control.github_base_acceptance_preflight(%s,%s,%s,%s)",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(current_recovery_generation),
                binding_id,
            ),
        ).fetchone()[0]
        _require_outcome(outcome, success="authorized")
    binding = read_github_read_binding(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        binding_id=binding_id,
    )
    if binding.status not in {"prepared", "failed", "active"}:
        raise GitHubBindingUnavailable("GITHUB_BINDING_INACTIVE")
    credentials = await credential.credentials(transport=openbao_transport, verify=openbao_verify)
    snapshot = await inspect_github_repository(
        credentials=credentials, target=binding.target, transport=github_transport
    )
    if snapshot.protected or snapshot.default_branch != binding.target.base_branch:
        raise GitHubBindingUnavailable("GITHUB_REPOSITORY_STATE_REJECTED")
    permissions = await inspect_github_installation_permissions(
        credentials=credentials, target=binding.target, transport=github_transport
    )
    if binding.status == "active":
        record_github_binding_state(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            binding=binding,
            snapshot=snapshot,
            permissions_sha256=permissions,
        )
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control.accept_github_unprotected_base(" + ",".join(["%s"] * 12) + ")",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(current_recovery_generation),
                binding_id,
                uuid4(),
                snapshot.repository_id,
                snapshot.full_name,
                snapshot.default_branch,
                snapshot.base_sha,
                snapshot.private,
                snapshot.protected,
                bytes.fromhex(permissions),
            ),
        ).fetchone()[0]
        _require_outcome(outcome, success="active")
    return read_github_read_binding(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        binding_id=binding_id,
    )


def revoke_github_read_binding(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    binding_id: UUID,
) -> None:
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control.revoke_github_read_binding(%s, %s, %s, %s, %s)",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(current_recovery_generation),
                binding_id,
                uuid4(),
            ),
        ).fetchone()[0]
        _require_outcome(outcome, success="revoked")


async def inspect_current_github_read_binding(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    binding_id: UUID,
    credential: OpenBaoGitHubAppCredential,
    github_transport: GitHubSharedEgressTransport | None = None,
    openbao_transport: httpx2.AsyncBaseTransport | None = None,
    openbao_verify: ssl.SSLContext | bool = True,
) -> GitHubRepositorySnapshot:
    if not isinstance(github_transport, GitHubSharedEgressTransport):
        raise GitHubBindingUnavailable("GITHUB_EGRESS_UNAVAILABLE")
    before = read_github_read_binding(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        binding_id=binding_id,
    )
    if before.status != "active":
        raise GitHubBindingUnavailable("GITHUB_BINDING_INACTIVE")
    credentials = await credential.credentials(transport=openbao_transport, verify=openbao_verify)
    try:
        snapshot = await inspect_github_repository(
            credentials=credentials,
            target=before.target,
            transport=github_transport,
        )
    except GitHubAppProtocolError as error:
        record_github_binding_state(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            binding=before,
        )
        raise GitHubBindingUnavailable(error.code) from None
    snapshot = await check_github_binding_snapshot(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        binding=before,
        snapshot=snapshot,
        credentials=credentials,
        github_transport=github_transport,
    )
    after = read_github_read_binding(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        binding_id=binding_id,
    )
    if (
        after.status != "active"
        or after.repository_id != before.repository_id
        or snapshot.repository_id != before.repository_id
        or snapshot.full_name != before.observed_full_name
        or not (snapshot.protected or snapshot.owner_accepted_unprotected)
    ):
        raise GitHubBindingUnavailable("GITHUB_BINDING_CHANGED")
    return snapshot


async def bind_github_read_repository(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    idempotency_key: UUID,
    target: GitHubRepositoryTarget,
    credential: OpenBaoGitHubAppCredential,
    github_transport: GitHubSharedEgressTransport | None = None,
    openbao_transport: httpx2.AsyncBaseTransport | None = None,
    openbao_verify: ssl.SSLContext | bool = True,
) -> GitHubReadBinding:
    if not isinstance(github_transport, GitHubSharedEgressTransport):
        raise GitHubBindingUnavailable("GITHUB_EGRESS_UNAVAILABLE")
    prepared = prepare_github_read_binding(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        idempotency_key=idempotency_key,
        target=target,
    )
    if prepared.status != "prepared":
        return read_github_read_binding(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            site_id=site_id,
            binding_id=prepared.id,
        )
    try:
        credentials = await credential.credentials(
            transport=openbao_transport, verify=openbao_verify
        )
        snapshot = await inspect_github_repository(
            credentials=credentials,
            target=target,
            transport=github_transport,
        )
        if not snapshot.protected:
            raise GitHubAppProtocolError("GITHUB_REPOSITORY_STATE_REJECTED")
    except (GitHubAppProtocolError, GitHubBindingUnavailable) as error:
        finish_github_read_binding(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            prepared=prepared,
            failure_code=error.code,
        )
        raise GitHubBindingUnavailable(error.code) from None
    finish_github_read_binding(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        prepared=prepared,
        snapshot=snapshot,
    )
    return read_github_read_binding(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        binding_id=prepared.id,
    )


_token_hash = session_token_hasher(InvalidSession)


def _require_outcome(outcome: str, *, success: str) -> None:
    require_outcome(
        outcome,
        success,
        {
            "invalid_session": InvalidSession(),
            "step_up_required": GitHubBindingUnavailable("GITHUB_STEP_UP_REQUIRED"),
            **dict.fromkeys(
                (
                    "authorization_denied",
                    "permission_denied",
                    "site_not_verified",
                    "binding_not_authorized",
                ),
                AuthorizationDenied(),
            ),
            **dict.fromkeys(
                (
                    "request_conflict",
                    "binding_exists",
                    "binding_not_prepared",
                    "invalid_observation",
                ),
                GitHubBindingConflict(),
            ),
        },
        GitHubBindingUnavailable("GITHUB_BINDING_UNAVAILABLE"),
    )
