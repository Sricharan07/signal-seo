"""Owner-approved, journaled and reconciled one-commit GitHub PR delivery."""

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

import httpx2
import rfc8785
from psycopg import Connection

from signal_core.astro_recipes import ASTRO_RECIPES, astro_recipe_release_manifest
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.candidate_build_service import read_candidate_build
from signal_core.candidate_recipe_inbox import read_authenticated_candidate_recipe_inbox
from signal_core.database import _clean_transaction
from signal_core.front_matter_recipes import (
    FRONT_MATTER_RECIPES,
    front_matter_recipe_release_manifest,
)
from signal_core.github_app import GitHubAppProtocolError, checkout_github_repository
from signal_core.github_pr_extension import (
    GitHubPrExtensionUnavailable,
    inspect_current_github_pr_extension,
    read_github_pr_extension,
)
from signal_core.github_pr_patch import GitHubPrPatch, GitHubPrPatchRejected, plan_github_pr_patch
from signal_core.github_pr_provider import (
    GitHubOpenedPullRequest,
    GitHubPrProvider,
    GitHubPrProviderError,
    GitHubWriteEgressTransport,
)
from signal_core.github_read_binding import (
    GitHubSharedEgressTransport,
    OpenBaoGitHubAppCredential,
    read_github_read_binding,
)
from signal_core.nextjs_recipes import NEXTJS_RECIPES, nextjs_recipe_release_manifest
from signal_core.recipe_releases import RecipeReleaseUnavailable, get_reviewed_recipe_release
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import InvalidOpaqueSessionToken, hash_session_token
from signal_core.shared_egress import SharedEgressProvider
from signal_core.structured_data_recipe import RECIPE_KEY as GROUNDED_STRUCTURED_RECIPE
from signal_core.structured_data_recipe import structured_data_release_manifest
from signal_core.technical_seo_recipes import RECIPE_FINDING_KEYS, technical_recipe_release_manifest
from signal_core.weekly_delivery_authority import DispatchAuthority, WeeklyDeliveryConnection
from signal_core.write_intent_journal import WriteIntentJournal, WriteIntentRecord


