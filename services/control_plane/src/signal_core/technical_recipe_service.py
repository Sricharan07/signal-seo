"""Read-only source preparation and immutable evidence-bound recipe sealing."""

import hashlib
from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx2
import rfc8785
from psycopg import Connection

from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.business_brain import approved_facts
from signal_core.candidate_build import plan_candidate_build
from signal_core.candidate_build_service import (
    CandidateRunner,
    build_candidate_from_github,
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
from signal_core.model_budget import PostgresModelBudget
from signal_core.model_reasoning import CrawlTextTask, MetadataDraftError, OpenAIResponsesAdapter
from signal_core.proposals import canonicalize_proposal_manifest
from signal_core.recipe_releases import (
    RecipeReleaseUnavailable,
    get_reviewed_recipe_release,
)
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import session_token_hasher
from signal_core.structured_data_recipe import (
    RECIPE_KEY as STRUCTURED_RECIPE,
)
from signal_core.structured_data_recipe import (
    assert_structured_build,
    make_structured_data_patch,
    structured_data_release_manifest,
)
from signal_core.technical_seo_recipes import (
    RECIPE_FINDING_KEYS,
    TechnicalRecipeUnavailable,
    make_technical_recipe_patch,
    technical_recipe_release_manifest,
)


@dataclass(frozen=True)
class SealedTechnicalRevision:
    id: UUID
    revision_sha256: str
    build_id: UUID
    replayed: bool


_token_hash = session_token_hasher(InvalidSession)


def _load_evidence(
    connection: Connection,
    session_token: object,
    generation: object,
    site_id: UUID,
    report_id: UUID,
    finding_id: UUID,
) -> dict:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.load_candidate_recipe_evidence(%s,%s,%s,%s,%s)",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(generation),
                report_id,
                finding_id,
            ),
        ).fetchone()
    if row is None or row[1] != "found":
        if row and row[1] in {"invalid_session", "authorization_denied"}:
            raise InvalidSession()
        if row and row[1] == "permission_denied":
            raise AuthorizationDenied()
        raise TechnicalRecipeUnavailable("RECIPE_EVIDENCE_UNAVAILABLE")
    evidence = row[0]
    if not isinstance(evidence, dict) or evidence.get("finding", {}).get("id") != str(finding_id):
        raise TechnicalRecipeUnavailable("RECIPE_EVIDENCE_UNAVAILABLE")
    return evidence


