"""Owner-attributed Astro sealing through the existing credential-free build boundary."""

import hashlib
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from signal_core.astro_recipes import (
    ASTRO_RECIPES,
    astro_recipe_release_manifest,
    make_astro_recipe_patch,
    verify_astro_impact,
)
from signal_core.astro_source import inspect_astro_source
from signal_core.candidate_build_service import build_candidate_from_github, built_html_payload
from signal_core.database import _clean_transaction
from signal_core.front_matter_recipes import (
    FRONT_MATTER_RECIPES,
    front_matter_build_profile,
    front_matter_recipe_release_manifest,
    make_front_matter_recipe_patch,
    verify_front_matter_impact,
)
from signal_core.github_app import checkout_github_repository
from signal_core.github_pr_extension import (
    inspect_current_github_pr_extension,
    read_github_pr_extension,
)
from signal_core.github_read_binding import GitHubSharedEgressTransport, read_github_read_binding
from signal_core.nextjs_recipes import (
    NEXTJS_RECIPES,
    make_nextjs_recipe_patch,
    nextjs_build_profile,
    nextjs_recipe_release_manifest,
    verify_nextjs_impact,
)
from signal_core.proposals import canonicalize_proposal_manifest
from signal_core.recipe_releases import RecipeReleaseUnavailable, get_reviewed_recipe_release
from signal_core.session_issuance import validate_recovery_generation
from signal_core.technical_seo_recipes import RECIPE_FINDING_KEYS, TechnicalRecipeUnavailable


async def seal_astro_recipe_revision(*args, **kwargs):
    return await _seal_built_recipe_revision(*args, **kwargs, adapter="astro")


async def seal_front_matter_recipe_revision(*args, **kwargs):
    return await _seal_built_recipe_revision(*args, **kwargs, adapter="front_matter")


async def seal_nextjs_recipe_revision(*args, **kwargs):
    return await _seal_built_recipe_revision(*args, **kwargs, adapter="nextjs_metadata")


