"""Owner-scoped crawl reads and reviewed candidate sealing through existing ports."""

import hashlib
from uuid import NAMESPACE_URL, UUID, uuid5

from signal_core.authorization import AuthorizationDenied
from signal_core.candidate_build import plan_candidate_build
from signal_core.candidate_build_service import build_candidate_from_github
from signal_core.database import _clean_transaction
from signal_core.github_app import checkout_github_repository
from signal_core.github_pr_extension import (
    inspect_current_github_pr_extension,
    read_github_pr_extension,
)
from signal_core.github_read_binding import GitHubSharedEgressTransport, read_github_read_binding
from signal_core.internal_linking import RECIPE, link_opportunities, make_internal_link_patch
from signal_core.proposals import canonicalize_proposal_manifest
from signal_core.recipe_releases import RecipeReleaseUnavailable, get_reviewed_recipe_release
from signal_core.session_issuance import validate_recovery_generation
from signal_core.structured_data_recipe import assert_structured_build, static_page_url
from signal_core.technical_recipe_service import SealedTechnicalRevision, _token_hash
from signal_core.technical_seo_recipes import (
    TechnicalRecipeUnavailable,
    technical_recipe_release_manifest,
)


def load_internal_link_sources(connection, *, session_token, generation, site_id):
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT control.internal_link_sources(%s,%s,%s)",
            (_token_hash(session_token), validate_recovery_generation(generation), site_id),
        ).fetchone()
    if not row or row[0] is None:
        raise AuthorizationDenied()
    return row[0]