async def seal_technical_recipe_revision(
    identity_connection: Connection,
    release_connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    extension_id: UUID,
    report_id: UUID,
    finding_id: UUID,
    recipe_key: str,
    recipe_release_id: UUID,
    idempotency_key: UUID,
    build_idempotency_key: UUID,
    credential: OpenBaoGitHubAppCredential,
    github_transport: GitHubSharedEgressTransport,
    runner: CandidateRunner,
    model: OpenAIResponsesAdapter | None = None,
    openbao_transport: httpx2.AsyncBaseTransport | None = None,
    structured_data: dict | None = None,
    source_path: str = "index.html",
    brain_connection: Connection | None = None,
    astro_edit: dict | None = None,
    front_matter_edit: dict | None = None,
    nextjs_edit: dict | None = None,
    expected_by_page: dict[str, str] | None = None,
    registry_provider=None,
    registry_cache=None,
    image_src: str | None = None,
    artifact_store=None,
    artifact_key=None,
    visibility_proposal_id: UUID | None = None,
    visibility_proposal_digest: str | None = None,
    visibility_fact_fields: dict | None = None,
) -> SealedTechnicalRevision:
    """No repository write: re-observe, build, then seal the exact review revision."""
    from signal_core.astro_recipe_service import (
        seal_astro_recipe_revision,
        seal_front_matter_recipe_revision,
        seal_nextjs_recipe_revision,
    )
    from signal_core.astro_recipes import ASTRO_RECIPES
    from signal_core.front_matter_recipes import FRONT_MATTER_RECIPES
    from signal_core.nextjs_recipes import NEXTJS_RECIPES

    built_adapters = (
        (
            FRONT_MATTER_RECIPES,
            front_matter_edit,
            seal_front_matter_recipe_revision,
            "FRONT_MATTER_EDIT_UNAVAILABLE",
        ),
        (
            NEXTJS_RECIPES,
            nextjs_edit,
            seal_nextjs_recipe_revision,
            "NEXT_RECIPE_INPUTS_UNAVAILABLE",
        ),
        (ASTRO_RECIPES, astro_edit, seal_astro_recipe_revision, "ASTRO_RECIPE_INPUTS_UNAVAILABLE"),
    )
    selected = next((item for item in built_adapters if recipe_key in item[0]), None)
    if selected:
        _, edit, seal, unavailable = selected
        if not isinstance(edit, dict) or not isinstance(expected_by_page, dict):
            raise TechnicalRecipeUnavailable(unavailable)
        return await seal(
            identity_connection,
            release_connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            site_id=site_id,
            extension_id=extension_id,
            report_id=report_id,
            finding_id=finding_id,
            recipe_key=recipe_key,
            recipe_release_id=recipe_release_id,
            idempotency_key=idempotency_key,
            build_idempotency_key=build_idempotency_key,
            credential=credential,
            github_transport=github_transport,
            runner=runner,
            edit=edit,
            expected_by_page=expected_by_page,
            registry_provider=registry_provider,
            registry_cache=registry_cache,
            image_src=image_src,
            openbao_transport=openbao_transport,
            artifact_store=artifact_store,
            artifact_key=artifact_key,
        )

    grounded = recipe_key == STRUCTURED_RECIPE
    if (recipe_key not in RECIPE_FINDING_KEYS and not grounded) or not all(
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
        raise ValueError("The technical recipe request is invalid.")
    release = get_reviewed_recipe_release(
        release_connection, recipe_key=recipe_key, release_id=recipe_release_id
    )
    expected_release = (
        structured_data_release_manifest(recipe_release_id, version=str(release.version))
        if grounded
        else technical_recipe_release_manifest(
            recipe_key, recipe_release_id, version=str(release.version)
        )
    )
    if release.manifest != expected_release:
        raise RecipeReleaseUnavailable("Technical recipe contract is unavailable.")
    evidence = _load_evidence(
        identity_connection,
        session_token,
        current_recovery_generation,
        site_id,
        report_id,
        finding_id,
    )
    finding = evidence["finding"]
    if not grounded and finding.get("key") not in RECIPE_FINDING_KEYS[recipe_key]:
        raise TechnicalRecipeUnavailable("RECIPE_FINDING_UNAVAILABLE")
    if not isinstance(github_transport, GitHubSharedEgressTransport):
        raise TechnicalRecipeUnavailable("RECIPE_EGRESS_UNAVAILABLE")
    extension = read_github_pr_extension(
        identity_connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
        extension_id=extension_id,
    )
    if extension.framework != "eleventy" or extension.content_format != "html":
        raise TechnicalRecipeUnavailable("RECIPE_FORMAT_UNAVAILABLE")
    try:
        current = await inspect_current_github_pr_extension(
            identity_connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            site_id=site_id,
            extension_id=extension_id,
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
        raise TechnicalRecipeUnavailable(error.code) from None
    if (
        checkout.inventory.snapshot.repository_id != current.repository_id
        or checkout.inventory.snapshot.full_name != current.full_name
        or checkout.inventory.snapshot.base_sha != current.base_sha
        or checkout.inventory.tree_sha != extension.tree_sha
    ):
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_DRIFT")
    drafted_text = None
    draft_receipt = None
    if recipe_key in {"technical_title", "technical_description", "technical_alt"}:
        if model is None:
            raise TechnicalRecipeUnavailable("RECIPE_MODEL_UNAVAILABLE")
        images = evidence.get("missing_alt_images") or []
        task = CrawlTextTask(
            kind={
                "technical_title": "title",
                "technical_description": "description",
                "technical_alt": "alt",
            }[recipe_key],
            page_url=evidence["page_url"],
            page_title=evidence.get("page_title") or "",
            headings=tuple(
                item["text"]
                for item in evidence.get("page_headings", [])
                if isinstance(item, dict) and item.get("level") == 1
            ),
            image_src=images[0] if recipe_key == "technical_alt" and len(images) == 1 else None,
        )
        try:
            if isinstance(model, OpenAIResponsesAdapter):
                model = replace(
                    model,
                    budget=PostgresModelBudget(
                        identity_connection, session_token, current_recovery_generation, site_id
                    ),
                )
                draft = await model.draft_crawl_text(task, operation_id=idempotency_key)
            else:
                draft = await model.draft_crawl_text(task)
        except MetadataDraftError as error:
            raise TechnicalRecipeUnavailable(error.code) from None
        drafted_text = draft.text
        draft_receipt = {
            "task_sha256": hashlib.sha256(rfc8785.dumps(task.packet())).hexdigest(),
            "draft_sha256": hashlib.sha256(drafted_text.encode("utf-8")).hexdigest(),
            "provider_response_id": draft.provider_response_id,
            "model_reported": draft.model_reported,
            "usage": {
                "input_tokens": draft.usage.input_tokens,
                "output_tokens": draft.usage.output_tokens,
                "total_tokens": draft.usage.total_tokens,
                "cached_input_tokens": draft.usage.cached_input_tokens,
            },
        }
    block = None
    if grounded:
        facts = ()
        if isinstance(structured_data, dict) and structured_data.get("@type") in {
            "Organization",
            "Product",
        }:
            if brain_connection is None:
                raise TechnicalRecipeUnavailable("STRUCTURED_DATA_FACT_UNAVAILABLE")
            facts = approved_facts(
                brain_connection,
                session_token=session_token,
                current_recovery_generation=current_recovery_generation,
                site_id=site_id,
            )
        patch, block = make_structured_data_patch(
            evidence=evidence,
            extension=extension,
            checkout=checkout,
            proposal=structured_data,
            source_path=source_path,
            facts=facts,
        )
    else:
        patch = make_technical_recipe_patch(
            recipe_key=recipe_key,
            evidence=evidence,
            extension=extension,
            checkout=checkout,
            drafted_text=drafted_text,
        )
    if (
        len(patch.before_fragment.encode("utf-8")) > 4096
        or len(patch.after_fragment.encode("utf-8")) > 4096
    ):
        raise TechnicalRecipeUnavailable("RECIPE_PATCH_TOO_LARGE")
    expected_plan = plan_candidate_build(
        extension, checkout, patch=patch.patch, approved_paths=frozenset({patch.path})
    )
    baseline = None
    if grounded:
        baseline = await build_candidate_from_github(
            identity_connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            site_id=site_id,
            extension_id=extension_id,
            idempotency_key=uuid5(NAMESPACE_URL, f"structured-baseline:{build_idempotency_key}"),
            credential=credential,
            github_transport=github_transport,
            runner=runner,
            openbao_transport=openbao_transport,
        )
    build = await build_candidate_from_github(
        identity_connection,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
        site_id=site_id,
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
    if build.base_sha != expected_plan.base_sha or build.patch_sha256 != expected_plan.patch_sha256:
        raise TechnicalRecipeUnavailable("RECIPE_SOURCE_DRIFT")
    if grounded:
        if baseline.base_sha != build.base_sha:
            raise TechnicalRecipeUnavailable("RECIPE_SOURCE_DRIFT")
        assert_structured_build(patch, baseline, build, output_path="_site/" + patch.path)
    manifest = {
        "schema_version": 1,
        "site_id": str(site_id),
        "extension_id": str(extension_id),
        "build_id": str(build.id),
        "audit_report_id": str(report_id),
        "finding_id": str(finding_id),
        "recipe_release_id": str(recipe_release_id),
        "release_content_hash": release.content_hash,
        "base_sha": build.base_sha,
        "patch_sha256": build.patch_sha256,
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
            "finding": finding,
        },
        "build_receipt": {
            "toolchain": build.toolchain,
            "command": build.command,
            "exit_class": build.exit_class,
            "logs_sha256": build.logs_sha256,
            "artifacts": [
                {"path": path, "sha256": digest, "size": size}
                for path, digest, size in build.artifacts
            ],
        },
        "expected_impact": patch.expected_impact,
        "recovery_plan": patch.recovery_plan,
        "approval_class": "owner_review",
        "claim_review_required": draft_receipt is not None,
        "model_draft": draft_receipt,
    }
    if grounded:
        manifest["structured_data"] = {
            "recipe_key": STRUCTURED_RECIPE,
            "json_ld": block.document,
            "fact_refs": block.fact_refs,
            "owner_required": block.owner_required,
            "autonomy_eligible": False,
            "output_path": "_site/" + patch.path,
            "baseline_build_id": str(baseline.id),
        }
        manifest["claim_review_required"] = block.owner_required
    if visibility_proposal_id is not None:
        if not grounded or visibility_proposal_digest is None or visibility_fact_fields is None:
            raise TechnicalRecipeUnavailable("VISIBILITY_PROPOSAL_UNAVAILABLE")
        manifest.update(
            visibility_proposal_id=str(visibility_proposal_id),
            visibility_proposal_digest=visibility_proposal_digest,
            visibility_fact_fields=visibility_fact_fields,
        )
    canonical = canonicalize_proposal_manifest(manifest)
    with _clean_transaction(identity_connection):
        row = identity_connection.execute(
            "SELECT * FROM control."
            + (
                "seal_ai_visibility_structured_revision"
                if visibility_proposal_id
                else "seal_structured_data_revision"
                if grounded
                else "seal_candidate_recipe_revision"
            )
            + "("
            + ",".join(["%s"] * (15 if visibility_proposal_id else 13))
            + ")",
            (
                _token_hash(session_token),
                site_id,
                validate_recovery_generation(current_recovery_generation),
                uuid5(NAMESPACE_URL, f"technical-recipe:{site_id}:{idempotency_key}"),
                extension_id,
                build.id,
                report_id,
                finding_id,
                recipe_release_id,
                bytes.fromhex(release.content_hash),
                idempotency_key,
                canonical,
                hashlib.sha256(canonical).digest(),
                *(
                    (visibility_proposal_id, bytes.fromhex(visibility_proposal_digest))
                    if visibility_proposal_id
                    else ()
                ),
            ),
        ).fetchone()
    if row is None or row[3] != "sealed":
        raise TechnicalRecipeUnavailable("RECIPE_SEAL_UNAVAILABLE")
    return SealedTechnicalRevision(row[0], bytes(row[1]).hex(), build.id, row[2])
