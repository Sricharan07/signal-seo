"""Seal exactly one root key-file addition into the existing reviewed Inbox."""

import hashlib
from uuid import NAMESPACE_URL, UUID, uuid5

import rfc8785
from psycopg import Connection

from signal_core.astro_source import inspect_astro_source
from signal_core.candidate_build import plan_candidate_build
from signal_core.candidate_build_service import CandidateRunner, build_candidate_from_github
from signal_core.github_app import checkout_github_repository
from signal_core.github_pr_extension import (
    inspect_current_github_pr_extension,
    read_github_pr_extension,
)
from signal_core.github_read_binding import (
    GitHubSharedEgressTransport,
    OpenBaoGitHubAppCredential,
    read_github_read_binding,
)
from signal_core.indexnow import IndexNowService, IndexNowUnavailable
from signal_core.indexnow_placement import indexnow_key_placement
from signal_core.indexnow_protocol import KEY_RECIPE
from signal_core.recipe_releases import get_reviewed_recipe_release
from signal_core.technical_recipe_service import SealedTechnicalRevision
from signal_core.technical_seo_recipes import technical_recipe_release_manifest


async def seal_indexnow_key_recipe(
    service: IndexNowService,
    release_connection: Connection,
    *,
    session_token: object,
    site_id: UUID,
    key_id: UUID,
    extension_id: UUID,
    recipe_release_id: UUID,
    build_idempotency_key: UUID,
    credential: OpenBaoGitHubAppCredential,
    github_transport: GitHubSharedEgressTransport,
    runner: CandidateRunner,
    previous_key_id: UUID | None = None,
    secret_options: dict | None = None,
    openbao_transport=None,
    registry_provider=None,
    registry_cache=None,
    artifact_store=None,
    artifact_key=None,
) -> SealedTechnicalRevision:
    if not all(
        isinstance(value, UUID)
        for value in (site_id, key_id, extension_id, recipe_release_id, build_idempotency_key)
    ):
        raise ValueError("Exact typed recipe identities are required.")
    release = get_reviewed_recipe_release(
        release_connection, recipe_key=KEY_RECIPE, release_id=recipe_release_id
    )
    if release.manifest != technical_recipe_release_manifest(
        KEY_RECIPE, recipe_release_id, version=str(release.version)
    ):
        raise IndexNowUnavailable("KEY_RECIPE_RELEASE_UNAVAILABLE")
    if not isinstance(github_transport, GitHubSharedEgressTransport):
        raise IndexNowUnavailable("KEY_RECIPE_EGRESS_UNAVAILABLE")
    _, origin, key = await service.create_key(
        session_token=session_token,
        site_id=site_id,
        key_id=key_id,
        previous_key_id=previous_key_id,
        secret_options=secret_options,
    )
    args = dict(
        session_token=session_token,
        site_id=site_id,
        current_recovery_generation=service.generation,
        extension_id=extension_id,
    )
    extension = read_github_pr_extension(service.connection, **args)
    if (extension.framework, extension.content_format) not in {
        ("eleventy", "html"),
        ("astro", "astro"),
    }:
        raise IndexNowUnavailable("KEY_RECIPE_FORMAT_UNAVAILABLE")
    current = await inspect_current_github_pr_extension(
        service.connection,
        **args,
        credential=credential,
        github_transport=github_transport,
        openbao_transport=openbao_transport,
    )
    binding = read_github_read_binding(
        service.connection,
        session_token=session_token,
        site_id=site_id,
        current_recovery_generation=service.generation,
        binding_id=extension.binding_id,
    )
    credentials = await credential.credentials(transport=openbao_transport)
    checkout = await checkout_github_repository(
        credentials=credentials, target=binding.target, transport=github_transport
    )
    if (
        checkout.inventory.snapshot.base_sha != current.base_sha
        or checkout.inventory.tree_sha != extension.tree_sha
    ):
        raise IndexNowUnavailable("KEY_RECIPE_SOURCE_DRIFT")
    path, output_path = indexnow_key_placement(extension.framework, dict(checkout.files), key)
    content = key.encode("utf-8")
    if path in dict(checkout.files):
        raise IndexNowUnavailable("KEY_RECIPE_FILE_EXISTS")
    patch = {path: content}
    plan = plan_candidate_build(
        extension, checkout, patch=patch, approved_paths=frozenset({path}), indexnow_key=key
    )
    build_options = dict(
        registry_provider=registry_provider,
        registry_cache=registry_cache,
        artifact_store=artifact_store,
        artifact_key=artifact_key,
    )
    baseline = None
    if extension.framework == "astro":
        baseline = await build_candidate_from_github(
            service.connection,
            **args,
            idempotency_key=uuid5(NAMESPACE_URL, f"indexnow-baseline:{build_idempotency_key}"),
            credential=credential,
            github_transport=github_transport,
            runner=runner,
            openbao_transport=openbao_transport,
            **build_options,
        )
    build = await build_candidate_from_github(
        service.connection,
        **args,
        idempotency_key=build_idempotency_key,
        credential=credential,
        github_transport=github_transport,
        runner=runner,
        patch=patch,
        approved_paths=frozenset({path}),
        openbao_transport=openbao_transport,
        indexnow_key=key,
        **build_options,
    )
    digest = hashlib.sha256(content).hexdigest()
    if (
        build.status != "completed"
        or build.exit_class != "passed"
        or build.patch_sha256 != plan.patch_sha256
        or build.base_sha != plan.base_sha
        or (output_path, digest, len(content)) not in build.artifacts
        or baseline is not None
        and (
            baseline.status != "completed"
            or baseline.exit_class != "passed"
            or baseline.base_sha != build.base_sha
            or baseline.lockfile_sha256 != build.lockfile_sha256
            or any(p == output_path for p, _, _ in baseline.artifacts)
            or set(build.artifacts)
            != set(baseline.artifacts) | {(output_path, digest, len(content))}
        )
    ):
        raise IndexNowUnavailable("KEY_RECIPE_STATIC_OUTPUT_UNAVAILABLE")
    finding = {
        "id": str(key_id),
        "key": "indexnow.key.required",
        "title": "IndexNow key file",
        "summary": "Publish the verified site's key file before notifying search engines.",
        "resource_locator": origin + "/" + key + ".txt",
    }
    manifest = {
        "schema_version": 1,
        "site_id": str(site_id),
        "extension_id": str(extension_id),
        "build_id": str(build.id),
        "audit_report_id": None,
        "finding_id": str(key_id),
        "recipe_release_id": str(recipe_release_id),
        "release_content_hash": release.content_hash,
        "base_sha": build.base_sha,
        "patch_sha256": build.patch_sha256,
        "source_path": path,
        "source_sha256": hashlib.sha256(b"").hexdigest(),
        "result_sha256": digest,
        "patch": {"offset": 0, "before": "", "after": key},
        "evidence": {
            "key_id": str(key_id),
            "key_sha256": digest,
            "site_origin": origin,
            "page_url": origin + "/" + key + ".txt",
            "finding": finding,
        },
        "build_receipt": {
            "toolchain": build.toolchain,
            "command": build.command,
            "exit_class": build.exit_class,
            "logs_sha256": build.logs_sha256,
            "artifacts": [{"path": p, "sha256": h, "size": s} for p, h, s in build.artifacts],
        },
        "expected_impact": (
            "Enable changed-URL notification only after this exact key file is deployed."
        ),
        "recovery_plan": (
            "Retire this key generation; any replacement requires a new reviewed key-file PR. "
            "Never delete the old file automatically."
        ),
        "approval_class": "owner_review",
        "claim_review_required": False,
        "model_draft": None,
    }
    if baseline is not None:
        manifest.update(
            approval_class="A4",
            autonomy_eligible=False,
            static_key_placement={
                "public_directory": inspect_astro_source(dict(checkout.files)).public_directory,
                "output_path": output_path,
                "baseline_build_id": str(baseline.id),
            },
        )
    body = rfc8785.dumps(manifest)
    revision_id = uuid5(NAMESPACE_URL, f"indexnow-key-revision:{site_id}:{key_id}")
    row = service._call(
        "seal_indexnow_key_revision",
        session_token,
        site_id,
        key_id,
        revision_id,
        body,
        hashlib.sha256(body).digest(),
    )
    if not row or row[0] != "sealed":
        raise IndexNowUnavailable("KEY_RECIPE_SEAL_UNAVAILABLE")
    return SealedTechnicalRevision(
        revision_id, hashlib.sha256(body).hexdigest(), build.id, build.replayed
    )
