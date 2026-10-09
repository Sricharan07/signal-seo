"""Owner-attributed, replay-safe candidate build orchestration."""

import hashlib
import json
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4, uuid5

import httpx2
import rfc8785
from anyio import to_thread
from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.authorization import AuthorizationDenied, InvalidSession, authorize_snapshot
from signal_core.candidate_build import (
    CandidateBuildPlan,
    CandidatePolicyRejected,
    plan_candidate_build,
)
from signal_core.candidate_sandbox import CandidateSandboxOutcome
from signal_core.crawl_artifacts import (
    ArtifactConflict,
    ArtifactEncryptionKey,
    ArtifactIntegrityError,
    ArtifactRecord,
    ArtifactUnavailable,
    EncryptedLocalArtifactStore,
)
from signal_core.database import _clean_transaction
from signal_core.github_app import GitHubAppProtocolError, checkout_github_repository
from signal_core.github_pr_extension import (
    GitHubPrExtensionUnavailable,
    inspect_current_github_pr_extension,
    read_github_pr_extension,
)
from signal_core.github_read_binding import (
    GitHubBindingUnavailable,
    GitHubSharedEgressTransport,
    OpenBaoGitHubAppCredential,
    read_github_read_binding,
)
from signal_core.npm_registry import NpmRegistryCache, NpmRegistryUnavailable
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import session_token_hasher
from signal_core.shared_egress import SharedEgressProvider


class CandidateBuildConflict(Exception):
    """A candidate intent conflicts with current authority or prior identity."""