async def _seal_built_recipe_revision(
    identity_connection,
    release_connection,
    *,
    session_token,
    current_recovery_generation,
    site_id: UUID,
    extension_id: UUID,
    report_id: UUID,
    finding_id: UUID,
    recipe_key: str,
    recipe_release_id: UUID,
    idempotency_key: UUID,
    build_idempotency_key: UUID,
    credential,
    github_transport,
    runner,
    edit: dict,
    expected_by_page: dict[str, str],
    registry_provider,
    registry_cache,
    image_src: str | None = None,
    openbao_transport=None,
    artifact_store=None,
    artifact_key=None,
    adapter,
):
    from signal_core.technical_recipe_service import (
        SealedTechnicalRevision,
        _load_evidence,
        _token_hash,
    )

    front_matter = adapter == "front_matter"
    nextjs = adapter == "nextjs_metadata"
    recipes, release_manifest, make_patch, verify_impact = {
        "astro": (
            ASTRO_RECIPES,
            astro_recipe_release_manifest,
            make_astro_recipe_patch,
            verify_astro_impact,
        ),
        "front_matter": (
            FRONT_MATTER_RECIPES,
            front_matter_recipe_release_manifest,
            make_front_matter_recipe_patch,
            verify_front_matter_impact,
        ),
        "nextjs_metadata": (
            NEXTJS_RECIPES,
            nextjs_recipe_release_manifest,
            make_nextjs_recipe_patch,
            verify_nextjs_impact,
        ),
    }[adapter]
    if recipe_key not in recipes or not all(
        isinstance(value, UUID)
        for value in (
            site_id,
            extension_id,
            report_id,
            finding_id,
            recipe_release_id,
            idempotency_key,
            build_idempotency_key,
        )
    ):
        raise ValueError("Invalid Astro recipe request.")
    release = get_reviewed_recipe_release(
        release_connection, recipe_key=recipe_key, release_id=recipe_release_id
    )
    if release.manifest != release_manifest(
        recipe_key, recipe_release_id, version=str(release.version)
    ):
        raise RecipeReleaseUnavailable("Astro recipe contract is unavailable.")
    evidence = _load_evidence(
        identity_connection,
        session_token,
        current_recovery_generation,
        site_id,
        report_id,
        finding_id,
    )
    if evidence["finding"]["key"] not in RECIPE_FINDING_KEYS[recipes[recipe_key]] or evidence.get(
        "page_output_truncated"
    ):
        raise TechnicalRecipeUnavailable("ASTRO_FINDING_UNAVAILABLE")
    if recipe_key == "astro_alt" and image_src not in (evidence.get("missing_alt_images") or []):
        raise TechnicalRecipeUnavailable("ASTRO_IMAGE_MAPPING_UNAVAILABLE")
    if not isinstance(github_transport, GitHubSharedEgressTransport):
        raise TechnicalRecipeUnavailable("RECIPE_EGRESS_UNAVAILABLE")
    args = dict(
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        extension_id=extension_id,
    )
    extension = read_github_pr_extension(identity_connection, **args)
    if (adapter == "astro" and extension.framework != "astro") or (
        nextjs and extension.framework != "nextjs"
    ):
        raise TechnicalRecipeUnavailable("ASTRO_FORMAT_UNAVAILABLE")
    current = await inspect_current_github_pr_extension(
        identity_connection,
        **args,
        credential=credential,
        github_transport=github_transport,
        openbao_transport=openbao_transport,
    )
    binding = read_github_read_binding(
        identity_connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        binding_id=extension.binding_id,
    )
    checkout = await checkout_github_repository(
        credentials=await credential.credentials(transport=openbao_transport),
        target=binding.target,
        transport=github_transport,
    )
    snapshot = checkout.inventory.snapshot
    if (
        snapshot.repository_id != current.repository_id
        or snapshot.base_sha != current.base_sha
        or checkout.inventory.tree_sha != extension.tree_sha
    ):
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_DRIFT")
    files = dict(checkout.files)
    root = (
        nextjs_build_profile(files)[0]
        if nextjs
        else front_matter_build_profile(files, extension.framework)[0]
        if front_matter
        else inspect_astro_source(files).output_directory
    )
    if not isinstance(edit, dict) or set(edit) != {"path", "offset", "before", "after"}:
        raise TechnicalRecipeUnavailable("ASTRO_EDIT_UNAVAILABLE")
    patch = make_patch(files=files, recipe_key=recipe_key, **edit)
    build_args = dict(
        args,
        credential=credential,
        github_transport=github_transport,
        runner=runner,
        registry_provider=registry_provider,
        registry_cache=registry_cache,
        openbao_transport=openbao_transport,
        artifact_store=artifact_store,
        artifact_key=artifact_key,
    )
    baseline = await build_candidate_from_github(
        identity_connection,
        **build_args,
        idempotency_key=uuid5(build_idempotency_key, "astro-baseline"),
        **(
            {"nextjs_recipe": {}} if nextjs else {"front_matter_recipe": {}} if front_matter else {}
        ),
    )
    if nextjs and baseline.exit_class != "passed":
        raise TechnicalRecipeUnavailable(
            baseline.unavailable_reason or "NEXT_OFFLINE_BUILD_UNAVAILABLE_NETWORK_DISABLED"
        )
    candidate = await build_candidate_from_github(
        identity_connection,
        **build_args,
        idempotency_key=build_idempotency_key,
        patch=patch.patch,
        approved_paths=frozenset({patch.path}),
        **(
            {"nextjs_recipe": dict(edit, recipe_key=recipe_key)}
            if nextjs
            else {"front_matter_recipe": dict(edit, recipe_key=recipe_key)}
            if front_matter
            else {"astro_recipe": dict(edit, recipe_key=recipe_key)}
        ),
    )
    if nextjs and candidate.exit_class != "passed":
        raise TechnicalRecipeUnavailable(
            candidate.unavailable_reason or "NEXT_OFFLINE_BUILD_UNAVAILABLE_NETWORK_DISABLED"
        )
    # Use the configured artifact root, including nested outDir, rather than assuming dist/.
    urls = {}
    for page in expected_by_page:
        if not page.startswith(root + "/") or not page.endswith(".html"):
            raise TechnicalRecipeUnavailable("ASTRO_PAGE_MAPPING_UNAVAILABLE")
        route = page[len(root) + 1 :]
        route = (
            route[:-10]
            if route.endswith("index.html")
            else route.removesuffix(".html")
            if nextjs
            else route
        )
        urls[page] = evidence["site_origin"].rstrip("/") + "/" + route
        if urlsplit(urls[page]).netloc != urlsplit(evidence["site_origin"]).netloc:
            raise TechnicalRecipeUnavailable("ASTRO_PAGE_MAPPING_UNAVAILABLE")
    matching = [page for page, url in urls.items() if url == evidence["page_url"]]
    if (
        len(matching) != 1
        or hashlib.sha256(dict(baseline.built_html).get(matching[0], "").encode("utf8")).hexdigest()
        != evidence["page_body_sha256"]
    ):
        raise TechnicalRecipeUnavailable("ASTRO_CRAWL_BUILD_DRIFT")
    impact = verify_impact(
        files=files,
        path=patch.path,
        recipe_key=recipe_key,
        baseline=baseline,
        candidate=candidate,
        expected_by_page=expected_by_page,
        page_urls=urls,
        image_src=image_src,
    )
    manifest = {
        "schema_version": 1,
        "framework": extension.framework,
        "recipe_key": recipe_key,
        "site_id": str(site_id),
        "extension_id": str(extension_id),
        "build_id": str(candidate.id),
        "audit_report_id": str(report_id),
        "finding_id": str(finding_id),
        "recipe_release_id": str(recipe_release_id),
        "release_content_hash": release.content_hash,
        "base_sha": candidate.base_sha,
        "patch_sha256": candidate.patch_sha256,
        "source_path": patch.path,
        "source_sha256": hashlib.sha256(patch.before).hexdigest(),
        "result_sha256": hashlib.sha256(patch.after).hexdigest(),
        "patch": {
            "offset": patch.offset,
            "before": patch.before_fragment,
            "after": patch.after_fragment,
        },
        "evidence": {
            "manifest_id": evidence["manifest_id"],
            "manifest_sha256": evidence["manifest_sha256"],
            "page_id": evidence["page_id"],
            "page_url": evidence["page_url"],
            "site_origin": evidence["site_origin"],
            "finding": evidence["finding"],
        },
        "build_receipt": {
            "toolchain": candidate.toolchain,
            "command": candidate.command,
            "exit_class": candidate.exit_class,
            "logs_sha256": candidate.logs_sha256,
            "artifacts": [{"path": p, "sha256": d, "size": n} for p, d, n in candidate.artifacts],
        },
        "built_impact": {
            "baseline_build_id": str(baseline.id),
            "lockfile_sha256": candidate.lockfile_sha256,
            "page_count": len(impact.pages),
            "pages": list(impact.pages),
            "scope_sha256": hashlib.sha256(impact.canonical_scope).hexdigest(),
            "samples": list(impact.samples),
        },
        "expected_impact": f"{len(impact.pages)} built pages change; exact field assertions pass; "
        "all other artifacts unchanged.",
        "recovery_plan": patch.recovery_plan,
        "approval_class": impact.approval_class,
        "autonomy_eligible": False,
        "claim_review_required": True,
        "model_draft": None,
    }
    if front_matter or nextjs:
        manifest["content_adapter"] = adapter
    canonical = canonicalize_proposal_manifest(manifest)
    with _clean_transaction(identity_connection):
        row = identity_connection.execute(
            "SELECT * FROM control."
            + (
                "seal_nextjs_recipe_revision"
                if nextjs
                else "seal_front_matter_recipe_revision"
                if front_matter
                else "seal_astro_recipe_revision"
            )
            + "("
            + ",".join(["%s"] * 17)
            + ")",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(current_recovery_generation),
                uuid5(NAMESPACE_URL, f"{adapter}-recipe:{site_id}:{idempotency_key}"),
                extension_id,
                candidate.id,
                report_id,
                finding_id,
                recipe_release_id,
                bytes.fromhex(release.content_hash),
                idempotency_key,
                canonical,
                hashlib.sha256(canonical).digest(),
                baseline.id,
                impact.canonical_scope,
                built_html_payload(baseline.built_html),
                built_html_payload(candidate.built_html),
            ),
        ).fetchone()
    if row is None or row[3] != "sealed":
        raise TechnicalRecipeUnavailable("ASTRO_SEAL_UNAVAILABLE")
    return SealedTechnicalRevision(row[0], bytes(row[1]).hex(), candidate.id, row[2])