class GitHubPrDeliveryUnavailable(Exception):
    """The operation is ineligible, unresolved, or blocked without a new write."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GitHubPrOperation:
    id: UUID
    revision_id: UUID
    revision_sha256: str
    branch_name: str
    state: str
    step: str
    base_sha: str
    expected_tree_sha: str | None
    expected_commit_sha: str | None
    created_at: datetime
    pr_number: int | None = None
    pr_url: str | None = None
    journal_generation: UUID | None = None
    journal_position: int | None = None
    journal_body_hash: str | None = None
    updated_at: datetime | None = None
    authority_kind: str | None = None
    authority_id: UUID | None = None
    authority_owner_user_id: UUID | None = None
    authority_decision_channel: str | None = None


def _status(outcome: object, success: str) -> None:
    if outcome != success:
        raise GitHubPrDeliveryUnavailable(str(outcome))


def _read_operation(
    connection: Connection, token_hash: bytes, site_id: UUID, generation: str, operation_id: UUID
) -> GitHubPrOperation:
    with _clean_transaction(connection):
        rows = connection.execute(
            "SELECT * FROM control.read_authenticated_github_pr_operations(%s,%s,%s)",
            (token_hash, site_id, generation),
        ).fetchall()
    if not rows or rows[0][-1] not in {"found", "not_found"}:
        raise GitHubPrDeliveryUnavailable("PR_OPERATION_UNAVAILABLE")
    matches = [row for row in rows if row[0] == operation_id and row[-1] == "found"]
    if len(matches) != 1:
        raise GitHubPrDeliveryUnavailable("PR_OPERATION_UNAVAILABLE")
    row = matches[0]
    return GitHubPrOperation(
        row[0],
        row[1],
        row[2],
        row[3],
        row[7],
        row[8],
        row[4],
        row[6],
        row[5],
        row[14],
        row[9],
        row[10],
        row[11],
        row[12],
        row[13],
        row[15],
    )


def read_authenticated_github_pr_operations(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: UUID,
    current_recovery_generation: object,
) -> tuple[GitHubPrOperation, ...]:
    try:
        token_hash = hash_session_token(session_token)
    except (InvalidOpaqueSessionToken, TypeError):
        raise InvalidSession() from None
    generation = validate_recovery_generation(current_recovery_generation)
    with _clean_transaction(connection):
        rows = connection.execute(
            "SELECT * FROM control.read_authenticated_github_pr_operations(%s,%s,%s)",
            (token_hash, requested_site_id, generation),
        ).fetchall()
    if rows and rows[0][-1] == "invalid_session":
        raise InvalidSession()
    if rows and rows[0][-1] == "authorization_denied":
        raise AuthorizationDenied()
    if not rows or rows[0][-1] not in {"found", "not_found"}:
        raise GitHubPrDeliveryUnavailable("PR_OPERATION_READ_UNAVAILABLE")
    if rows[0][-1] == "not_found":
        return ()
    if len(rows) > 50 or any(row[-1] != "found" for row in rows):
        raise GitHubPrDeliveryUnavailable("PR_OPERATION_READ_INVALID")
    operations = tuple(
        GitHubPrOperation(
            row[0],
            row[1],
            row[2],
            row[3],
            row[7],
            row[8],
            row[4],
            row[6],
            row[5],
            row[14],
            row[9],
            row[10],
            row[11],
            row[12],
            row[13],
            row[15],
        )
        for row in rows
    )
    with _clean_transaction(connection):
        authorities = connection.execute(
            "SELECT * FROM control.read_github_pr_operation_authorities(%s,%s,%s)",
            (token_hash, requested_site_id, generation),
        ).fetchall()
    by_operation = {row[0]: row[1:] for row in authorities}
    if len(by_operation) != len(authorities) or any(
        item.id not in by_operation for item in operations
    ):
        raise GitHubPrDeliveryUnavailable("PR_AUTHORITY_EVIDENCE_UNAVAILABLE")
    return tuple(
        replace(
            item,
            authority_kind=by_operation[item.id][0],
            authority_id=by_operation[item.id][1],
            authority_owner_user_id=by_operation[item.id][2],
            authority_decision_channel=by_operation[item.id][3],
        )
        for item in operations
    )


def _call_status(connection: Connection, function: str, *args: object) -> str:
    approved = {
        "acknowledge_github_pr_intent",
        "bind_github_pr_operation_tree",
        "github_pr_dispatch_permit",
        "begin_github_pr_step",
        "finish_github_pr_step",
        "reconcile_github_pr_step",
    }
    if function not in approved:
        raise ValueError("Unsupported operation transition.")
    placeholders = ",".join(["%s"] * len(args))
    with _clean_transaction(connection):
        row = connection.execute(f"SELECT control.{function}({placeholders})", args).fetchone()
    if row is None or not isinstance(row[0], str):
        raise GitHubPrDeliveryUnavailable("PR_OPERATION_STATE_UNAVAILABLE")
    return row[0]


def _pr_body(manifest: dict, revision_sha256: str, operation_id: UUID, patch: GitHubPrPatch) -> str:
    if manifest.get("work_type") in {"new_article", "content_refresh"}:
        flagged = sum(bool(s["reasons"]) for s in manifest["grounding"]["sentences"])
        return (
            f"Owner-reviewed article: `{manifest['work_type']}`\n"
            f"Draft: `{manifest['draft_id']}`\n"
            f"Source path: `{patch.path}`\n"
            f"Base commit: `{manifest['base_sha']}`\n"
            f"Candidate tree: `{patch.tree_sha}`\n"
            f"Patch digest: `{patch.patch_sha256}`\n"
            f"Build log digest: `{manifest['build_receipt']['logs_sha256']}`\n"
            f"Grounding: {manifest['grounding']['state']}; "
            f"flagged sentences: {flagged}\n"
            f"Originality: {rfc8785.dumps(manifest['originality']).decode()}\n"
            f"Expected impact: {manifest['expected_impact']}\n"
            "Risk: one content file; deployment is external and unverified. No article autonomy.\n"
            f"Recovery: {manifest['recovery_plan']} No deletion or unpublish authority.\n"
            f"Signal revision: `{revision_sha256}`\nSignal operation: `{operation_id}`\n"
        )
    evidence = manifest["evidence"]
    finding = evidence["finding"]
    body = (
        f"Technical SEO finding: {finding['key']}\n\n"
        f"Evidence: {evidence['page_url']}\n"
        f"Finding ID: {manifest['finding_id']}\n"
        f"Source path: `{patch.path}`\n"
        f"Base commit: `{manifest['base_sha']}`\n"
        f"Candidate tree: `{patch.tree_sha}`\n"
        f"Patch digest: `{patch.patch_sha256}`\n"
        f"Build log digest: `{manifest['build_receipt']['logs_sha256']}`\n\n"
        f"Expected impact: {manifest['expected_impact']}\n\n"
        f"Risk: one source file changes; deployment remains external and unverified.\n\n"
        f"Recovery: {manifest['recovery_plan']} Revert by a separate pull request using "
        "the inverse patch only against the recorded candidate result; compare the current "
        "target with the base and candidate first. Any overlapping later edit is a conflict "
        "for owner review, never an overwrite.\n\n"
        f"Signal revision: `{revision_sha256}`\n"
        f"Signal operation: `{operation_id}`\n"
    )
    if manifest.get("framework") == "astro" or manifest.get("content_adapter") in {
        "front_matter",
        "nextjs_metadata",
    }:
        impact = manifest["built_impact"]
        body += (
            f"\nBuilt-content approval class: `{manifest['approval_class']}`\n"
            f"Exact built-page impact: {impact['page_count']} pages\n"
            f"Built scope SHA-256: `{impact['scope_sha256']}`\n"
            "Owner Inbox and fresh MFA only; no standing authorization.\n"
        )
    if len(body.encode("utf-8")) > 8192:
        raise GitHubPrDeliveryUnavailable("PR_BODY_TOO_LARGE")
    return body


def _recipe_key(manifest: dict) -> str:
    if manifest.get("content_adapter") == "nextjs_metadata":
        key = manifest.get("recipe_key")
        if (
            key in NEXTJS_RECIPES
            and manifest.get("evidence", {}).get("finding", {}).get("key")
            in RECIPE_FINDING_KEYS[NEXTJS_RECIPES[key]]
        ):
            return key
        raise GitHubPrDeliveryUnavailable("PR_RECIPE_INVALID")
    if manifest.get("content_adapter") == "front_matter":
        key = manifest.get("recipe_key")
        if (
            key in FRONT_MATTER_RECIPES
            and manifest.get("evidence", {}).get("finding", {}).get("key")
            in RECIPE_FINDING_KEYS[FRONT_MATTER_RECIPES[key]]
        ):
            return key
        raise GitHubPrDeliveryUnavailable("PR_RECIPE_INVALID")
    if manifest.get("framework") == "astro":
        key = manifest.get("recipe_key")
        if (
            key in ASTRO_RECIPES
            and manifest.get("evidence", {}).get("finding", {}).get("key")
            in RECIPE_FINDING_KEYS[ASTRO_RECIPES[key]]
        ):
            return key
        raise GitHubPrDeliveryUnavailable("PR_RECIPE_INVALID")
    try:
        finding_key = manifest["evidence"]["finding"]["key"]
    except (KeyError, TypeError):
        raise GitHubPrDeliveryUnavailable("PR_EVIDENCE_INVALID") from None
    structured = manifest.get("structured_data")
    internal = manifest.get("internal_link")
    if internal is not None:
        if (
            isinstance(internal, dict)
            and internal.get("recipe_key") == "technical_internal_link_add"
            and internal.get("autonomy_eligible") is False
            and finding_key == "links.internal.add"
            and manifest.get("approval_class") == "owner_review"
            and manifest.get("audit_report_id") is None
            and structured is None
        ):
            return "technical_internal_link_add"
        raise GitHubPrDeliveryUnavailable("PR_RECIPE_INVALID")
    if structured is not None:
        if (
            isinstance(structured, dict)
            and structured.get("recipe_key") == GROUNDED_STRUCTURED_RECIPE
            and structured.get("autonomy_eligible") is False
            and manifest.get("approval_class") == "owner_review"
            and any(finding_key in findings for findings in RECIPE_FINDING_KEYS.values())
        ):
            return GROUNDED_STRUCTURED_RECIPE
        raise GitHubPrDeliveryUnavailable("PR_RECIPE_INVALID")
    if finding_key == "indexnow.key.required" and manifest.get("audit_report_id") is None:
        return "technical_indexnow_key"
    matching = [key for key, findings in RECIPE_FINDING_KEYS.items() if finding_key in findings]
    if len(matching) != 1:
        raise GitHubPrDeliveryUnavailable("PR_RECIPE_INVALID")
    return matching[0]


def _validate_technical_release(connection, item, manifest):
    recipe_key = _recipe_key(manifest)
    try:
        release = get_reviewed_recipe_release(
            connection, recipe_key=recipe_key, release_id=item.recipe_release_id
        )
        expected = (
            structured_data_release_manifest(item.recipe_release_id, version=str(release.version))
            if recipe_key == GROUNDED_STRUCTURED_RECIPE
            else (
                nextjs_recipe_release_manifest
                if recipe_key in NEXTJS_RECIPES
                else front_matter_recipe_release_manifest
                if recipe_key in FRONT_MATTER_RECIPES
                else astro_recipe_release_manifest
                if recipe_key in ASTRO_RECIPES
                else technical_recipe_release_manifest
            )(recipe_key, item.recipe_release_id, version=str(release.version))
        )
        if release.content_hash != item.release_content_hash or release.manifest != expected:
            raise RecipeReleaseUnavailable("Recipe contract changed.")
    except RecipeReleaseUnavailable:
        raise GitHubPrDeliveryUnavailable("PR_RELEASE_INACTIVE") from None


def _intent(
    *,
    operation_id: UUID,
    site_id: UUID,
    revision_id: UUID,
    revision_sha256: str,
    decision_id: UUID,
    repository_id: int,
    installation_id: int,
    manifest: dict,
    authority_kind: str | None = None,
    decision_channel: str | None = None,
) -> tuple[str, WriteIntentRecord]:
    if manifest.get("work_type") in {"new_article", "content_refresh"}:
        change = manifest["changed_files"][0]
        manifest = {
            **manifest,
            "source_path": change["path"],
            "source_sha256": change["source_sha256"],
            "result_sha256": change["result_sha256"],
        }
    recovery_plan = {
        "schema_version": 1,
        "strategy": "inverse_patch_three_way",
        "conflict_action": "owner_review_no_overwrite",
        "delivery": "separate_pull_request_only",
        "base_sha": manifest["base_sha"],
        "source_path": manifest["source_path"],
        "before_sha256": manifest["source_sha256"],
        "after_sha256": manifest["result_sha256"],
        "sealed_instruction": manifest["recovery_plan"],
    }
    recovery_hash = hashlib.sha256(rfc8785.dumps(recovery_plan)).hexdigest()
    body = {
        "schema_version": 1,
        "operation_id": str(operation_id),
        "site_id": str(site_id),
        "revision_id": str(revision_id),
        "revision_sha256": revision_sha256,
        "decision_id": str(decision_id),
        "repository_id": repository_id,
        "installation_id": installation_id,
        "base_sha": manifest["base_sha"],
        "patch_sha256": manifest["patch_sha256"],
        "source_path": manifest["source_path"],
        "branch_name": f"signal/{operation_id.hex}",
        "recovery_plan_sha256": recovery_hash,
    }
    if authority_kind is not None:
        body["authority_kind"] = authority_kind
        body["authority_id"] = str(decision_id)
        body["decision_channel"] = decision_channel
    digest = hashlib.sha256(rfc8785.dumps(body)).hexdigest()
    return digest, WriteIntentRecord(
        operation_id,
        site_id,
        repository_id,
        revision_sha256,
        digest,
        manifest["base_sha"],
        manifest["patch_sha256"],
        body["branch_name"],
        recovery_hash,
    )


async def open_approved_github_pr(
    identity_connection: Connection,
    release_connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    site_id: UUID,
    revision_id: UUID,
    expected_revision_sha256: str,
    operation_id: UUID,
    worker_id: UUID,
    journal: WriteIntentJournal,
    credential: OpenBaoGitHubAppCredential,
    github_read_transport: GitHubSharedEgressTransport,
    github_write_egress: SharedEgressProvider,
    openbao_transport: httpx2.AsyncBaseTransport | None = None,
    artifact_store=None,
    artifact_key=None,
) -> GitHubPrOperation:
    """Run one durable operation. Unknown write effects are only read-reconciled."""
    if not all(
        isinstance(value, UUID) for value in (site_id, revision_id, operation_id, worker_id)
    ):
        raise ValueError("GitHub PR operation identity is invalid.")
    if not isinstance(journal, WriteIntentJournal):
        raise GitHubPrDeliveryUnavailable("PR_JOURNAL_UNAVAILABLE")
    if not isinstance(github_read_transport, GitHubSharedEgressTransport):
        raise GitHubPrDeliveryUnavailable("PR_EGRESS_UNAVAILABLE")
    if (
        not isinstance(github_write_egress, SharedEgressProvider)
        or github_write_egress.purpose != "connector"
        or github_write_egress.run.site_id != site_id
        or github_read_transport.provider.run.site_id != site_id
    ):
        raise GitHubPrDeliveryUnavailable("PR_EGRESS_SCOPE_INVALID")
    generation = validate_recovery_generation(current_recovery_generation)
    token_hash = hash_session_token(session_token)
    inbox = read_authenticated_candidate_recipe_inbox(
        identity_connection,
        session_token=session_token,
        requested_site_id=site_id,
        current_recovery_generation=generation,
    )
    matching = [item for item in inbox if item.revision_id == revision_id]
    editorial_authority = None
    if not matching and not isinstance(identity_connection, WeeklyDeliveryConnection):
        with _clean_transaction(identity_connection):
            row = identity_connection.execute(
                "SELECT control.read_content_delivery_candidate(%s,%s,%s,%s)",
                (token_hash, site_id, generation, revision_id),
            ).fetchone()
        content = row[0] if row else None
        if content:
            manifest = content["manifest"]
            editorial_authority = DispatchAuthority(
                "owner_editorial",
                UUID(content["authority_id"]),
                UUID(content["owner_user_id"]),
                "dashboard",
            )
            matching = [
                SimpleNamespace(
                    revision_id=revision_id,
                    revision_sha256=content["revision_sha256"],
                    manifest=manifest,
                    base_sha=manifest["base_sha"],
                    patch_sha256=manifest["patch_sha256"],
                    decision_id=editorial_authority.id,
                    review_status="approved",
                    decision_channel="dashboard",
                )
            ]
    if len(matching) != 1 or (
        not isinstance(identity_connection, WeeklyDeliveryConnection)
        and matching[0].review_status != "approved"
    ):
        raise GitHubPrDeliveryUnavailable("PR_OWNER_DECISION_UNAVAILABLE")
    item = matching[0]
    authority = editorial_authority
    if isinstance(identity_connection, WeeklyDeliveryConnection):
        authority = identity_connection.dispatch_authority(
            revision_id=revision_id,
            revision_sha256=expected_revision_sha256,
            operation_id=operation_id,
        )
    decision_id = authority.id if authority is not None else item.decision_id
    if item.revision_sha256 != expected_revision_sha256 or decision_id is None:
        raise GitHubPrDeliveryUnavailable("PR_REVISION_STALE")
    manifest = item.manifest
    canonical = rfc8785.dumps(manifest)
    if hashlib.sha256(canonical).hexdigest() != item.revision_sha256:
        raise GitHubPrDeliveryUnavailable("PR_MANIFEST_INVALID")
    if manifest.get("site_id") != str(site_id) or manifest.get("base_sha") != item.base_sha:
        raise GitHubPrDeliveryUnavailable("PR_MANIFEST_INVALID")
    if editorial_authority is None:
        _validate_technical_release(release_connection, item, manifest)
    build = read_candidate_build(
        identity_connection,
        session_token=session_token,
        current_recovery_generation=generation,
        site_id=site_id,
        build_id=UUID(manifest["build_id"]),
        include_dependencies=manifest.get("framework") == "astro"
        or manifest.get("content_adapter") in {"front_matter", "nextjs_metadata"},
        include_html=manifest.get("framework") == "astro"
        or manifest.get("content_adapter") in {"front_matter", "nextjs_metadata"},
        artifact_store=artifact_store,
        artifact_key=artifact_key,
    )
    expected_artifacts = tuple(
        tuple(entry)
        if editorial_authority is not None
        else (entry["path"], entry["sha256"], entry["size"])
        for entry in manifest["build_receipt"]["artifacts"]
    )
    if (
        build.status != "completed"
        or build.exit_class != "passed"
        or build.base_sha != item.base_sha
        or build.patch_sha256 != item.patch_sha256
        or build.logs_sha256 != manifest["build_receipt"]["logs_sha256"]
        or build.artifacts != expected_artifacts
    ):
        raise GitHubPrDeliveryUnavailable("PR_BUILD_RECEIPT_INVALID")
    if (
        manifest.get("framework") == "astro"
        or manifest.get("content_adapter") in {"front_matter", "nextjs_metadata"}
    ) and (
        authority is not None
        or item.decision_channel != "dashboard"
        or build.lockfile_sha256 != manifest["built_impact"]["lockfile_sha256"]
    ):
        raise GitHubPrDeliveryUnavailable("PR_ASTRO_OWNER_INBOX_REQUIRED")
    extension = read_github_pr_extension(
        identity_connection,
        session_token=session_token,
        current_recovery_generation=generation,
        site_id=site_id,
        extension_id=UUID(manifest["extension_id"]),
    )
    binding = read_github_read_binding(
        identity_connection,
        session_token=session_token,
        current_recovery_generation=generation,
        site_id=site_id,
        binding_id=extension.binding_id,
    )
    if (
        binding.status != "active"
        or extension.status != "observed"
        or not extension.candidate_compatible
        or extension.base_sha != item.base_sha
        or extension.repository_id != binding.repository_id
    ):
        raise GitHubPrDeliveryUnavailable("PR_BINDING_STALE")
    intent_sha256, intent_record = _intent(
        operation_id=operation_id,
        site_id=site_id,
        revision_id=revision_id,
        revision_sha256=item.revision_sha256,
        decision_id=decision_id,
        repository_id=binding.repository_id,
        installation_id=binding.target.installation_id,
        manifest=manifest,
        authority_kind=authority.kind if authority is not None else None,
        decision_channel=authority.decision_channel if authority is not None else None,
    )
    with _clean_transaction(identity_connection):
        row = identity_connection.execute(
            "SELECT * FROM control.prepare_github_pr_operation(%s,%s,%s,%s,%s,%s,%s)",
            (
                token_hash,
                site_id,
                generation,
                revision_id,
                bytes.fromhex(item.revision_sha256),
                operation_id,
                bytes.fromhex(intent_sha256),
            ),
        ).fetchone()
    if row is None or row[5] != "prepared" or bytes(row[3]) != canonical:
        raise GitHubPrDeliveryUnavailable(str(row[5]) if row else "PR_PREPARE_UNAVAILABLE")
    if row[1] == "opened":
        return _read_operation(identity_connection, token_hash, site_id, generation, operation_id)
    if row[1] == "blocked":
        raise GitHubPrDeliveryUnavailable("PR_OPERATION_BLOCKED")
    receipt = journal.append(intent_record)
    _status(
        _call_status(
            identity_connection,
            "acknowledge_github_pr_intent",
            token_hash,
            site_id,
            generation,
            operation_id,
            receipt.generation,
            receipt.position,
            bytes.fromhex(receipt.body_hash),
        ),
        "acknowledged",
    )
    try:
        current = await inspect_current_github_pr_extension(
            identity_connection,
            session_token=session_token,
            current_recovery_generation=generation,
            site_id=site_id,
            extension_id=extension.id,
            credential=credential,
            github_transport=github_read_transport,
            openbao_transport=openbao_transport,
        )
        credentials = await credential.credentials(transport=openbao_transport)
        checkout = await checkout_github_repository(
            credentials=credentials, target=binding.target, transport=github_read_transport
        )
        if current.repository_id != binding.repository_id or current.base_sha != item.base_sha:
            raise GitHubPrDeliveryUnavailable("PR_BASE_DRIFT")
        if any(path.startswith(".github/workflows/") for path, _ in checkout.files):
            raise GitHubPrDeliveryUnavailable("PR_WORKFLOW_CERTIFICATION_REQUIRED")
        patch = plan_github_pr_patch(
            manifest_bytes=canonical,
            revision_sha256=item.revision_sha256,
            operation_id=operation_id,
            created_at=row[4],
            extension=extension,
            checkout=checkout,
        )
    except (GitHubPrExtensionUnavailable, GitHubAppProtocolError, GitHubPrPatchRejected) as error:
        raise GitHubPrDeliveryUnavailable(getattr(error, "code", "PR_PATCH_REJECTED")) from None
    _status(
        _call_status(
            identity_connection,
            "bind_github_pr_operation_tree",
            token_hash,
            site_id,
            generation,
            operation_id,
            patch.tree_sha,
            patch.commit_sha,
        ),
        "bound",
    )
    with _clean_transaction(identity_connection):
        claim = identity_connection.execute(
            "SELECT * FROM control.claim_github_pr_operation(%s,%s,%s,%s,%s)",
            (token_hash, site_id, generation, operation_id, worker_id),
        ).fetchone()
    if claim is None or claim[3] != "claimed":
        raise GitHubPrDeliveryUnavailable(str(claim[3]) if claim else "PR_LEASE_UNAVAILABLE")
    fence = claim[0]
    step_now = "tree"

    def permit(step: str) -> None:
        if step != step_now:
            raise GitHubPrDeliveryUnavailable("PR_STEP_MISMATCH")
        result = _call_status(
            identity_connection,
            "github_pr_dispatch_permit",
            token_hash,
            site_id,
            generation,
            operation_id,
            worker_id,
            fence,
            step,
        )
        _status(result, "permitted")

    body = _pr_body(manifest, item.revision_sha256, operation_id, patch)
    decision_channel = (
        authority.decision_channel if authority is not None else item.decision_channel
    )
    if decision_channel is not None:
        body += f"\nOwner decision channel: `{decision_channel}`\n"
    if authority is not None:
        body += (
            f"\nAuthorization kind: `{authority.kind}`\n"
            f"Authorization record: `{authority.id}`\n"
            f"Grant or decision owner: `{authority.owner_user_id}`\n"
        )
    if editorial_authority is not None:
        body += f"Candidate: `{revision_id}`\n"
    if len(body.encode("utf-8")) > 8192:
        raise GitHubPrDeliveryUnavailable("PR_BODY_TOO_LARGE")
    if isinstance(identity_connection, WeeklyDeliveryConnection):
        identity_connection.record_execution(
            operation_id=operation_id,
            revision_sha256=item.revision_sha256,
            base_sha=item.base_sha,
            base_tree_sha=extension.tree_sha,
            patch=patch,
            body=body,
            authority=authority,
        )
    transport = GitHubWriteEgressTransport(
        github_write_egress,
        binding.target,
        permit,
        operation_id=operation_id,
        patch=patch,
        base_sha=item.base_sha,
        base_tree_sha=extension.tree_sha,
        pr_body=body,
    )
    provider = GitHubPrProvider(credentials=credentials, target=binding.target, transport=transport)
    await provider.acquire_token()

    for step in ("tree", "commit", "branch", "pr"):
        step_now = step
        operation = _read_operation(
            identity_connection, token_hash, site_id, generation, operation_id
        )
        if operation.state == "opened":
            return operation
        if operation.step != step:
            continue
        if operation.state in {"dispatching", "outcome_unknown"}:
            confirmed = await _reconcile_step(
                provider, step, operation.branch_name, operation.base_sha, patch, body
            )
            if confirmed is None:
                raise GitHubPrDeliveryUnavailable("OUTCOME_UNKNOWN")
            evidence, pr = confirmed
            _status(
                _call_status(
                    identity_connection,
                    "reconcile_github_pr_step",
                    token_hash,
                    site_id,
                    generation,
                    operation_id,
                    worker_id,
                    fence,
                    step,
                    bytes.fromhex(evidence),
                    pr.number if pr else None,
                    pr.url if pr else None,
                ),
                "reconciled",
            )
            continue
        try:
            current = await inspect_current_github_pr_extension(
                identity_connection,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
                extension_id=extension.id,
                credential=credential,
                github_transport=github_read_transport,
                openbao_transport=openbao_transport,
            )
        except GitHubPrExtensionUnavailable as error:
            raise GitHubPrDeliveryUnavailable(error.code) from None
        if current.base_sha != operation.base_sha or current.repository_id != binding.repository_id:
            raise GitHubPrDeliveryUnavailable("PR_BASE_DRIFT")
        if step in {"branch", "pr"}:
            existing_head = await provider.get_branch(operation.branch_name)
            if step == "branch" and existing_head is not None:
                raise GitHubPrDeliveryUnavailable("PR_BRANCH_ALREADY_EXISTS")
            if step == "pr" and existing_head != patch.commit_sha:
                raise GitHubPrDeliveryUnavailable("PR_BRANCH_DRIFT")
            existing_prs = await provider.list_pulls(operation.branch_name)
            if existing_prs:
                raise GitHubPrDeliveryUnavailable("PR_ALREADY_EXISTS")
        _status(
            _call_status(
                identity_connection,
                "begin_github_pr_step",
                token_hash,
                site_id,
                generation,
                operation_id,
                worker_id,
                fence,
                step,
            ),
            "dispatching",
        )
        try:
            if step == "tree":
                await provider.create_tree(base_tree_sha=extension.tree_sha, patch=patch)
                pr = None
            elif step == "commit":
                await provider.create_commit(base_sha=operation.base_sha, patch=patch)
                pr = None
            elif step == "branch":
                await provider.create_branch(operation.branch_name, patch.commit_sha)
                pr = None
            else:
                pr = await provider.create_pr(
                    branch_name=operation.branch_name,
                    body=body,
                    expected_head=patch.commit_sha,
                    expected_base=operation.base_sha,
                )
        except GitHubPrProviderError as error:
            result = "outcome_unknown" if error.ambiguous else "blocked"
            try:
                _call_status(
                    identity_connection,
                    "finish_github_pr_step",
                    token_hash,
                    site_id,
                    generation,
                    operation_id,
                    worker_id,
                    fence,
                    step,
                    result,
                    hashlib.sha256(error.code.encode("ascii")).digest(),
                    None,
                    None,
                )
            except GitHubPrDeliveryUnavailable:
                pass
            raise GitHubPrDeliveryUnavailable(
                "OUTCOME_UNKNOWN" if error.ambiguous else error.code
            ) from None
        evidence = hashlib.sha256(
            rfc8785.dumps(
                {
                    "step": step,
                    "egress_operation_id": str(transport.last_egress_operation_id),
                    "tree_sha": patch.tree_sha,
                    "commit_sha": patch.commit_sha,
                    "pr_number": pr.number if pr else None,
                }
            )
        ).digest()
        _status(
            _call_status(
                identity_connection,
                "finish_github_pr_step",
                token_hash,
                site_id,
                generation,
                operation_id,
                worker_id,
                fence,
                step,
                "completed",
                evidence,
                pr.number if pr else None,
                pr.url if pr else None,
            ),
            "completed",
        )
    return _read_operation(identity_connection, token_hash, site_id, generation, operation_id)


async def _reconcile_step(
    provider: GitHubPrProvider,
    step: str,
    branch: str,
    base_sha: str,
    patch: GitHubPrPatch,
    body: str,
) -> tuple[str, GitHubOpenedPullRequest | None] | None:
    pr = None
    if step == "tree":
        present = await provider.get_tree(patch.tree_sha)
    elif step == "commit":
        present = await provider.get_commit(patch.commit_sha, base_sha, patch)
    elif step == "branch":
        head = await provider.get_branch(branch)
        if head is not None and head != patch.commit_sha:
            raise GitHubPrDeliveryUnavailable("PR_BRANCH_CONFLICT")
        present = head == patch.commit_sha
    else:
        prs = await provider.list_pulls(branch)
        if len(prs) > 1:
            raise GitHubPrDeliveryUnavailable("PR_CONFLICT")
        if len(prs) == 1:
            pr = provider.parse_existing_pr(prs[0], branch, patch.commit_sha, base_sha, body)
        present = pr is not None
    if not present:
        return None
    digest = hashlib.sha256(
        rfc8785.dumps(
            {
                "step": step,
                "reconciled": True,
                "tree_sha": patch.tree_sha,
                "commit_sha": patch.commit_sha,
                "pr_number": pr.number if pr else None,
            }
        )
    ).hexdigest()
    return digest, pr
