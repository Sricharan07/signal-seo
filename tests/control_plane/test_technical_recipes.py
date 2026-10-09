import hashlib
import os
import time
from uuid import UUID, uuid4

import psycopg
import pytest
import rfc8785
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.errors import InsufficientPrivilege
from signal_core.candidate_build import NODE_IMAGE, CandidateBuildPlan
from signal_core.candidate_build_service import (
    dispatch_candidate_build,
    finish_candidate_build,
    prepare_candidate_build,
)
from signal_core.candidate_sandbox import CandidateSandboxOutcome
from signal_core.crawl_audit import analyze_crawl_manifest
from signal_core.recipe_releases import (
    RecipeReleaseUnavailable,
    get_reviewed_recipe_release,
    register_recipe_release,
    register_recipe_signing_key,
    transition_recipe_release,
)
from signal_core.technical_recipe_service import _load_evidence
from signal_core.technical_seo_recipes import (
    RECIPE_FINDING_KEYS,
    TechnicalRecipeUnavailable,
    technical_recipe_release_manifest,
)

from tests.control_plane.test_candidate_builds import _active_extension
from tests.control_plane.test_full_site_crawl import Fetcher, _command, _executor


class TimedFetcher(Fetcher):
    def request(self, outbound, *, policy):
        result = super().request(outbound, policy=policy)
        # The synthetic elapsed time must not place completion in the future.
        time.sleep(result.elapsed_ms / 1000)
        return result


@pytest.fixture
def release_manager():
    with psycopg.connect(os.environ["SIGNAL_TEST_RELEASE_MANAGER_DSN"], autocommit=True) as conn:
        yield conn


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _register_release(
    admin, release_manager, key, *, reviewed, version="1.0.0", approval_class=None
):
    signer = Ed25519PrivateKey.generate()
    signer_id = "test_technical_" + uuid4().hex
    register_recipe_signing_key(
        release_manager,
        key_id=signer_id,
        public_key=signer.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )
    actor_id = uuid4()
    admin.execute(
        "INSERT INTO control.users (id,oidc_issuer,oidc_subject,display_name) "
        "VALUES (%s,'https://identity.example.invalid',%s,'Synthetic reviewer')",
        (actor_id, str(actor_id)),
    )
    release_id = uuid4()
    manifest = technical_recipe_release_manifest(key, release_id, version=version)
    if approval_class is not None:
        manifest["approval_class"] = approval_class
    body = rfc8785.dumps(manifest)
    register_recipe_release(
        release_manager,
        release_id=release_id,
        recipe_key=key,
        version=version,
        canonical_body=body,
        signing_key_id=signer_id,
        signature=signer.sign(body),
        actor_user_id=actor_id,
    )
    if reviewed:
        for before, after in (("DRAFT", "TESTED"), ("TESTED", "REVIEWED")):
            transition_recipe_release(
                release_manager,
                release_id=release_id,
                expected_status=before,
                new_status=after,
                actor_user_id=actor_id,
                reason="Synthetic review",
            )
    return release_id, actor_id


@pytest.mark.parametrize("key", sorted(RECIPE_FINDING_KEYS))
def test_each_technical_recipe_is_registered_through_reviewed_registry(
    admin, api, release_manager, key
):
    release_id, _ = _register_release(admin, release_manager, key, reviewed=True)
    release = get_reviewed_recipe_release(api, recipe_key=key, release_id=release_id)
    assert release.manifest == technical_recipe_release_manifest(key, release_id)
    assert len(release.content_hash) == 64


def test_unreviewed_and_revoked_release_never_dispatches(admin, api, release_manager):
    key = "technical_canonical"
    release_id, actor_id = _register_release(
        admin, release_manager, key, reviewed=False, version="1.0.1"
    )
    with pytest.raises(RecipeReleaseUnavailable):
        get_reviewed_recipe_release(api, recipe_key=key, release_id=release_id)
    for before, after in (("DRAFT", "TESTED"), ("TESTED", "REVIEWED")):
        transition_recipe_release(
            release_manager,
            release_id=release_id,
            expected_status=before,
            new_status=after,
            actor_user_id=actor_id,
            reason="Synthetic review",
        )
    assert get_reviewed_recipe_release(api, recipe_key=key, release_id=release_id)
    transition_recipe_release(
        release_manager,
        release_id=release_id,
        expected_status="REVIEWED",
        new_status="REVOKED",
        actor_user_id=actor_id,
        reason="Synthetic revocation",
    )
    with pytest.raises(RecipeReleaseUnavailable):
        get_reviewed_recipe_release(api, recipe_key=key, release_id=release_id)