class CandidateBuildUnavailable(Exception):
    """The candidate cannot be built or its result cannot be claimed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CandidateRunner(Protocol):
    def run(self, plan: CandidateBuildPlan) -> CandidateSandboxOutcome: ...


@dataclass(frozen=True)
class CandidateBuildRecord:
    id: UUID
    site_id: UUID
    status: str
    base_sha: str
    patch_sha256: str
    toolchain: str
    command: str
    exit_class: str | None = None
    exit_code: int | None = None
    logs_sha256: str | None = None
    log_bytes: int | None = None
    artifacts: tuple[tuple[str, str, int], ...] = ()
    replayed: bool = False
    lockfile_sha256: str | None = None
    built_pages: tuple[tuple[str, str, str | None, str | None], ...] = ()
    unavailable_reason: str | None = None
    built_html: tuple[tuple[str, str], ...] = ()


def prepare_candidate_build(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    extension_id: UUID,
    idempotency_key: UUID,
    plan: CandidateBuildPlan,
) -> CandidateBuildRecord:
    if not all(isinstance(value, UUID) for value in (site_id, extension_id, idempotency_key)):
        raise ValueError("Candidate build identities are invalid.")
    if not isinstance(plan, CandidateBuildPlan):
        raise ValueError("A validated candidate build plan is required.")
    command = " ".join(plan.command)
    digest = hashlib.sha256(
        "\n".join(
            (
                str(site_id),
                str(extension_id),
                plan.base_sha,
                plan.tree_sha,
                plan.patch_sha256,
                plan.toolchain,
                command,
                plan.artifact_root,
            )
        ).encode("ascii")
    ).digest()
    if plan.dependency_manifest:
        digest = hashlib.sha256(
            digest + bytes.fromhex(plan.dependency_manifest.lockfile_sha256)
        ).digest()
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT build_id, build_status, replayed, outcome FROM control.prepare_candidate_build("
            + ", ".join(["%s"] * 13)
            + ")",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(current_recovery_generation),
                extension_id,
                uuid4(),
                idempotency_key,
                digest,
                plan.base_sha,
                plan.tree_sha,
                plan.patch_sha256,
                plan.toolchain,
                command,
                plan.artifact_root,
            ),
        ).fetchone()
        _require_outcome(row[3], success="prepared")
        if plan.dependency_manifest:
            outcome = connection.execute(
                "SELECT control.bind_candidate_dependencies(%s,%s,%s,%s,%s)",
                (
                    _token_hash(session_token),
                    site_id,
                    validate_recovery_generation(current_recovery_generation),
                    row[0],
                    plan.dependency_manifest.lockfile_sha256,
                ),
            ).fetchone()[0]
            _require_outcome(outcome, success="bound")
    return CandidateBuildRecord(
        row[0],
        site_id,
        row[1],
        plan.base_sha,
        plan.patch_sha256,
        plan.toolchain,
        command,
        replayed=row[2],
    )


def dispatch_candidate_build(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    prepared: CandidateBuildRecord,
) -> None:
    if prepared.status != "prepared":
        raise CandidateBuildUnavailable("CANDIDATE_DISPATCH_UNKNOWN")
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control.dispatch_candidate_build(%s, %s, %s, %s)",
            (
                _token_hash(session_token),
                prepared.site_id,
                validate_recovery_generation(current_recovery_generation),
                prepared.id,
            ),
        ).fetchone()[0]
        _require_outcome(outcome, success="dispatched")


def finish_candidate_build(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    prepared: CandidateBuildRecord,
    result: CandidateSandboxOutcome,
    artifact_store: EncryptedLocalArtifactStore | None = None,
    artifact_key: ArtifactEncryptionKey | None = None,
) -> CandidateBuildRecord:
    if not isinstance(result, CandidateSandboxOutcome):
        raise ValueError("A bounded sandbox result is required.")
    artifacts = [
        {"path": path, "sha256": digest, "size": size} for path, digest, size in result.artifacts
    ]
    storage = nullcontext(None)
    payload = None
    if result.built_html:
        if not isinstance(artifact_store, EncryptedLocalArtifactStore) or not isinstance(
            artifact_key, ArtifactEncryptionKey
        ):
            raise CandidateBuildUnavailable("CANDIDATE_ARTIFACT_STORAGE_UNAVAILABLE")
        authority = authorize_snapshot(
            connection,
            session_token=session_token,
            requested_site_id=prepared.site_id,
            current_recovery_generation=current_recovery_generation,
        )
        if authority.role_key != "owner":
            raise CandidateBuildUnavailable("CANDIDATE_ARTIFACT_STORAGE_UNAVAILABLE")
        payload = built_html_payload(result.built_html)
        storage = _stage_candidate_html(
            artifact_store,
            authority.scope,
            uuid5(prepared.id, "built-html"),
            payload,
            media_type="application/json",
            key=artifact_key,
        )
    with storage as artifact, _clean_transaction(connection):
        dependency_args = (
            (
                result.lockfile_sha256,
                Jsonb(
                    [
                        {"path": path, "sha256": digest, "title": title, "description": description}
                        for path, digest, title, description in result.built_pages
                    ]
                ),
                result.unavailable_reason,
            )
            if result.lockfile_sha256
            else ()
        )
        outcome = connection.execute(
            "SELECT control."
            + ("finish_candidate_dependency_build" if dependency_args else "finish_candidate_build")
            + "("
            + ",".join(["%s"] * (12 if dependency_args else 9))
            + ")",
            (
                _token_hash(session_token),
                prepared.site_id,
                validate_recovery_generation(current_recovery_generation),
                prepared.id,
                result.exit_class,
                result.exit_code,
                result.logs_sha256,
                result.log_bytes,
                Jsonb(artifacts),
            )
            + dependency_args,
        ).fetchone()[0]
        _require_outcome(outcome, success="completed")
        if result.built_html:
            outcome = connection.execute(
                "SELECT control.bind_candidate_built_html(" + ",".join(["%s"] * 9) + ")",
                (
                    _token_hash(session_token),
                    prepared.site_id,
                    validate_recovery_generation(current_recovery_generation),
                    prepared.id,
                    payload,
                    artifact.artifact_id,
                    artifact.encryption_key_ref,
                    artifact.created_at,
                    artifact.created_at + timedelta(days=30),
                ),
            ).fetchone()[0]
            _require_outcome(outcome, success="bound")
    return read_candidate_build(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=prepared.site_id,
        build_id=prepared.id,
        include_dependencies=result.lockfile_sha256 is not None,
        include_html=bool(result.built_html),
        artifact_store=artifact_store,
        artifact_key=artifact_key,
    )


def read_candidate_build(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    build_id: UUID,
    include_dependencies: bool = False,
    include_html: bool = False,
    artifact_store: EncryptedLocalArtifactStore | None = None,
    artifact_key: ArtifactEncryptionKey | None = None,
) -> CandidateBuildRecord:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.read_candidate_build(%s, %s, %s, %s)",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(current_recovery_generation),
                build_id,
            ),
        ).fetchone()
        _require_outcome(row[-1], success="found")
    evidence = row[9] or []
    if not isinstance(evidence, list):
        raise CandidateBuildUnavailable("CANDIDATE_RECEIPT_INVALID")
    record = CandidateBuildRecord(
        build_id,
        site_id,
        row[0],
        row[1],
        row[2],
        row[3],
        row[4],
        exit_class=row[5],
        exit_code=row[6],
        logs_sha256=row[7],
        log_bytes=row[8],
        artifacts=tuple((item["path"], item["sha256"], item["size"]) for item in evidence),
    )
    if include_dependencies:
        with _clean_transaction(connection):
            dependency = connection.execute(
                "SELECT control.read_candidate_dependency_receipt(%s,%s,%s,%s)",
                (
                    _token_hash(session_token),
                    site_id,
                    validate_recovery_generation(current_recovery_generation),
                    build_id,
                ),
            ).fetchone()[0]
        if not isinstance(dependency, dict):
            raise CandidateBuildUnavailable("CANDIDATE_DEPENDENCY_RECEIPT_UNAVAILABLE")
        record = replace(
            record,
            lockfile_sha256=dependency["lockfile_sha256"],
            built_pages=tuple(
                (item["path"], item["sha256"], item["title"], item["description"])
                for item in dependency["built_pages"]
            ),
            unavailable_reason=dependency["unavailable_reason"],
        )
    if include_html:
        with _clean_transaction(connection):
            html = connection.execute(
                "SELECT control.read_candidate_built_html(%s,%s,%s,%s)",
                (
                    _token_hash(session_token),
                    site_id,
                    validate_recovery_generation(current_recovery_generation),
                    build_id,
                ),
            ).fetchone()[0]
        if (
            not isinstance(html, dict)
            or not isinstance(artifact_store, EncryptedLocalArtifactStore)
            or not isinstance(artifact_key, ArtifactEncryptionKey)
        ):
            raise CandidateBuildUnavailable("CANDIDATE_HTML_UNAVAILABLE")
        for field in ("tenant_id", "site_id", "artifact_id"):
            html[field] = UUID(html[field])
        for field in ("created_at", "retain_until"):
            html[field] = datetime.fromisoformat(html[field])
        try:
            pages = json.loads(artifact_store.read(ArtifactRecord(**html), key=artifact_key))
        except (OSError, ArtifactUnavailable, ArtifactIntegrityError, ValueError):
            raise CandidateBuildUnavailable("CANDIDATE_HTML_UNAVAILABLE") from None
        record = replace(record, built_html=tuple((item["path"], item["html"]) for item in pages))
    return record


async def build_candidate_from_github(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    extension_id: UUID,
    idempotency_key: UUID,
    credential: OpenBaoGitHubAppCredential,
    github_transport: GitHubSharedEgressTransport | None,
    runner: CandidateRunner,
    patch: Mapping[str, bytes] | None = None,
    approved_paths: frozenset[str] = frozenset(),
    openbao_transport: httpx2.AsyncBaseTransport | None = None,
    registry_provider: SharedEgressProvider | None = None,
    registry_cache: NpmRegistryCache | None = None,
    astro_recipe: dict | None = None,
    indexnow_key: str | None = None,
    front_matter_recipe: dict | None = None,
    nextjs_recipe: dict | None = None,
    artifact_store: EncryptedLocalArtifactStore | None = None,
    artifact_key: ArtifactEncryptionKey | None = None,
) -> CandidateBuildRecord:
    if not isinstance(github_transport, GitHubSharedEgressTransport):
        raise CandidateBuildUnavailable("GITHUB_EGRESS_UNAVAILABLE")
    extension = read_github_pr_extension(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        extension_id=extension_id,
    )
    if not extension.candidate_compatible:
        raise CandidateBuildUnavailable("CANDIDATE_EXTENSION_INACTIVE")
    if (
        extension.framework == "astro"
        or front_matter_recipe is not None
        or nextjs_recipe is not None
    ) and (
        not isinstance(registry_cache, NpmRegistryCache)
        or not isinstance(registry_provider, SharedEgressProvider)
        or registry_provider.run.site_id != site_id
    ):
        raise CandidateBuildUnavailable("NPM_EGRESS_UNAVAILABLE")
    if (
        extension.framework == "astro"
        or front_matter_recipe is not None
        or nextjs_recipe is not None
    ) and (
        not isinstance(artifact_store, EncryptedLocalArtifactStore)
        or not isinstance(artifact_key, ArtifactEncryptionKey)
    ):
        raise CandidateBuildUnavailable("CANDIDATE_ARTIFACT_STORAGE_UNAVAILABLE")
    try:
        current = await inspect_current_github_pr_extension(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            site_id=site_id,
            extension_id=extension_id,
            credential=credential,
            github_transport=github_transport,
            openbao_transport=openbao_transport,
        )
        binding = read_github_read_binding(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            site_id=site_id,
            binding_id=extension.binding_id,
        )
        credentials = await credential.credentials(transport=openbao_transport)
        checkout = await checkout_github_repository(
            credentials=credentials,
            target=binding.target,
            transport=github_transport,
        )
    except (
        GitHubPrExtensionUnavailable,
        GitHubBindingUnavailable,
        GitHubAppProtocolError,
    ) as error:
        raise CandidateBuildUnavailable(error.code) from None
    snapshot = checkout.inventory.snapshot
    if (
        snapshot.repository_id != current.repository_id
        or snapshot.full_name != current.full_name
        or snapshot.base_sha != current.base_sha
    ):
        raise CandidateBuildUnavailable("CANDIDATE_SOURCE_CHANGED")
    try:
        plan = plan_candidate_build(
            extension,
            checkout,
            patch=patch,
            approved_paths=approved_paths,
            astro_recipe=astro_recipe,
            indexnow_key=indexnow_key,
            front_matter_recipe=front_matter_recipe,
            nextjs_recipe=nextjs_recipe,
        )
    except CandidatePolicyRejected as error:
        raise CandidateBuildUnavailable(error.code) from None
    if plan.dependency_manifest:
        try:
            dependencies = await registry_cache.fetch(plan.dependency_manifest, registry_provider)
        except NpmRegistryUnavailable as error:
            raise CandidateBuildUnavailable(str(error)) from None
        plan = replace(plan, dependencies=dependencies)
    prepared = prepare_candidate_build(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        extension_id=extension_id,
        idempotency_key=idempotency_key,
        plan=plan,
    )
    if prepared.status == "completed":
        return read_candidate_build(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            site_id=site_id,
            build_id=prepared.id,
            include_dependencies=plan.dependency_manifest is not None,
            include_html=extension.framework == "astro"
            or front_matter_recipe is not None
            or nextjs_recipe is not None,
            artifact_store=artifact_store,
            artifact_key=artifact_key,
        )
    if prepared.status == "dispatched":
        raise CandidateBuildUnavailable("CANDIDATE_DISPATCH_UNKNOWN")
    dispatch_candidate_build(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        prepared=prepared,
    )
    result = await to_thread.run_sync(runner.run, plan)
    return finish_candidate_build(
        connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        prepared=prepared,
        result=result,
        artifact_store=artifact_store,
        artifact_key=artifact_key,
    )


def built_html_payload(pages: tuple[tuple[str, str], ...]) -> bytes:
    payload = rfc8785.dumps([{"path": path, "html": html} for path, html in sorted(pages)])
    if not 1 <= len(payload) <= 20 * 1024 * 1024:
        raise CandidateBuildUnavailable("CANDIDATE_HTML_UNAVAILABLE")
    return payload


@contextmanager
def _stage_candidate_html(store, *args, **kwargs):
    try:
        with store.stage_verified(*args, **kwargs) as artifact:
            yield artifact
    except (OSError, ArtifactUnavailable, ArtifactIntegrityError, ArtifactConflict, ValueError):
        raise CandidateBuildUnavailable("CANDIDATE_ARTIFACT_STORAGE_UNAVAILABLE") from None


_token_hash = session_token_hasher(InvalidSession)


def _require_outcome(outcome: str, *, success: str) -> None:
    if outcome == success:
        return
    if outcome == "invalid_session":
        raise InvalidSession()
    if outcome in {
        "authorization_denied",
        "permission_denied",
        "site_not_verified",
        "build_not_authorized",
    }:
        raise AuthorizationDenied()
    if outcome in {"request_conflict", "build_exists", "receipt_conflict", "invalid_receipt"}:
        raise CandidateBuildConflict()
    if outcome == "dispatch_unknown":
        raise CandidateBuildUnavailable("CANDIDATE_DISPATCH_UNKNOWN")
    raise CandidateBuildUnavailable("CANDIDATE_BUILD_UNAVAILABLE")
