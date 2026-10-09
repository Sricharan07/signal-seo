import hashlib
from uuid import UUID, uuid4

import psycopg
import pytest
import rfc8785
from signal_core.candidate_build import EMPTY_PATCH_SHA256, NODE_IMAGE, CandidateBuildPlan
from signal_core.candidate_build_service import (
    dispatch_candidate_build,
    finish_candidate_build,
    prepare_candidate_build,
)
from signal_core.candidate_sandbox import CandidateSandboxOutcome
from signal_core.crawl_audit import analyze_crawl_manifest
from signal_core.recipe_releases import get_reviewed_recipe_release
from signal_core.structured_data_recipe import structured_data_release_manifest
from signal_core.technical_recipe_service import _load_evidence

from tests.control_plane.test_full_site_crawl import _command, _executor
from tests.control_plane.test_technical_recipes import (
    TimedFetcher,
    _register_release,
)
from tests.control_plane.test_technical_recipes import (
    release_manager as release_manager,
)
from tests.control_plane.test_visibility_agent import visibility as visibility


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_grounded_sealer_exact_build_release_and_owner_only(
    admin,
    identity,
    api,
    scheduler,
    workflow,
    crawl_ingest,
    scopes,
    identity_context,
    release_manager,
    tmp_path,
    monkeypatch,
    visibility,
):
    import tests.control_plane.test_technical_recipes as recipes

    monkeypatch.setattr(
        recipes,
        "technical_recipe_release_manifest",
        lambda key, release, **kwargs: structured_data_release_manifest(release, **kwargs),
    )
    import json

    from signal_core.github_pr_extension import observe_github_pr_extension
    from signal_core.github_read_binding import finish_github_read_binding
    from signal_core.shared_egress import ProviderEgressResponse

    from tests.control_plane.test_github_pr_extension import _credential, _provider
    from tests.control_plane.test_github_read_binding import (
        _owner_site,
        _prepare,
        _snapshot,
        _target,
    )

    scope, context = _owner_site(admin, identity, scopes, identity_context)
    target = _target(content_path="index.html")
    binding = _prepare(identity, scope, context, target=target)
    finish_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=binding,
        snapshot=_snapshot(target),
    )
    credential, bao = _credential()
    transport, _ = _provider()
    original = transport.provider.request_json.side_effect

    def static_tree(**kwargs):
        if "/git/trees/" in kwargs["url"]:
            return ProviderEgressResponse(
                200,
                "application/json",
                json.dumps(
                    {
                        "sha": "b" * 40,
                        "truncated": False,
                        "tree": [
                            {
                                "path": ".eleventy.js",
                                "mode": "100644",
                                "type": "blob",
                                "sha": "c" * 40,
                            },
                            {
                                "path": "index.html",
                                "mode": "100644",
                                "type": "blob",
                                "sha": "d" * 40,
                            },
                        ],
                    }
                ).encode(),
            )
        return original(**kwargs)

    transport.provider.request_json.side_effect = static_tree
    extension = await observe_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=binding.id,
        idempotency_key=uuid4(),
        credential=credential,
        github_transport=transport,
        openbao_transport=bao,
    )
    origin = admin.execute(
        "SELECT primary_origin FROM app.sites WHERE tenant_id=%s AND id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone()[0]
    body = b"<html><head></head><body><h1>Signal Guide</h1></body></html>"
    command, first_run = _command(api, scheduler, workflow, scope, "grounded-structured-crawl")
    manifest = _executor(tmp_path, TimedFetcher(origin, page_body=body)).run(
        command, first_run_id=first_run
    )
    report = analyze_crawl_manifest(
        crawl_ingest,
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        manifest_id=UUID(manifest.manifest_id),
    )
    finding = next(f for f in report.findings if f.key == "metadata.title.missing")
    evidence = _load_evidence(
        identity,
        context["session_token"],
        context["generation"],
        scope.site_id,
        report.id,
        finding.id,
    )
    release_id, _ = _register_release(
        admin, release_manager, "structured_data_grounded", reviewed=True
    )
    release = get_reviewed_recipe_release(
        api, recipe_key="structured_data_grounded", release_id=release_id
    )
    document = {
        "@context": "https://schema.org",
        "@type": "Article",
        "url": origin + "/",
        "headline": "Signal Guide",
    }
    fragment = (
        '<script type="application/ld+json">' + rfc8785.dumps(document).decode() + "</script>"
    )
    result = body.replace(b"<head>", b"<head>" + fragment.encode())

    def build(content, patch_hash):
        plan = CandidateBuildPlan(
            extension.base_sha,
            extension.tree_sha,
            patch_hash,
            NODE_IMAGE,
            ("npm", "run", "build"),
            "_site",
            (("index.html", content),),
        )
        authority = dict(
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
        )
        prepared = prepare_candidate_build(
            identity,
            **authority,
            site_id=scope.site_id,
            extension_id=extension.id,
            idempotency_key=uuid4(),
            plan=plan,
        )
        dispatch_candidate_build(identity, **authority, prepared=prepared)
        digest = hashlib.sha256(content).hexdigest()
        return finish_candidate_build(
            identity,
            **authority,
            prepared=prepared,
            result=CandidateSandboxOutcome(
                "passed",
                0,
                "e" * 64,
                1,
                (("_site/index.html", digest, len(content)), ("_site/asset.txt", "f" * 64, 1)),
            ),
        )

    baseline = build(body, EMPTY_PATCH_SHA256)
    candidate = build(result, "d" * 64)
    revision = dict(
        schema_version=1,
        site_id=str(scope.site_id),
        extension_id=str(extension.id),
        build_id=str(candidate.id),
        audit_report_id=str(report.id),
        finding_id=str(finding.id),
        recipe_release_id=str(release_id),
        release_content_hash=release.content_hash,
        base_sha=extension.base_sha,
        patch_sha256=candidate.patch_sha256,
        source_path="index.html",
        source_sha256=hashlib.sha256(body).hexdigest(),
        result_sha256=hashlib.sha256(result).hexdigest(),
        patch={"offset": body.index(b"<head>") + 6, "before": "", "after": fragment},
        evidence={
            "manifest_id": str(report.manifest_id),
            "manifest_sha256": report.manifest_sha256,
            "page_id": evidence["page_id"],
            "finding": evidence["finding"],
            "site_origin": origin,
            "page_url": origin + "/",
        },
        build_receipt={
            "toolchain": NODE_IMAGE,
            "command": "npm run build",
            "exit_class": "passed",
            "logs_sha256": "e" * 64,
            "artifacts": [{"path": p, "sha256": h, "size": n} for p, h, n in candidate.artifacts],
        },
        approval_class="owner_review",
        claim_review_required=False,
        model_draft=None,
        expected_impact="Observed change only.",
        recovery_plan="Revert this exact block.",
        structured_data={
            "recipe_key": "structured_data_grounded",
            "json_ld": document,
            "fact_refs": {},
            "owner_required": False,
            "autonomy_eligible": False,
            "output_path": "_site/index.html",
            "baseline_build_id": str(baseline.id),
        },
    )
    query = "SELECT * FROM control.seal_structured_data_revision(" + ",".join(["%s"] * 13) + ")"

    def args(value, revision_id=None, request_id=None):
        canonical = rfc8785.dumps(value)
        return (
            hashlib.sha256(context["session_token"].encode()).digest(),
            scope.site_id,
            context["generation"],
            revision_id or uuid4(),
            extension.id,
            candidate.id,
            report.id,
            finding.id,
            release_id,
            bytes.fromhex(release.content_hash),
            request_id or uuid4(),
            canonical,
            hashlib.sha256(canonical).digest(),
        )

    for field, value in (
        ("autonomy_eligible", True),
        ("autonomy_eligible", "false"),
        ("json_ld", {**document, "author": "invented"}),
        ("output_path", "_site/other.html"),
    ):
        invalid = {**revision, "structured_data": {**revision["structured_data"], field: value}}
        with pytest.raises(psycopg.errors.CheckViolation):
            identity.execute(query, args(invalid))

    from signal_core.ai_visibility import TargetQuestion, record_target_question_set
    from signal_core.ai_visibility_agent import decide_proposal, prepare_proposals, read_agent
    from signal_core.business_brain import FactCategory, FactProvenance, approve_fact, propose_fact
    from signal_core.candidate_recipe_inbox import read_authenticated_candidate_recipe_inbox

    from tests.control_plane.test_owner_ai_questions import mfa

    brain = dict(
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
    )
    propose_fact(
        api,
        **brain,
        category=FactCategory.POSITIONING,
        statement="Signal Guide",
        provenance=FactProvenance("owner_statement"),
    )
    fact = admin.execute(
        "SELECT id FROM app.business_brain_facts WHERE site_id=%s AND statement='Signal Guide'",
        (scope.site_id,),
    ).fetchone()[0]
    approve_fact(api, **brain, fact_id=fact)
    questions = record_target_question_set(
        crawl_ingest,
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        crawl_manifest_id=report.manifest_id,
        questions=(TargetQuestion("What is Signal Guide?", "owner", None),),
    )
    question = admin.execute(
        "SELECT id FROM app.ai_visibility_questions WHERE question_set_id=%s", (questions,)
    ).fetchone()[0]
    admin.execute(
        "INSERT INTO app.ai_visibility_observations(tenant_id,site_id,id,question_id,provider,"
        "model,status,provider_evidence_id,cited_pages,other_domains,usage) "
        "SELECT tenant_id,site_id,%s,%s,provider,model,status,provider_evidence_id,cited_pages,"
        "other_domains,usage FROM app.ai_visibility_observations "
        "WHERE site_id=%s ORDER BY observed_at LIMIT 1",
        (uuid4(), question, scope.site_id),
    )
    common = dict(
        session_token=context["session_token"],
        generation=context["generation"],
        site_id=scope.site_id,
    )
    prepare_proposals(api, **common)
    proposal = next(
        p
        for p in read_agent(api, **common)["proposals"]
        if p["kind"] == "structured_data" and p["page_id"] == evidence["page_id"]
    )
    linked = {
        **revision,
        "visibility_proposal_id": proposal["id"],
        "visibility_proposal_digest": proposal["digest"],
        "visibility_fact_fields": {"headline": str(fact)},
    }
    owner_query = (
        "SELECT * FROM control.seal_ai_visibility_structured_revision("
        + ",".join(["%s"] * 15)
        + ")"
    )
    owner_args = (*args(linked), UUID(proposal["id"]), bytes.fromhex(proposal["digest"]))
    assert identity.execute(owner_query, owner_args).fetchone()[3] == "permission_denied"
    mfa(admin, context)
    assert identity.execute(owner_query, owner_args).fetchone()[3] == "proposal_unavailable"
    decide_proposal(
        api,
        **common,
        proposal_id=UUID(proposal["id"]),
        digest=proposal["digest"],
        decision="accepted",
    )
    for field, value in (
        ("visibility_fact_fields", {"headline": str(uuid4())}),
        ("visibility_proposal_digest", "b" * 64),
    ):
        invalid = {**linked, field: value}
        rejected = (*args(invalid), UUID(proposal["id"]), bytes.fromhex(proposal["digest"]))
        assert identity.execute(owner_query, rejected).fetchone()[3] in {
            "facts_unavailable",
            "proposal_unavailable",
        }
    assert identity.execute(owner_query, owner_args).fetchone()[3] == "sealed"
    assert identity.execute(owner_query, owner_args).fetchone()[2]
    inbox = read_authenticated_candidate_recipe_inbox(
        identity,
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
    )
    assert any(item.revision_id == owner_args[3] for item in inbox)
    assert all(item.review_status == "pending" for item in inbox)
    from signal_core.business_brain import remove_fact

    remove_fact(api, **brain, fact_id=fact)
    assert identity.execute(owner_query, owner_args).fetchone()[3] == "proposal_unavailable"
    assert (
        admin.execute(
            "SELECT count(*) FROM app.github_pr_operations WHERE site_id=%s", (scope.site_id,)
        ).fetchone()[0]
        == 0
    )
    # The legacy sealer still qualifies on a distinct exact candidate build.
    candidate = build(result, "d" * 64)
    revision["build_id"] = str(candidate.id)
    accepted = args(revision)
    assert identity.execute(query, accepted).fetchone()[3] == "sealed"
    assert identity.execute(query, accepted).fetchone()[2] is True
    assert not api.execute("SELECT control.recipe_autonomy_eligible(%s)", (release_id,)).fetchone()[
        0
    ]
    admin.execute(
        "UPDATE app.memberships SET role_key='analyst',"
        "authorization_epoch=authorization_epoch+1 WHERE id=%s",
        (context["membership_id"],),
    )
    assert identity.execute(query, args(revision)).fetchone()[3] == "permission_denied"
