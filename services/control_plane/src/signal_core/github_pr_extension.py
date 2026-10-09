"""Owner-attributed PR capability observation without repository writes."""

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx2
from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.connector_framework import require_outcome
from signal_core.database import _clean_transaction
from signal_core.github_app import (
    GitHubAppProtocolError,
    GitHubRepositorySnapshot,
    checkout_github_repository,
    inspect_github_pr_authority,
    inspect_github_repository_inventory,
)
from signal_core.github_format import RepositoryFormatAssessment, detect_repository_format
from signal_core.github_read_binding import (
    GitHubBindingUnavailable,
    GitHubReadBinding,
    GitHubSharedEgressTransport,
    OpenBaoGitHubAppCredential,
    check_github_binding_snapshot,
    read_github_read_binding,
    record_github_binding_state,
)
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import session_token_hasher


class GitHubPrExtensionConflict(Exception):
    """The PR capability request conflicts with committed or current authority."""


class GitHubPrExtensionUnavailable(Exception):
    """The narrow PR capability cannot currently be observed or exercised."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GitHubPrExtension:
    id: UUID
    site_id: UUID
    binding_id: UUID
    status: str
    repository_id: int | None = None
    base_sha: str | None = None
    tree_sha: str | None = None
    framework: str | None = None
    content_format: str | None = None
    coverage: str | None = None
    content_sha: str | None = None
    marker_evidence: tuple[tuple[str, str], ...] = ()
    replayed: bool = False
    owner_accepted_unprotected: bool = False
    accepted_default_branch: str | None = None

    @property
    def build_unavailable_reason(self) -> str | None:
        if self.framework in {"hugo", "jekyll"}:
            return "build verification unavailable: no pinned offline toolchain"
        return None

    @property
    def candidate_compatible(self) -> bool:
        return (
            self.status == "observed"
            and self.coverage == "complete"
            and self.framework in {"nextjs", "astro", "hugo", "eleventy"}
            and self.content_format not in {None, "unknown"}
            and self.content_sha is not None
        )


def prepare_github_pr_extension(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    binding_id: UUID,
    idempotency_key: UUID,
    owner_flow: bool = False,
) -> GitHubPrExtension:
    if not all(isinstance(value, UUID) for value in (site_id, binding_id, idempotency_key)):
        raise ValueError("GitHub PR extension identities are invalid.")
    digest = hashlib.sha256(f"{site_id}\n{binding_id}".encode("ascii")).digest()
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT extension_id, extension_status, replayed, outcome "
            "FROM control."
            + ("prepare_owner_github_pr_extension" if owner_flow else "prepare_github_pr_extension")
            + "(%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(current_recovery_generation),
                binding_id,
                uuid4(),
                uuid4(),
                idempotency_key,
                digest,
            ),
        ).fetchone()
        _require_outcome(row[3], success="prepared")
    return GitHubPrExtension(row[0], site_id, binding_id, row[1], replayed=row[2])


def finish_github_pr_extension(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    prepared: GitHubPrExtension,
    repository_id: int | None = None,
    snapshot: GitHubRepositorySnapshot | None = None,
    assessment: RepositoryFormatAssessment | None = None,
    failure_code: str | None = None,
    owner_flow: bool = False,
) -> None:
    if not isinstance(prepared, GitHubPrExtension) or prepared.status != "prepared":
        raise GitHubPrExtensionConflict()
    if (assessment is None) == (failure_code is None):
        raise ValueError("Exactly one PR capability result is required.")
    if assessment is not None and (
        not isinstance(snapshot, GitHubRepositorySnapshot)
        or snapshot.repository_id != repository_id
        or not (snapshot.protected or snapshot.owner_accepted_unprotected)
        or snapshot.base_sha != assessment.base_sha
    ):
        raise GitHubPrExtensionConflict()
    markers = (
        [{"path": entry.path, "sha": entry.sha} for entry in assessment.marker_entries]
        if assessment is not None
        else None
    )
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control."
            + ("finish_owner_github_pr_extension" if owner_flow else "finish_github_pr_extension")
            + "("
            + ", ".join(["%s"] * 15)
            + ")",
            (
                _token_hash(session_token),
                prepared.site_id,
                validate_recovery_generation(current_recovery_generation),
                prepared.id,
                uuid4(),
                "observed" if assessment is not None else failure_code,
                repository_id if assessment is not None else None,
                assessment.base_sha if assessment else None,
                assessment.tree_sha if assessment else None,
                assessment.framework if assessment else None,
                assessment.content_format if assessment else None,
                assessment.coverage if assessment else None,
                assessment.content_entry.sha if assessment and assessment.content_entry else None,
                Jsonb(markers) if markers is not None else None,
                snapshot.protected if snapshot else None,
            ),
        ).fetchone()[0]
        _require_outcome(outcome, success="observed" if assessment else "failed")


def read_github_pr_extension(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    extension_id: UUID,
) -> GitHubPrExtension:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.read_github_pr_extension(%s, %s, %s, %s)",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(current_recovery_generation),
                extension_id,
            ),
        ).fetchone()
        _require_outcome(row[-1], success="found")
    evidence = row[10] or []
    if not isinstance(evidence, list):
        raise RuntimeError("GitHub PR extension evidence is invalid.")
    binding = read_github_read_binding(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        binding_id=row[2],
    )
    return GitHubPrExtension(
        id=row[0],
        site_id=site_id,
        binding_id=row[2],
        status=row[1],
        repository_id=row[3],
        base_sha=row[4],
        tree_sha=row[5],
        framework=row[6],
        content_format=row[7],
        coverage=row[8],
        content_sha=row[9],
        marker_evidence=tuple((item["path"], item["sha"]) for item in evidence),
        owner_accepted_unprotected=binding.owner_accepted_unprotected,
        accepted_default_branch=binding.default_branch
        if binding.owner_accepted_unprotected
        else None,
    )


def revoke_github_pr_extension(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    extension_id: UUID,
    owner_flow: bool = False,
) -> str:
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control."
            + ("revoke_owner_github_pr_extension" if owner_flow else "revoke_github_pr_extension")
            + "(%s, %s, %s, %s, %s)",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(current_recovery_generation),
                extension_id,
                uuid4(),
            ),
        ).fetchone()[0]
        if owner_flow and outcome in {"ACKNOWLEDGED", "AUTHORITY_DURABILITY_PENDING"}:
            return outcome
        _require_outcome(outcome, success="revoked")
    return outcome


async def observe_github_pr_extension(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    binding_id: UUID,
    idempotency_key: UUID,
    credential: OpenBaoGitHubAppCredential,
    github_transport: GitHubSharedEgressTransport | None,
    openbao_transport: httpx2.AsyncBaseTransport | None = None,
    openbao_verify: bool | object = True,
    prepared_extension_id: UUID | None = None,
    owner_flow: bool = False,
) -> GitHubPrExtension:
    if not isinstance(github_transport, GitHubSharedEgressTransport):
        raise GitHubPrExtensionUnavailable("GITHUB_EGRESS_UNAVAILABLE")
    binding = read_github_read_binding(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        binding_id=binding_id,
    )
    if binding.status != "active":
        raise GitHubPrExtensionUnavailable("GITHUB_BINDING_INACTIVE")
    prepared = prepare_github_pr_extension(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        binding_id=binding_id,
        idempotency_key=idempotency_key,
        owner_flow=owner_flow,
    )
    if prepared_extension_id is not None and (
        prepared.id != prepared_extension_id or prepared.status != "prepared"
    ):
        raise GitHubPrExtensionConflict()
    if prepared.status != "prepared":
        return read_github_pr_extension(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            site_id=site_id,
            extension_id=prepared.id,
        )
    try:
        credentials = await credential.credentials(
            transport=openbao_transport, verify=openbao_verify
        )
        pr_snapshot = await inspect_github_pr_authority(
            credentials=credentials,
            target=binding.target,
            transport=github_transport,
        )
        inventory = await inspect_github_repository_inventory(
            credentials=credentials,
            target=binding.target,
            transport=github_transport,
        )
        pr_snapshot = await check_github_binding_snapshot(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            binding=binding,
            snapshot=pr_snapshot,
            credentials=credentials,
            github_transport=github_transport,
        )
        _match_binding(binding, pr_snapshot)
        _match_binding(binding, inventory.snapshot)
        if (
            pr_snapshot.base_sha != inventory.snapshot.base_sha
            or pr_snapshot.protected != inventory.snapshot.protected
            or pr_snapshot.default_branch != inventory.snapshot.default_branch
        ):
            raise GitHubPrExtensionUnavailable("GITHUB_BINDING_CHANGED")
        metadata = None
        if any(
            entry.path.startswith("astro.config.") for entry in inventory.entries
        ) or inventory.snapshot.content_path.endswith((".md", ".mdx", ".njk")):
            checkout = await checkout_github_repository(
                credentials=credentials, target=binding.target, transport=github_transport
            )
            _match_binding(binding, checkout.inventory.snapshot)
            if (
                checkout.inventory.tree_sha != inventory.tree_sha
                or checkout.inventory.snapshot.base_sha != pr_snapshot.base_sha
                or checkout.inventory.snapshot.protected != pr_snapshot.protected
                or checkout.inventory.snapshot.default_branch != pr_snapshot.default_branch
            ):
                raise GitHubPrExtensionUnavailable("GITHUB_BINDING_CHANGED")
            metadata = dict(checkout.files)
        assessment = detect_repository_format(inventory, metadata)
    except (
        GitHubAppProtocolError,
        GitHubBindingUnavailable,
        GitHubPrExtensionUnavailable,
    ) as error:
        if binding.risk_monitored:
            record_github_binding_state(
                connection,
                session_token=session_token,
                current_recovery_generation=current_recovery_generation,
                binding=binding,
            )
        finish_github_pr_extension(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            prepared=prepared,
            failure_code=error.code,
            owner_flow=owner_flow,
        )
        raise GitHubPrExtensionUnavailable(error.code) from None
    finish_github_pr_extension(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        prepared=prepared,
        repository_id=pr_snapshot.repository_id,
        snapshot=pr_snapshot,
        assessment=assessment,
        owner_flow=owner_flow,
    )
    return read_github_pr_extension(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        extension_id=prepared.id,
    )


async def inspect_current_github_pr_extension(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    extension_id: UUID,
    credential: OpenBaoGitHubAppCredential,
    github_transport: GitHubSharedEgressTransport | None,
    openbao_transport: httpx2.AsyncBaseTransport | None = None,
) -> GitHubRepositorySnapshot:
    if not isinstance(github_transport, GitHubSharedEgressTransport):
        raise GitHubPrExtensionUnavailable("GITHUB_EGRESS_UNAVAILABLE")
    before = read_github_pr_extension(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        extension_id=extension_id,
    )
    if before.status != "observed" or not before.candidate_compatible:
        raise GitHubPrExtensionUnavailable("GITHUB_PR_EXTENSION_INACTIVE")
    binding = read_github_read_binding(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        binding_id=before.binding_id,
    )
    if binding.status != "active":
        raise GitHubPrExtensionUnavailable("GITHUB_BINDING_INACTIVE")
    try:
        credentials = await credential.credentials(transport=openbao_transport)
        snapshot = await inspect_github_pr_authority(
            credentials=credentials,
            target=binding.target,
            transport=github_transport,
        )
        snapshot = await check_github_binding_snapshot(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            binding=binding,
            snapshot=snapshot,
            credentials=credentials,
            github_transport=github_transport,
        )
    except (GitHubAppProtocolError, GitHubBindingUnavailable) as error:
        record_github_binding_state(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            binding=binding,
        )
        raise GitHubPrExtensionUnavailable(error.code) from None
    after = read_github_pr_extension(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        extension_id=extension_id,
    )
    _match_binding(binding, snapshot)
    if (
        after.status != "observed"
        or after.repository_id != before.repository_id
        or snapshot.base_sha != before.base_sha
    ):
        raise GitHubPrExtensionUnavailable("GITHUB_BINDING_CHANGED")
    return snapshot


def _match_binding(binding: GitHubReadBinding, snapshot: GitHubRepositorySnapshot) -> None:
    if (
        snapshot.repository_id != binding.repository_id
        or snapshot.full_name != binding.observed_full_name
        or snapshot.base_branch != binding.target.base_branch
        or snapshot.default_branch != binding.default_branch
        or not (
            snapshot.protected
            or snapshot.owner_accepted_unprotected
            or binding.owner_accepted_unprotected
        )
    ):
        raise GitHubPrExtensionUnavailable("GITHUB_BINDING_CHANGED")


_token_hash = session_token_hasher(InvalidSession)


def _require_outcome(outcome: str, *, success: str) -> None:
    require_outcome(
        outcome,
        success,
        {
            "invalid_session": InvalidSession(),
            "step_up_required": GitHubPrExtensionUnavailable("GITHUB_STEP_UP_REQUIRED"),
            **dict.fromkeys(
                (
                    "authorization_denied",
                    "permission_denied",
                    "site_not_verified",
                    "binding_not_authorized",
                    "extension_not_authorized",
                ),
                AuthorizationDenied(),
            ),
            **dict.fromkeys(
                (
                    "request_conflict",
                    "extension_exists",
                    "extension_not_prepared",
                    "invalid_observation",
                ),
                GitHubPrExtensionConflict(),
            ),
        },
        GitHubPrExtensionUnavailable("GITHUB_PR_EXTENSION_UNAVAILABLE"),
    )
