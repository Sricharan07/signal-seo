import hashlib
import json
import os
from uuid import UUID, uuid4

import psycopg
import pytest
import rfc8785
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from signal_core.astro_recipes import astro_recipe_release_manifest, verify_astro_impact
from signal_core.candidate_build import EMPTY_PATCH_SHA256, NODE_IMAGE, CandidateBuildPlan
from signal_core.candidate_build_service import (
    CandidateBuildUnavailable,
    built_html_payload,
    dispatch_candidate_build,
    finish_candidate_build,
    prepare_candidate_build,
    read_candidate_build,
)
from signal_core.candidate_recipe_inbox import decide_authenticated_candidate_recipe_revision
from signal_core.candidate_sandbox import CandidateSandboxOutcome
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_audit import analyze_crawl_manifest
from signal_core.front_matter_recipes import (
    front_matter_recipe_release_manifest,
    verify_front_matter_impact,
)
from signal_core.nextjs_recipes import nextjs_recipe_release_manifest, verify_nextjs_impact
from signal_core.npm_registry import locked_dependencies
from signal_core.proposals import ApprovalPermissionDenied
from signal_core.recipe_releases import (
    register_recipe_release,
    register_recipe_signing_key,
    transition_recipe_release,
)
from signal_core.technical_recipe_service import _load_evidence

from tests.control_plane.test_candidate_builds import _active_extension
from tests.control_plane.test_full_site_crawl import _command, _executor
from tests.control_plane.test_github_unprotected_base import accept_sql, fresh_mfa
from tests.control_plane.test_technical_recipes import TimedFetcher
from tests.tooling.astro_delivery_support import fixture_source
from tests.tooling.front_matter_support import fixture_source as front_matter_source
from tests.tooling.nextjs_support import fixture_source as nextjs_source


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def release_manager():
    with psycopg.connect(
        os.environ["SIGNAL_TEST_RELEASE_MANAGER_DSN"], autocommit=True
    ) as connection:
        yield connection


def _release(admin, manager, *, version, key="astro_title"):
    signer = Ed25519PrivateKey.generate()
    signer_id = "synthetic_astro_" + uuid4().hex
    register_recipe_signing_key(
        manager,
        key_id=signer_id,
        public_key=signer.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )
    actor = uuid4()
    admin.execute(
        "INSERT INTO control.users(id,oidc_issuer,oidc_subject,display_name) "
        "VALUES(%s,'https://identity.example.invalid',%s,'Synthetic reviewer')",
        (actor, str(actor)),
    )
    release = uuid4()
    body = rfc8785.dumps(
        (
            nextjs_recipe_release_manifest
            if key.startswith("nextjs_")
            else front_matter_recipe_release_manifest
            if key.startswith("front_matter_")
            else astro_recipe_release_manifest
        )(key, release, version=version)
    )
    register_recipe_release(
        manager,
        release_id=release,
        recipe_key=key,
        version=version,
        canonical_body=body,
        signing_key_id=signer_id,
        signature=signer.sign(body),
        actor_user_id=actor,
    )
    for before, after in (("DRAFT", "TESTED"), ("TESTED", "REVIEWED")):
        transition_recipe_release(
            manager,
            release_id=release,
            expected_status=before,
            new_status=after,
            actor_user_id=actor,
            reason="Synthetic Astro review",
        )
    return release, hashlib.sha256(body).hexdigest()