async def seal_internal_link_revision(
    identity_connection,
    release_connection,
    *,
    session_token,
    current_recovery_generation,
    site_id,
    extension_id,
    source_id,
    target_id,
    recipe_release_id,
    idempotency_key,
    build_idempotency_key,
    credential,
    github_transport,
    runner,
    page_cap=3,
    baseline_idempotency_key=None,
    openbao_transport=None,
):
    if not all(
        isinstance(v, UUID)
        for v in (
            site_id,
            extension_id,
            source_id,
            target_id,
            recipe_release_id,
            idempotency_key,
            build_idempotency_key,
        )
    ):
        raise ValueError("Invalid internal link request.")
    if not isinstance(github_transport, GitHubSharedEgressTransport):
        raise TechnicalRecipeUnavailable("RECIPE_EGRESS_UNAVAILABLE")
    release = get_reviewed_recipe_release(
        release_connection, recipe_key=RECIPE, release_id=recipe_release_id
    )
    if release.manifest != technical_recipe_release_manifest(
        RECIPE, recipe_release_id, version=str(release.version)
    ):
        raise RecipeReleaseUnavailable("Exact reviewed internal-link contract required.")
    sources = load_internal_link_sources(
        identity_connection,
        session_token=session_token,
        generation=current_recovery_generation,
        site_id=site_id,
    )
    prior = next(
        (c for c in sources["candidates"] if c.get("idempotency_key") == str(idempotency_key)), None
    )
    if prior:
        if (
            any(
                prior.get(k) != str(v)
                for k, v in (
                    ("source_id", source_id),
                    ("target_id", target_id),
                    ("extension_id", extension_id),
                    ("recipe_release_id", recipe_release_id),
                )
            )
            or prior.get("page_cap") != page_cap
        ):
            raise TechnicalRecipeUnavailable("INTERNAL_LINK_REVISION_CONFLICT")
        return SealedTechnicalRevision(
            UUID(prior["id"]), prior["revision_sha256"], UUID(prior["build_id"]), True
        )
    opportunity = next(
        (
            p
            for p in link_opportunities(sources["pages"], sources["site_origin"], page_cap=page_cap)
            if p["source_id"] == str(source_id) and p["target_id"] == str(target_id)
        ),
        None,
    )
    if not opportunity:
        raise TechnicalRecipeUnavailable("INTERNAL_LINK_OPPORTUNITY_UNAVAILABLE")
    source = next(p for p in sources["pages"] if p["id"] == str(source_id))
    evidence = {
        "site_origin": sources["site_origin"],
        "page_url": source["url"],
        "page_id": source["id"],
        "page_body_sha256": source["body_sha256"],
    }
    common = dict(
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
    )
    extension = read_github_pr_extension(identity_connection, **common, extension_id=extension_id)
    current = await inspect_current_github_pr_extension(
        identity_connection,
        **common,
        extension_id=extension_id,
        credential=credential,
        github_transport=github_transport,
        openbao_transport=openbao_transport,
    )
    binding = read_github_read_binding(
        identity_connection, **common, binding_id=extension.binding_id
    )
    credentials = await credential.credentials(transport=openbao_transport)
    checkout = await checkout_github_repository(
        credentials=credentials, target=binding.target, transport=github_transport
    )
    if (
        checkout.inventory.snapshot.repository_id != current.repository_id
        or checkout.inventory.snapshot.full_name != current.full_name
        or checkout.inventory.snapshot.base_sha != current.base_sha
    ):
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_DRIFT")
    paths = []
    for path, _ in checkout.files:
        if path.endswith(".html"):
            try:
                if static_page_url(path, sources["site_origin"]) == source["url"]:
                    paths.append(path)
            except TechnicalRecipeUnavailable:
                continue
    if len(paths) != 1:
        raise TechnicalRecipeUnavailable("RECIPE_FORMAT_UNAVAILABLE")
    patch = make_internal_link_patch(
        evidence=evidence,
        extension=extension,
        checkout=checkout,
        opportunity=opportunity,
        used_anchors=sources["used_anchors"],
        source_path=paths[0],
    )
    expected = plan_candidate_build(
        extension, checkout, patch=patch.patch, approved_paths=frozenset({patch.path})
    )
    baseline = await build_candidate_from_github(
        identity_connection,
        **common,
        extension_id=extension_id,
        idempotency_key=baseline_idempotency_key
        or uuid5(NAMESPACE_URL, f"internal-link-baseline:{build_idempotency_key}"),
        credential=credential,
        github_transport=github_transport,
        runner=runner,
        openbao_transport=openbao_transport,
    )
    build = await build_candidate_from_github(
        identity_connection,
        **common,
        extension_id=extension_id,
        idempotency_key=build_idempotency_key,
        credential=credential,
        github_transport=github_transport,
        runner=runner,
        patch=patch.patch,
        approved_paths=frozenset({patch.path}),
        openbao_transport=openbao_transport,
    )
    if build.status != "completed" or build.exit_class != "passed" or not build.artifacts:
        raise TechnicalRecipeUnavailable("RECIPE_BUILD_FAILED")
    if build.base_sha != expected.base_sha or build.patch_sha256 != expected.patch_sha256:
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_DRIFT")
    if baseline.base_sha != build.base_sha:
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_DRIFT")
    assert_structured_build(patch, baseline, build, output_path="_site/" + patch.path)
    finding_id = uuid5(
        NAMESPACE_URL, f"internal-link:{sources['manifest_id']}:{source_id}:{target_id}"
    )
    manifest = {
        "schema_version": 1,
        "site_id": str(site_id),
        "extension_id": str(extension_id),
        "build_id": str(build.id),
        "finding_id": str(finding_id),
        "recipe_release_id": str(recipe_release_id),
        "release_content_hash": release.content_hash,
        "base_sha": build.base_sha,
        "patch_sha256": build.patch_sha256,
        "source_path": patch.path,
        "source_sha256": hashlib.sha256(patch.before).hexdigest(),
        "result_sha256": hashlib.sha256(patch.after).hexdigest(),
        "audit_report_id": None,
        "internal_link": {
            "recipe_key": RECIPE,
            "page_cap": page_cap,
            "autonomy_eligible": False,
            "baseline_build_id": str(baseline.id),
            "output_path": "_site/" + patch.path,
            "target_id": str(target_id),
            "target_url": opportunity["target_url"],
            "graph": opportunity,
            "coverage": sources["coverage"],
        },
        "patch": {
            "offset": patch.offset,
            "before": patch.before_fragment,
            "after": patch.after_fragment,
        },
        "evidence": {
            "manifest_id": sources["manifest_id"],
            "manifest_sha256": sources["manifest_sha256"],
            "page_id": str(source_id),
            "page_url": source["url"],
            "site_origin": sources["site_origin"],
            "finding": {
                "id": str(finding_id),
                "key": "links.internal.add",
                "resource_locator": source["url"],
                "title": "Relevant contextual internal link",
                "summary": "Observed weak incoming links and shared salient heading terms; "
                "exact existing paragraph anchor requires owner review.",
            },
        },
        "build_receipt": {
            "toolchain": build.toolchain,
            "command": build.command,
            "exit_class": build.exit_class,
            "logs_sha256": build.logs_sha256,
            "artifacts": [{"path": p, "sha256": d, "size": s} for p, d, s in build.artifacts],
        },
        "approval_class": "owner_review",
        "claim_review_required": False,
        "model_draft": None,
        "expected_impact": patch.expected_impact,
        "recovery_plan": patch.recovery_plan,
    }
    canonical = canonicalize_proposal_manifest(manifest)
    revision_id = uuid5(NAMESPACE_URL, f"internal-link-revision:{site_id}:{idempotency_key}")
    with _clean_transaction(identity_connection):
        result = identity_connection.execute(
            "SELECT control.seal_internal_link_revision(%s,%s,%s,%s,%s,%s)",
            (
                _token_hash(session_token),
                validate_recovery_generation(current_recovery_generation),
                site_id,
                revision_id,
                idempotency_key,
                canonical,
            ),
        ).fetchone()[0]
    if result != "sealed":
        raise TechnicalRecipeUnavailable("INTERNAL_LINK_" + result.upper())
    return SealedTechnicalRevision(
        revision_id, hashlib.sha256(canonical).hexdigest(), build.id, False
    )