@pytest.mark.anyio
@pytest.mark.parametrize("approval_channel", ["dashboard", "slack", "telegram"])
async def test_committed_finding_is_loaded_and_exact_build_is_sealed(
    admin,
    api,
    identity,
    scheduler,
    workflow,
    crawl_ingest,
    crawl_admission,
    release_manager,
    scopes,
    identity_context,
    tmp_path,
    approval_channel,
):
    scope, context, extension = await _active_extension(admin, identity, scopes, identity_context)
    origin = admin.execute(
        "SELECT primary_origin FROM app.sites WHERE tenant_id=%s AND id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone()[0]
    body = b'<html><head></head><body><h1>Signal Guide</h1><img src="guide.png"></body></html>'
    command, run_id = _command(api, scheduler, workflow, scope, "technical-recipe-crawl")
    manifest = _executor(tmp_path, TimedFetcher(origin, page_body=body)).run(
        command, first_run_id=run_id
    )
    report = analyze_crawl_manifest(
        crawl_ingest,
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        manifest_id=UUID(manifest.manifest_id),
    )
    finding = next(item for item in report.findings if item.key == "images.alt.missing")
    evidence = _load_evidence(
        identity,
        context["session_token"],
        context["generation"],
        scope.site_id,
        report.id,
        finding.id,
    )
    assert evidence["page_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert evidence["missing_alt_images"] == ["guide.png"]
    with pytest.raises(TechnicalRecipeUnavailable):
        _load_evidence(
            identity,
            context["session_token"],
            context["generation"],
            scope.site_id,
            report.id,
            uuid4(),
        )
    release_id, actor_id = _register_release(
        admin,
        release_manager,
        "technical_alt",
        reviewed=True,
        version={"dashboard": "1.0.1", "slack": "1.0.2", "telegram": "1.0.3"}[approval_channel],
    )
    release = get_reviewed_recipe_release(api, recipe_key="technical_alt", release_id=release_id)
    plan = CandidateBuildPlan(
        extension.base_sha,
        extension.tree_sha,
        "d" * 64,
        NODE_IMAGE,
        ("npm", "run", "build"),
        ".next",
        (("app/page.tsx", b"candidate"),),
    )
    prepared = prepare_candidate_build(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        extension_id=extension.id,
        idempotency_key=uuid4(),
        plan=plan,
    )
    dispatch_candidate_build(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=prepared,
    )
    revision_id, key = uuid4(), uuid4()
    revision = {
        "schema_version": 1,
        "site_id": str(scope.site_id),
        "extension_id": str(extension.id),
        "build_id": str(prepared.id),
        "audit_report_id": str(report.id),
        "finding_id": str(finding.id),
        "recipe_release_id": str(release_id),
        "release_content_hash": release.content_hash,
        "base_sha": extension.base_sha,
        "patch_sha256": plan.patch_sha256,
        "source_path": "index.html",
        "source_sha256": evidence["page_body_sha256"],
        "result_sha256": hashlib.sha256(
            body.replace(b'<img src="guide.png">', b'<img src="guide.png" alt="Signal Guide">')
        ).hexdigest(),
        "patch": {
            "offset": body.index(b"<img"),
            "before": '<img src="guide.png">',
            "after": '<img src="guide.png" alt="Signal Guide">',
        },
        "evidence": {
            "manifest_id": str(report.manifest_id),
            "manifest_sha256": report.manifest_sha256,
            "page_id": evidence["page_id"],
            "finding": evidence["finding"],
            "site_origin": origin,
            "page_url": origin + "/",
        },
        "build_receipt": {
            "toolchain": NODE_IMAGE,
            "command": "npm run build",
            "exit_class": "passed",
            "logs_sha256": "e" * 64,
            "artifacts": [{"path": ".next/index.html", "sha256": "f" * 64, "size": 1}],
        },
        "approval_class": "owner_review",
        "expected_impact": "Resolve one finding.",
        "recovery_plan": "Revert this exact candidate.",
    }
    canonical = rfc8785.dumps(revision)
    args = (
        hashlib.sha256(context["session_token"].encode()).digest(),
        scope.site_id,
        context["generation"],
        revision_id,
        extension.id,
        prepared.id,
        report.id,
        finding.id,
        release_id,
        bytes.fromhex(release.content_hash),
        key,
        canonical,
        hashlib.sha256(canonical).digest(),
    )
    query = "SELECT * FROM control.seal_candidate_recipe_revision(" + ",".join(["%s"] * 13) + ")"
    assert identity.execute(query, args).fetchone()[3] == "build_unavailable"
    build = finish_candidate_build(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=prepared,
        result=CandidateSandboxOutcome(
            "passed", 0, "e" * 64, 1, ((".next/index.html", "f" * 64, 1),)
        ),
    )
    assert build.id == prepared.id
    incomplete = dict(revision)
    incomplete.pop("source_sha256")
    incomplete_bytes = rfc8785.dumps(incomplete)
    invalid_args = (
        *args[:3],
        uuid4(),
        *args[4:10],
        uuid4(),
        incomplete_bytes,
        hashlib.sha256(incomplete_bytes).digest(),
    )
    assert identity.execute(query, invalid_args).fetchone()[3] == "invalid_revision"
    row = identity.execute(query, args).fetchone()
    assert row[3] == "sealed" and row[2] is False
    assert identity.execute(query, args).fetchone()[2] is True
    assert (
        admin.execute(
            "SELECT revision_sha256 FROM app.candidate_recipe_revisions WHERE id=%s", (revision_id,)
        ).fetchone()[0]
        == hashlib.sha256(canonical).digest()
    )
    inbox = identity.execute(
        "SELECT * FROM control.read_authenticated_candidate_recipe_inbox(%s,%s,%s)",
        (
            hashlib.sha256(context["session_token"].encode()).digest(),
            scope.site_id,
            context["generation"],
        ),
    ).fetchone()
    assert inbox[0] == revision_id and inbox[8] == "pending" and inbox[14] == "found"
    slack_decision = None
    if approval_channel in {"slack", "telegram"}:
        if approval_channel == "slack":
            from tests.control_plane.test_slack_binding import (
                exercise_slack_approval as exercise_chat_approval,
            )
        else:
            from tests.control_plane.test_telegram_binding import (
                exercise_telegram_approval as exercise_chat_approval,
            )

        slack_decision = await exercise_chat_approval(
            admin,
            api,
            identity,
            scheduler,
            workflow,
            crawl_admission,
            crawl_ingest,
            scope,
            context,
            revision_id,
            hashlib.sha256(canonical).hexdigest(),
            tmp_path,
            release_manager,
        )
    decision_id = uuid4()
    decision = identity.execute(
        "SELECT * FROM control.decide_authenticated_candidate_recipe_revision("
        "%s,%s,%s,%s,%s,%s,%s)",
        (
            hashlib.sha256(context["session_token"].encode()).digest(),
            scope.site_id,
            context["generation"],
            revision_id,
            hashlib.sha256(canonical).digest(),
            decision_id,
            "approved",
        ),
    ).fetchone()
    assert decision[8] == "approved" and decision[15] == "decided"
    assert decision[14] is (approval_channel in {"slack", "telegram"})
    if slack_decision is not None:
        assert decision[9] == slack_decision and decision[12] == approval_channel
    replay = identity.execute(
        "SELECT * FROM control.decide_authenticated_candidate_recipe_revision("
        "%s,%s,%s,%s,%s,%s,%s)",
        (
            hashlib.sha256(context["session_token"].encode()).digest(),
            scope.site_id,
            context["generation"],
            revision_id,
            hashlib.sha256(canonical).digest(),
            uuid4(),
            "approved",
        ),
    ).fetchone()
    assert replay[14] is True and replay[15] == "decided"
    conflict = identity.execute(
        "SELECT * FROM control.decide_authenticated_candidate_recipe_revision("
        "%s,%s,%s,%s,%s,%s,%s)",
        (
            hashlib.sha256(context["session_token"].encode()).digest(),
            scope.site_id,
            context["generation"],
            revision_id,
            hashlib.sha256(canonical).digest(),
            uuid4(),
            "rejected",
        ),
    ).fetchone()
    assert conflict[15] == "decision_conflict"
    admin.execute(
        "UPDATE app.github_pr_extensions SET framework='eleventy',content_format='html' "
        "WHERE id=%s",
        (extension.id,),
    )
    token_hash = hashlib.sha256(context["session_token"].encode()).digest()
    operation_id, worker_id = uuid4(), uuid4()
    intent_hash = hashlib.sha256(b"synthetic exact PR intent").digest()
    prepare_sql = "SELECT * FROM control.prepare_github_pr_operation(%s,%s,%s,%s,%s,%s,%s)"
    operation_args = (
        token_hash,
        scope.site_id,
        context["generation"],
        revision_id,
        hashlib.sha256(canonical).digest(),
        operation_id,
        intent_hash,
    )
    operation = identity.execute(prepare_sql, operation_args).fetchone()
    assert operation[1:3] == ("planned", "tree") and operation[5] == "prepared"
    assert bytes(operation[3]) == canonical
    assert identity.execute(prepare_sql, operation_args).fetchone()[0] == operation_id
    assert (
        identity.execute(
            prepare_sql, (*operation_args[:-1], hashlib.sha256(b"different").digest())
        ).fetchone()[5]
        == "operation_conflict"
    )
    assert (
        identity.execute(
            "SELECT * FROM control.claim_github_pr_operation(%s,%s,%s,%s,%s)",
            (token_hash, scope.site_id, context["generation"], operation_id, worker_id),
        ).fetchone()[3]
        == "journal_unavailable"
    )
    receipt_hash = hashlib.sha256(b"independent journal acknowledgement").digest()
    journal_generation = uuid4()
    acknowledge = (
        token_hash,
        scope.site_id,
        context["generation"],
        operation_id,
        journal_generation,
        1,
        receipt_hash,
    )
    assert identity.execute(
        "SELECT control.acknowledge_github_pr_intent(%s,%s,%s,%s,%s,%s,%s)",
        acknowledge,
    ).fetchone() == ("acknowledged",)
    assert identity.execute(
        "SELECT control.acknowledge_github_pr_intent(%s,%s,%s,%s,%s,%s,%s)",
        (*acknowledge[:-1], hashlib.sha256(b"different").digest()),
    ).fetchone() == ("journal_conflict",)
    assert identity.execute(
        "SELECT control.bind_github_pr_operation_tree(%s,%s,%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], operation_id, "1" * 40, "2" * 40),
    ).fetchone() == ("bound",)
    assert identity.execute(
        "SELECT control.bind_github_pr_operation_tree(%s,%s,%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], operation_id, "3" * 40, "2" * 40),
    ).fetchone() == ("tree_binding_conflict",)
    claim = identity.execute(
        "SELECT * FROM control.claim_github_pr_operation(%s,%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], operation_id, worker_id),
    ).fetchone()
    assert claim[3] == "claimed" and claim[0] == 1
    admin.execute(
        "UPDATE app.sessions SET revoked_at=now() WHERE id=%s",
        (context["tenant_session_id"],),
    )
    assert identity.execute(
        "SELECT control.begin_github_pr_step(%s,%s,%s,%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], operation_id, worker_id, 1, "tree"),
    ).fetchone() == ("operation_unavailable",)
    admin.execute(
        "UPDATE app.sessions SET revoked_at=NULL WHERE id=%s",
        (context["tenant_session_id"],),
    )
    admin.execute(
        "UPDATE app.github_pr_extensions SET base_sha=%s WHERE id=%s",
        ("f" * 40, extension.id),
    )
    assert identity.execute(
        "SELECT control.begin_github_pr_step(%s,%s,%s,%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], operation_id, worker_id, 1, "tree"),
    ).fetchone() == ("binding_stale",)
    admin.execute(
        "UPDATE app.github_pr_extensions SET base_sha=%s WHERE id=%s",
        (extension.base_sha, extension.id),
    )
    assert identity.execute(
        "SELECT control.begin_github_pr_step(%s,%s,%s,%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], operation_id, worker_id, 0, "tree"),
    ).fetchone() == ("permit_denied",)
    assert identity.execute(
        "SELECT control.begin_github_pr_step(%s,%s,%s,%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], operation_id, worker_id, 1, "tree"),
    ).fetchone() == ("dispatching",)
    assert identity.execute(
        "SELECT control.begin_github_pr_step(%s,%s,%s,%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], operation_id, worker_id, 1, "tree"),
    ).fetchone() == ("reconcile_required",)
    assert identity.execute(
        "SELECT control.finish_github_pr_step(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            token_hash,
            scope.site_id,
            context["generation"],
            operation_id,
            worker_id,
            1,
            "tree",
            "outcome_unknown",
            receipt_hash,
            None,
            None,
        ),
    ).fetchone() == ("outcome_unknown",)
    assert identity.execute(
        "SELECT control.reconcile_github_pr_step(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            token_hash,
            scope.site_id,
            context["generation"],
            operation_id,
            worker_id,
            1,
            "tree",
            receipt_hash,
            None,
            None,
        ),
    ).fetchone() == ("reconciled",)
    admin.execute(
        "UPDATE app.github_pr_operations SET lease_until = now() - interval '1 second' WHERE id=%s",
        (operation_id,),
    )
    replacement_worker = uuid4()
    replacement = identity.execute(
        "SELECT * FROM control.claim_github_pr_operation(%s,%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], operation_id, replacement_worker),
    ).fetchone()
    assert replacement[0] == 2
    assert identity.execute(
        "SELECT control.begin_github_pr_step(%s,%s,%s,%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], operation_id, worker_id, 1, "commit"),
    ).fetchone() == ("permit_denied",)
    assert identity.execute(
        "SELECT control.begin_github_pr_step(%s,%s,%s,%s,%s,%s,%s)",
        (
            token_hash,
            scope.site_id,
            context["generation"],
            operation_id,
            replacement_worker,
            2,
            "commit",
        ),
    ).fetchone() == ("dispatching",)
    assert identity.execute(
        "SELECT control.finish_github_pr_step(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            token_hash,
            scope.site_id,
            context["generation"],
            operation_id,
            replacement_worker,
            2,
            "commit",
            "completed",
            receipt_hash,
            None,
            None,
        ),
    ).fetchone() == ("completed",)
    assert identity.execute(
        "SELECT control.begin_github_pr_step(%s,%s,%s,%s,%s,%s,%s)",
        (
            token_hash,
            scope.site_id,
            context["generation"],
            operation_id,
            replacement_worker,
            2,
            "branch",
        ),
    ).fetchone() == ("dispatching",)
    assert identity.execute(
        "SELECT control.reconcile_github_pr_step(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            token_hash,
            scope.site_id,
            context["generation"],
            operation_id,
            replacement_worker,
            2,
            "branch",
            receipt_hash,
            None,
            None,
        ),
    ).fetchone() == ("reconciled",)
    assert identity.execute(
        "SELECT control.begin_github_pr_step(%s,%s,%s,%s,%s,%s,%s)",
        (
            token_hash,
            scope.site_id,
            context["generation"],
            operation_id,
            replacement_worker,
            2,
            "pr",
        ),
    ).fetchone() == ("dispatching",)
    assert identity.execute(
        "SELECT control.finish_github_pr_step(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            token_hash,
            scope.site_id,
            context["generation"],
            operation_id,
            replacement_worker,
            2,
            "pr",
            "outcome_unknown",
            receipt_hash,
            None,
            None,
        ),
    ).fetchone() == ("outcome_unknown",)
    assert identity.execute(
        "SELECT control.reconcile_github_pr_step(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            token_hash,
            scope.site_id,
            context["generation"],
            operation_id,
            replacement_worker,
            2,
            "pr",
            receipt_hash,
            42,
            "https://github.com/SignalOwner/website/pull/42",
        ),
    ).fetchone() == ("reconciled",)
    assert identity.execute(
        "SELECT control.begin_github_pr_step(%s,%s,%s,%s,%s,%s,%s)",
        (
            token_hash,
            scope.site_id,
            context["generation"],
            operation_id,
            replacement_worker,
            2,
            "pr",
        ),
    ).fetchone() == ("permit_denied",)
    from tests.control_plane.test_github_delivery_observations import exercise_delivery_observations

    await exercise_delivery_observations(
        admin,
        api,
        identity,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        context,
        operation_id,
        canonical,
        tmp_path,
    )
    with pytest.raises(InsufficientPrivilege):
        identity.execute("UPDATE app.candidate_recipe_revisions SET patch_sha256=%s", ("0" * 64,))
    transition_recipe_release(
        release_manager,
        release_id=release_id,
        expected_status="REVIEWED",
        new_status="REVOKED",
        actor_user_id=actor_id,
        reason="Synthetic revocation",
    )
    assert identity.execute(
        "SELECT control.github_pr_dispatch_permit(%s,%s,%s,%s,%s,%s,%s)",
        (
            token_hash,
            scope.site_id,
            context["generation"],
            operation_id,
            replacement_worker,
            2,
            "done",
        ),
    ).fetchone() == ("release_inactive",)
    denied = identity.execute(
        query, (*args[:3], uuid4(), *args[4:10], uuid4(), *args[11:])
    ).fetchone()
    assert denied[3] == "release_unavailable"