async def _setup(
    admin,
    identity,
    api,
    scheduler,
    workflow,
    crawl_ingest,
    manager,
    scopes,
    context,
    tmp_path,
    *,
    shared=False,
    version="1.0.0",
    front_matter=False,
    framework="astro",
    nextjs_router=None,
):
    scope, context, extension = await _active_extension(admin, identity, scopes, context)
    admin.execute(
        "UPDATE app.github_pr_extensions SET framework=%s,content_format=%s WHERE id=%s",
        (
            framework,
            "tsx" if nextjs_router else "markdown" if front_matter else "astro",
            extension.id,
        ),
    )
    origin = admin.execute(
        "SELECT primary_origin FROM app.sites WHERE id=%s", (scope.site_id,)
    ).fetchone()[0]
    body = b"<html><head><title></title></head><body><h1>Calendar Guide</h1></body></html>"
    command, run_id = _command(api, scheduler, workflow, scope, "synthetic-astro-delivery")
    crawl = _executor(tmp_path, TimedFetcher(origin, page_body=body)).run(
        command, first_run_id=run_id
    )
    report = analyze_crawl_manifest(
        crawl_ingest,
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        manifest_id=UUID(crawl.manifest_id),
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
    _, checkout, _ = fixture_source()
    root = "public-build"
    if front_matter:
        _, checkout, _ = front_matter_source(framework)
        root = {"astro": "public-build", "eleventy": "_site", "nextjs": "out"}[framework]
    if nextjs_router:
        _, checkout, _ = nextjs_source(nextjs_router, shared=shared)
        root = "out"
    files = dict(checkout.files)
    deps = locked_dependencies(files["package.json"], files["package-lock.json"])
    html = {
        "public-build/index.html": body.decode(),
        "public-build/unchanged.html": "<title>Untouched</title>",
    }
    if shared:
        html.update({f"public-build/dates/day-{i}/index.html": body.decode() for i in range(2)})
    html = {p.replace("public-build", root): s for p, s in html.items()}
    changed = {
        p: s.replace("<title></title>", "<title>Calendar Guide</title>")
        if "unchanged" not in p
        else s
        for p, s in html.items()
    }
    records = []
    store = EncryptedLocalArtifactStore(tmp_path.resolve() / "candidate-artifacts")
    artifact_key = ArtifactEncryptionKey("synthetic-candidate-artifact-key/v1", b"k" * 32)
    args = dict(
        session_token=context["session_token"], current_recovery_generation=context["generation"]
    )
    for index, pages in enumerate((html, changed)):
        plan = CandidateBuildPlan(
            extension.base_sha,
            extension.tree_sha,
            EMPTY_PATCH_SHA256 if index == 0 else "d" * 64,
            NODE_IMAGE,
            ("npm", "run", "build"),
            root,
            checkout.files,
            deps,
        )
        prepared = prepare_candidate_build(
            identity,
            **args,
            site_id=scope.site_id,
            extension_id=extension.id,
            idempotency_key=uuid4(),
            plan=plan,
        )
        dispatch_candidate_build(identity, **args, prepared=prepared)
        artifacts = tuple(
            (p, hashlib.sha256(s.encode()).hexdigest(), len(s.encode()))
            for p, s in sorted(pages.items())
        )
        result = CandidateSandboxOutcome(
            "passed",
            0,
            "e" * 64,
            5,
            artifacts,
            deps.lockfile_sha256,
            tuple((p, d, "Calendar Guide" if index else None, None) for p, d, _ in artifacts),
            built_html=tuple(sorted(pages.items())),
        )
        records.append(
            finish_candidate_build(
                identity,
                **args,
                prepared=prepared,
                result=result,
                artifact_store=store,
                artifact_key=artifact_key,
            )
        )
    # Database rows retain only references/digests; the source snapshot is an encrypted object.
    row = admin.execute(
        "SELECT artifact_id,pages_sha256 FROM app.candidate_built_html WHERE build_id=%s",
        (records[0].id,),
    ).fetchone()
    assert bytes(row[1]) == hashlib.sha256(built_html_payload(records[0].built_html)).digest()
    metadata = identity.execute(
        "SELECT control.read_candidate_built_html(%s,%s,%s,%s)",
        (
            hashlib.sha256(context["session_token"].encode()).digest(),
            scope.site_id,
            context["generation"],
            records[0].id,
        ),
    ).fetchone()[0]
    assert "html" not in metadata and "pages" not in metadata
    encrypted = (store.root / metadata["object_key"]).read_bytes()
    assert body not in encrypted
    with pytest.raises(CandidateBuildUnavailable):
        read_candidate_build(
            identity,
            **args,
            site_id=scope.site_id,
            build_id=records[0].id,
            include_html=True,
            artifact_store=store,
            artifact_key=ArtifactEncryptionKey(artifact_key.reference, b"z" * 32),
        )
    with pytest.raises(CandidateBuildUnavailable):
        read_candidate_build(
            identity, **args, site_id=scope.site_id, build_id=records[0].id, include_html=True
        )
    object_path = store.root / metadata["object_key"]
    object_path.write_bytes(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
    with pytest.raises(CandidateBuildUnavailable):
        read_candidate_build(
            identity,
            **args,
            site_id=scope.site_id,
            build_id=records[0].id,
            include_html=True,
            artifact_store=store,
            artifact_key=artifact_key,
        )
    object_path.write_bytes(encrypted)
    assert (
        read_candidate_build(
            identity,
            **args,
            site_id=scope.site_id,
            build_id=records[0].id,
            include_html=True,
            artifact_store=store,
            artifact_key=artifact_key,
        ).built_html
        == records[0].built_html
    )
    targets = {p: "Calendar Guide" for p in html if "unchanged" not in p}
    path = "src/data/generator.ts" if shared else "src/pages/index.astro"
    key = "astro_title"
    if front_matter:
        path = checkout.inventory.snapshot.content_path
        key = "front_matter_title"
    if nextjs_router:
        path = checkout.inventory.snapshot.content_path
        key = "nextjs_title"
    impact = (
        verify_nextjs_impact
        if nextjs_router
        else verify_front_matter_impact
        if front_matter
        else verify_astro_impact
    )(
        files=files,
        path=path,
        recipe_key=key,
        baseline=records[0],
        candidate=records[1],
        expected_by_page=targets,
        page_urls={p: origin + "/" for p in targets},
    )
    release, release_hash = _release(admin, manager, version=version, key=key)
    manifest = {
        "schema_version": 1,
        "framework": framework,
        "recipe_key": key,
        "site_id": str(scope.site_id),
        "extension_id": str(extension.id),
        "build_id": str(records[1].id),
        "audit_report_id": str(report.id),
        "finding_id": str(finding.id),
        "recipe_release_id": str(release),
        "release_content_hash": release_hash,
        "base_sha": extension.base_sha,
        "patch_sha256": records[1].patch_sha256,
        "source_path": path,
        "source_sha256": "a" * 64,
        "result_sha256": "b" * 64,
        "patch": {"offset": 14, "before": '"Calendar"', "after": '"Calendar Guide"'},
        "evidence": {
            k: evidence[k]
            for k in (
                "manifest_id",
                "manifest_sha256",
                "page_id",
                "page_url",
                "site_origin",
                "finding",
            )
        },
        "build_receipt": {
            "toolchain": records[1].toolchain,
            "command": records[1].command,
            "exit_class": "passed",
            "logs_sha256": records[1].logs_sha256,
            "artifacts": [{"path": p, "sha256": d, "size": n} for p, d, n in records[1].artifacts],
        },
        "built_impact": {
            "baseline_build_id": str(records[0].id),
            "lockfile_sha256": deps.lockfile_sha256,
            "scope_sha256": hashlib.sha256(impact.canonical_scope).hexdigest(),
            "page_count": len(impact.pages),
            "pages": list(impact.pages),
            "samples": list(impact.samples),
        },
        "approval_class": impact.approval_class,
        "autonomy_eligible": False,
        "claim_review_required": True,
        "model_draft": None,
        "expected_impact": "Exact built scope only.",
        "recovery_plan": "Revert only the exact source fragment in an owner-reviewed PR.",
    }
    if front_matter:
        manifest["content_adapter"] = "front_matter"
    if nextjs_router:
        manifest["content_adapter"] = "nextjs_metadata"
    return scope, context, extension, records, manifest, impact, uuid4(), uuid4()


def _seal(identity, data, *, manifest=None, scope_bytes=None, html_bytes=None):
    scope, context, extension, records, original, impact, revision, key = data
    m = original if manifest is None else manifest
    canonical = rfc8785.dumps(m)
    return identity.execute(
        "SELECT * FROM control."
        + (
            "seal_nextjs_recipe_revision"
            if m.get("content_adapter") == "nextjs_metadata"
            else "seal_front_matter_recipe_revision"
            if m.get("content_adapter") == "front_matter"
            else "seal_astro_recipe_revision"
        )
        + "("
        + ",".join(["%s"] * 17)
        + ")",
        (
            hashlib.sha256(context["session_token"].encode()).digest(),
            scope.site_id,
            context["generation"],
            revision,
            extension.id,
            records[1].id,
            UUID(m["audit_report_id"]),
            UUID(m["finding_id"]),
            UUID(m["recipe_release_id"]),
            bytes.fromhex(m["release_content_hash"]),
            key,
            canonical,
            hashlib.sha256(canonical).digest(),
            records[0].id,
            impact.canonical_scope if scope_bytes is None else scope_bytes,
            built_html_payload(records[0].built_html),
            built_html_payload(records[1].built_html) if html_bytes is None else html_bytes,
        ),
    ).fetchone()


@pytest.mark.anyio
@pytest.mark.parametrize("shared,unprotected", [(False, False), (True, False), (True, True)])
async def test_astro_seal_owner_mfa_approval_and_no_standing_dispatch(
    admin,
    identity,
    api,
    scheduler,
    workflow,
    crawl_ingest,
    release_manager,
    scopes,
    identity_context,
    tmp_path,
    shared,
    unprotected,
):
    data = await _setup(
        admin,
        identity,
        api,
        scheduler,
        workflow,
        crawl_ingest,
        release_manager,
        scopes,
        identity_context,
        tmp_path,
        shared=shared,
        version="1.0." + str(int(shared) + int(unprotected)),
    )
    scope, context, extension, records, m, impact, revision, _ = data
    fresh_mfa(admin, context)
    if unprotected:
        from types import SimpleNamespace

        admin.execute(
            "UPDATE app.github_read_bindings SET protected=false WHERE id=%s",
            (extension.binding_id,),
        )
        assert (
            accept_sql(identity, scope, context, SimpleNamespace(id=extension.binding_id))
            == "active"
        )
    row = _seal(identity, data)
    assert row[3] == "sealed" and row[2] is False
    assert _seal(identity, data)[2] is True
    token = hashlib.sha256(context["session_token"].encode()).digest()
    operation = uuid4()

    def prepare_operation():
        return identity.execute(
            "SELECT * FROM control.prepare_github_pr_operation(%s,%s,%s,%s,%s,%s,%s)",
            (
                token,
                scope.site_id,
                context["generation"],
                revision,
                bytes(row[1]),
                operation,
                b"d" * 32,
            ),
        ).fetchone()

    assert prepare_operation()[5] != "prepared"
    assert admin.execute(
        "SELECT count(*) FROM app.github_pr_operations WHERE id=%s", (operation,)
    ).fetchone() == (0,)
    decision_args = dict(
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
        revision_id=revision,
        expected_revision_sha256=bytes(row[1]).hex(),
        decision_id=uuid4(),
        decision="approved",
    )
    fresh_mfa(admin, context, age="6 minutes")
    with pytest.raises(ApprovalPermissionDenied):
        decide_authenticated_candidate_recipe_revision(identity, **decision_args)
    assert admin.execute(
        "SELECT count(*) FROM app.candidate_recipe_review_decisions WHERE candidate_revision_id=%s",
        (revision,),
    ).fetchone() == (0,)
    fresh_mfa(admin, context)
    approved = decide_authenticated_candidate_recipe_revision(identity, **decision_args)
    assert approved.review_status == "approved" and approved.decision_channel == "dashboard"
    assert prepare_operation()[5] == "prepared"
    assert prepare_operation()[5] == "prepared"
    assert identity.execute(
        "SELECT outcome FROM control.github_pr_operation_eligible(%s,%s,%s,%s)",
        (token, scope.site_id, context["generation"], revision),
    ).fetchone() == ("eligible",)
    assert admin.execute(
        "SELECT control.weekly_revision_a2_policy(%s,%s,%s)",
        (scope.tenant_id, scope.site_id, revision),
    ).fetchone() == (False,)
    assert admin.execute(
        "SELECT control.technical_a2_release_shape(%s)", (UUID(m["recipe_release_id"]),)
    ).fetchone() == (False,)
    fresh_mfa(admin, context, age="6 minutes")
    assert prepare_operation()[5] == "step_up_required"
    assert identity.execute(
        "SELECT outcome FROM control.github_pr_operation_eligible(%s,%s,%s,%s)",
        (token, scope.site_id, context["generation"], revision),
    ).fetchone() == ("step_up_required",)
    fresh_mfa(admin, context)
    if unprotected:
        admin.execute(
            "UPDATE app.github_read_bindings SET risk_generation=risk_generation+1 WHERE id=%s",
            (extension.binding_id,),
        )
        assert (
            identity.execute(
                "SELECT outcome FROM control.github_pr_operation_eligible(%s,%s,%s,%s)",
                (token, scope.site_id, context["generation"], revision),
            ).fetchone()[0]
            != "eligible"
        )
    for table, column, target in (
        ("candidate_built_html", "build_id", records[0].id),
        ("astro_candidate_impacts", "revision_id", revision),
        ("astro_review_authentication", "revision_id", revision),
    ):
        assert admin.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
            ("app." + table,),
        ).fetchone() == (True, True)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute(f"SELECT * FROM app.{table}")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(f"DELETE FROM app.{table} WHERE {column}=%s", (target,))


@pytest.mark.anyio
async def test_astro_sql_sealer_rejects_count_samples_scope_forgery_and_cross_site(
    admin,
    identity,
    api,
    scheduler,
    workflow,
    crawl_ingest,
    release_manager,
    scopes,
    identity_context,
    tmp_path,
):
    data = await _setup(
        admin,
        identity,
        api,
        scheduler,
        workflow,
        crawl_ingest,
        release_manager,
        scopes,
        identity_context,
        tmp_path,
        shared=True,
        version="1.0.3",
    )
    scope, context, _, records, m, impact, revision, _ = data
    for mutation in (
        "count",
        "class",
        "samples",
        "scope",
        "outside",
        "lock",
        "workflow",
        "protected",
        "finding_page",
        "raw_html",
    ):
        changed = json.loads(json.dumps(m))
        scope_bytes = impact.canonical_scope
        html_bytes = None
        if mutation == "count":
            changed["built_impact"]["page_count"] = 1
        elif mutation == "class":
            changed["approval_class"] = "A2"
        elif mutation == "samples":
            changed["built_impact"]["samples"][0]["after"] = "forged"
        elif mutation == "lock":
            changed["built_impact"]["lockfile_sha256"] = "0" * 64
        elif mutation == "workflow":
            changed["source_path"] = ".github/workflows/ci.yml"
        elif mutation == "protected":
            changed["source_path"] = "src/payments/page.astro"
        elif mutation == "finding_page":
            changed["evidence"]["page_url"] = "https://other.example.invalid/"
        elif mutation == "raw_html":
            html_bytes = built_html_payload((("public-build/index.html", "forged"),))
        else:
            proof = json.loads(scope_bytes)
            if mutation == "scope":
                proof["assertions"][0]["after"] = "forged"
            else:
                proof["assertions"] = proof["assertions"][1:]
            scope_bytes = rfc8785.dumps(proof)
            changed["built_impact"]["scope_sha256"] = hashlib.sha256(scope_bytes).hexdigest()
            if mutation == "outside":
                changed["built_impact"]["page_count"] = len(proof["assertions"])
                changed["built_impact"]["pages"] = [p["path"] for p in proof["assertions"]]
        assert (
            _seal(identity, data, manifest=changed, scope_bytes=scope_bytes, html_bytes=html_bytes)[
                3
            ]
            != "sealed"
        )
    assert admin.execute(
        "SELECT count(*) FROM app.candidate_recipe_revisions WHERE id=%s", (revision,)
    ).fetchone() == (0,)
    assert identity.execute(
        "SELECT control.read_candidate_built_html(%s,%s,%s,%s)",
        (
            hashlib.sha256(context["session_token"].encode()).digest(),
            scopes[1].site_id,
            context["generation"],
            records[0].id,
        ),
    ).fetchone() == (None,)
    assert _seal(identity, data)[3] == "sealed"
