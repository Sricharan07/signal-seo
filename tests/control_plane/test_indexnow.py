import hashlib
import json
import os
from contextlib import ExitStack
from dataclasses import replace
from uuid import uuid4

import httpx2
import psycopg
import pytest
import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from signal_core.authorization import AuthorizationDenied
from signal_core.candidate_build import (
    EMPTY_PATCH_SHA256,
    NODE_IMAGE,
    CandidateBuildPlan,
    plan_candidate_build,
)
from signal_core.candidate_build_service import (
    dispatch_candidate_build,
    finish_candidate_build,
    prepare_candidate_build,
)
from signal_core.candidate_recipe_inbox import (
    decide_authenticated_candidate_recipe_revision,
    read_authenticated_candidate_recipe_inbox,
)
from signal_core.candidate_sandbox import CandidateSandboxOutcome
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_http import CrawlFetchUnavailable
from signal_core.github_pr_extension import read_github_pr_extension
from signal_core.indexnow import IndexNowService, IndexNowUnavailable
from signal_core.indexnow_recipe import seal_indexnow_key_recipe
from signal_core.indexnow_secrets import OpenBaoIndexNowKeys
from signal_core.npm_registry import locked_dependencies
from signal_core.proposals import ApprovalPermissionDenied
from signal_core.recipe_releases import get_reviewed_recipe_release
from signal_core.shared_egress import SharedEgressProvider
from signal_core.write_intent_journal import WriteIntentJournal

from tests.control_plane.test_candidate_builds import _active_extension
from tests.control_plane.test_github_pr_extension import _credential, _provider
from tests.control_plane.test_shared_egress import authority
from tests.control_plane.test_technical_recipes import _register_release
from tests.control_plane.test_technical_recipes import release_manager as release_manager
from tests.tooling.astro_build_support import source_files
from tests.tooling.test_indexnow import BaoDouble
from tests.tooling.test_technical_seo_recipes import _case

_JOURNAL_SIGNER = Ed25519PrivateKey.generate()
_JOURNAL_CIPHER_KEY = os.urandom(64)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("valid_output", [True, False])
async def test_key_recipe_seals_only_the_exact_completed_static_build(
    admin, api, identity, release_manager, scopes, identity_context, monkeypatch, valid_output
):
    from unittest.mock import AsyncMock

    from signal_core import indexnow_recipe

    scope, context, extension = await _active_extension(admin, identity, scopes, identity_context)
    admin.execute(
        "UPDATE app.github_pr_extensions SET framework='eleventy',content_format='html' "
        "WHERE id=%s",
        (extension.id,),
    )
    extension = read_github_pr_extension(
        identity,
        session_token=context["session_token"],
        site_id=scope.site_id,
        current_recovery_generation=context["generation"],
        extension_id=extension.id,
    )
    _, checkout, _ = _case("<title>Synthetic unchanged page</title>", "indexnow.key.required")
    content_sha = next(
        entry.sha for entry in checkout.inventory.entries if entry.path == "index.html"
    )
    admin.execute(
        "UPDATE app.github_pr_extensions SET content_sha=%s WHERE id=%s",
        (content_sha, extension.id),
    )
    extension = replace(extension, content_sha=content_sha)
    checkout = replace(
        checkout,
        inventory=replace(
            checkout.inventory,
            snapshot=replace(checkout.inventory.snapshot, base_sha=extension.base_sha),
            tree_sha=extension.tree_sha,
        ),
    )
    monkeypatch.setattr(
        indexnow_recipe, "inspect_current_github_pr_extension", AsyncMock(return_value=extension)
    )
    monkeypatch.setattr(
        indexnow_recipe, "checkout_github_repository", AsyncMock(return_value=checkout)
    )
    release_id, _ = _register_release(
        admin,
        release_manager,
        "technical_indexnow_key",
        reviewed=True,
        version="1.0.1680" if valid_output else "1.0.1681",
    )
    credential, bao = _credential()
    transport, _ = _provider()
    store = OpenBaoIndexNowKeys("https://bao.example.invalid", "synthetic-indexnow-bao-token")
    service = IndexNowService(identity, store, context["generation"])
    key_id = uuid4()

    async def build(connection, **options):
        plan = plan_candidate_build(
            extension,
            checkout,
            patch=options["patch"],
            approved_paths=options["approved_paths"],
            indexnow_key=options["indexnow_key"],
        )
        args = dict(
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
        )
        prepared = prepare_candidate_build(
            connection,
            **args,
            site_id=scope.site_id,
            extension_id=extension.id,
            idempotency_key=options["idempotency_key"],
            plan=plan,
        )
        dispatch_candidate_build(connection, **args, prepared=prepared)
        content = options["indexnow_key"].encode()
        artifacts = (
            (
                "_site/"
                + (options["indexnow_key"] if valid_output else "synthetic-unrelated")
                + ".txt",
                hashlib.sha256(content).hexdigest(),
                len(content),
            ),
        )
        return finish_candidate_build(
            connection,
            **args,
            prepared=prepared,
            result=CandidateSandboxOutcome("passed", 0, "e" * 64, 1, artifacts),
        )

    monkeypatch.setattr(indexnow_recipe, "build_candidate_from_github", build)
    options = dict(
        session_token=context["session_token"],
        site_id=scope.site_id,
        key_id=key_id,
        extension_id=extension.id,
        recipe_release_id=release_id,
        build_idempotency_key=uuid4(),
        credential=credential,
        github_transport=transport,
        runner=object(),
        secret_options={"transport": httpx2.MockTransport(BaoDouble().handle)},
        openbao_transport=bao,
    )
    if not valid_output:
        with pytest.raises(IndexNowUnavailable, match="KEY_RECIPE_STATIC_OUTPUT_UNAVAILABLE"):
            await seal_indexnow_key_recipe(service, api, **options)
        assert (
            admin.execute(
                "SELECT count(*) FROM app.indexnow_key_revisions WHERE key_id=%s", (key_id,)
            ).fetchone()[0]
            == 0
        )
        return
    revision = await seal_indexnow_key_recipe(service, api, **options)
    inbox = read_authenticated_candidate_recipe_inbox(
        identity,
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
    )
    exact = next(item for item in inbox if item.revision_id == revision.id)
    assert exact.manifest["approval_class"] == "owner_review"
    assert exact.manifest["patch"]["before"] == ""
    assert exact.manifest["build_id"] == str(revision.build_id)


async def seed_key_revision(
    admin,
    api,
    identity,
    release_manager,
    scope,
    context,
    extension,
    *,
    previous=None,
    version="1.0.86",
    bao=None,
    astro_public=None,
    artifact_store=None,
):
    double = bao or BaoDouble()
    store = OpenBaoIndexNowKeys("https://bao.example.invalid", "synthetic-indexnow-bao-token")
    service = IndexNowService(identity, store, context["generation"])
    key_id = uuid4()
    options = {"transport": httpx2.MockTransport(double.handle)}
    _, origin, key = await service.create_key(
        session_token=context["session_token"],
        site_id=scope.site_id,
        key_id=key_id,
        previous_key_id=previous,
        secret_options=options,
    )
    assert await service.create_key(
        session_token=context["session_token"],
        site_id=scope.site_id,
        key_id=key_id,
        previous_key_id=previous,
        secret_options=options,
    ) == (scope.tenant_id, origin, key)
    release_id, _ = _register_release(
        admin, release_manager, "technical_indexnow_key", reviewed=True, version=version
    )
    release = get_reviewed_recipe_release(
        api, recipe_key="technical_indexnow_key", release_id=release_id
    )
    admin.execute(
        "UPDATE app.github_pr_extensions SET framework=%s,content_format=%s WHERE id=%s",
        (
            "astro" if astro_public else "eleventy",
            "astro" if astro_public else "html",
            extension.id,
        ),
    )
    digest = hashlib.sha256(key.encode()).hexdigest()
    path = (astro_public + "/" if astro_public else "") + key + ".txt"
    root = "public-build" if astro_public else "_site"
    baseline = None
    deps = None
    artifact_key = ArtifactEncryptionKey("synthetic-indexnow-artifact-key/v1", b"k" * 32)
    pages = {root + "/index.html": "<title>Synthetic unchanged page</title>"}
    html_artifacts = tuple(
        (p, hashlib.sha256(s.encode()).hexdigest(), len(s)) for p, s in pages.items()
    )
    args = dict(
        session_token=context["session_token"], current_recovery_generation=context["generation"]
    )
    if astro_public:
        files = source_files(
            config=f"export default {{publicDir:'./{astro_public}',outDir:'./{root}'}};".encode()
        )
        deps = locked_dependencies(files["package.json"], files["package-lock.json"])
        base_plan = CandidateBuildPlan(
            extension.base_sha,
            extension.tree_sha,
            EMPTY_PATCH_SHA256,
            NODE_IMAGE,
            ("npm", "run", "build"),
            root,
            tuple(files.items()),
            deps,
        )
        baseline = prepare_candidate_build(
            identity,
            **args,
            site_id=scope.site_id,
            extension_id=extension.id,
            idempotency_key=uuid4(),
            plan=base_plan,
        )
        dispatch_candidate_build(identity, **args, prepared=baseline)
        finish_candidate_build(
            identity,
            **args,
            prepared=baseline,
            result=CandidateSandboxOutcome(
                "passed",
                0,
                "e" * 64,
                1,
                html_artifacts,
                deps.lockfile_sha256,
                tuple((p, d, "Synthetic unchanged page", None) for p, d, _ in html_artifacts),
                built_html=tuple(pages.items()),
            ),
            artifact_store=artifact_store,
            artifact_key=artifact_key,
        )
    plan = CandidateBuildPlan(
        extension.base_sha,
        extension.tree_sha,
        "d" * 64,
        NODE_IMAGE,
        ("npm", "run", "build"),
        root,
        ((path, key.encode()),),
        deps,
    )
    args = dict(
        session_token=context["session_token"], current_recovery_generation=context["generation"]
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
    artifacts = (html_artifacts if astro_public else ()) + (
        (root + "/" + key + ".txt", digest, len(key)),
    )
    finish_candidate_build(
        identity,
        **args,
        prepared=prepared,
        result=CandidateSandboxOutcome(
            "passed",
            0,
            "e" * 64,
            1,
            artifacts,
            deps.lockfile_sha256 if deps else None,
            tuple((p, d, "Synthetic unchanged page", None) for p, d, _ in html_artifacts)
            if deps
            else (),
            built_html=tuple(pages.items()) if deps else (),
        ),
        artifact_store=artifact_store,
        artifact_key=artifact_key if deps else None,
    )
    revision_id = uuid4()
    manifest = {
        "schema_version": 1,
        "site_id": str(scope.site_id),
        "extension_id": str(extension.id),
        "build_id": str(prepared.id),
        "audit_report_id": None,
        "finding_id": str(key_id),
        "recipe_release_id": str(release_id),
        "release_content_hash": release.content_hash,
        "base_sha": extension.base_sha,
        "patch_sha256": plan.patch_sha256,
        "source_path": path,
        "source_sha256": hashlib.sha256(b"").hexdigest(),
        "result_sha256": digest,
        "patch": {"offset": 0, "before": "", "after": key},
        "evidence": {
            "key_id": str(key_id),
            "key_sha256": digest,
            "site_origin": origin,
            "page_url": origin + "/" + key + ".txt",
            "finding": {
                "id": str(key_id),
                "key": "indexnow.key.required",
                "title": "IndexNow key file",
                "summary": "Publish the exact key.",
                "resource_locator": origin + "/" + key + ".txt",
            },
        },
        "build_receipt": {
            "toolchain": NODE_IMAGE,
            "command": "npm run build",
            "exit_class": "passed",
            "logs_sha256": "e" * 64,
            "artifacts": [{"path": p, "sha256": d, "size": s} for p, d, s in artifacts],
        },
        "approval_class": "owner_review",
        "expected_impact": "Enable changed-URL notification.",
        "recovery_plan": "Retire the old key without deleting its file.",
        "claim_review_required": False,
        "model_draft": None,
    }
    if astro_public:
        manifest.update(
            approval_class="A4",
            autonomy_eligible=False,
            static_key_placement={
                "public_directory": astro_public,
                "output_path": root + "/" + key + ".txt",
                "baseline_build_id": str(baseline.id),
            },
        )
    canonical = rfc8785.dumps(manifest)
    assert service._call(
        "seal_indexnow_key_revision",
        context["session_token"],
        scope.site_id,
        key_id,
        revision_id,
        canonical,
        hashlib.sha256(canonical).digest(),
    ) == ("sealed",)
    return service, key_id, key, revision_id, canonical, double


@pytest.mark.anyio
@pytest.mark.parametrize("public", ["public", "static/assets"])
async def test_astro_indexnow_seal_inherits_a4_owner_review_and_exact_static_artifacts(
    admin, api, identity, release_manager, scopes, identity_context, tmp_path, public
):
    scope, context, extension = await _active_extension(admin, identity, scopes, identity_context)
    _, _, key, revision, canonical, _ = await seed_key_revision(
        admin,
        api,
        identity,
        release_manager,
        scope,
        context,
        extension,
        astro_public=public,
        version="1.0.144" if public == "public" else "1.0.145",
        artifact_store=EncryptedLocalArtifactStore(tmp_path.resolve() / "astro-key"),
    )
    manifest = json.loads(canonical)
    assert manifest["source_path"] == public + "/" + key + ".txt"
    assert manifest["approval_class"] == "A4" and manifest["autonomy_eligible"] is False
    assert admin.execute(
        "SELECT approval_class FROM app.astro_candidate_impacts WHERE revision_id=%s", (revision,)
    ).fetchone() == ("A4",)
    with pytest.raises(ApprovalPermissionDenied):
        decide_authenticated_candidate_recipe_revision(
            identity,
            session_token=context["session_token"],
            requested_site_id=scope.site_id,
            current_recovery_generation=context["generation"],
            revision_id=revision,
            expected_revision_sha256=hashlib.sha256(canonical).hexdigest(),
            decision_id=uuid4(),
            decision="approved",
        )
    step_up(admin, context)
    decision = decide_authenticated_candidate_recipe_revision(
        identity,
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
        revision_id=revision,
        expected_revision_sha256=hashlib.sha256(canonical).hexdigest(),
        decision_id=uuid4(),
        decision="approved",
    )
    assert decision.decision == "approved"
    assert admin.execute(
        "SELECT outcome FROM control.github_pr_operation_eligible(%s,%s,%s,%s)",
        (
            hashlib.sha256(context["session_token"].encode()).digest(),
            scope.site_id,
            context["generation"],
            revision,
        ),
    ).fetchone() == ("eligible",)


@pytest.mark.anyio
async def test_key_lifecycle_inbox_rotation_and_role_tenant_denial(
    admin, api, identity, release_manager, scopes, identity_context
):
    scope, context, extension = await _active_extension(admin, identity, scopes, identity_context)
    service, key_id, key, revision_id, canonical, double = await seed_key_revision(
        admin, api, identity, release_manager, scope, context, extension
    )
    args = dict(
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
    )
    inbox = read_authenticated_candidate_recipe_inbox(identity, **args)
    assert len(inbox) == 1 and inbox[0].review_status == "pending"
    assert inbox[0].manifest["patch"] == {"offset": 0, "before": "", "after": key}
    assert admin.execute(
        "SELECT count(*) FROM app.github_pr_operations WHERE candidate_revision_id=%s",
        (revision_id,),
    ).fetchone() == (0,)
    token_hash = hashlib.sha256(context["session_token"].encode()).digest()
    assert admin.execute(
        "SELECT outcome FROM control.github_pr_operation_eligible(%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], revision_id),
    ).fetchone() == ("decision_stale",)
    with pytest.raises(ApprovalPermissionDenied):
        decide_authenticated_candidate_recipe_revision(
            identity,
            **args,
            revision_id=revision_id,
            expected_revision_sha256=hashlib.sha256(canonical).hexdigest(),
            decision_id=uuid4(),
            decision="approved",
        )
    step_up(admin, context)
    decision = decide_authenticated_candidate_recipe_revision(
        identity,
        **args,
        revision_id=revision_id,
        expected_revision_sha256=hashlib.sha256(canonical).hexdigest(),
        decision_id=uuid4(),
        decision="approved",
    )
    assert decision.decision == "approved"
    assert admin.execute(
        "SELECT outcome FROM control.github_pr_operation_eligible(%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], revision_id),
    ).fetchone() == ("eligible",)
    _, new_key, _, _, _, _ = await seed_key_revision(
        admin,
        api,
        identity,
        release_manager,
        scope,
        context,
        extension,
        previous=key_id,
        version="1.0.87",
        bao=double,
    )
    assert admin.execute(
        "SELECT new_key_id FROM app.indexnow_key_retirements WHERE old_key_id=%s", (key_id,)
    ).fetchone() == (new_key,)
    assert admin.execute(
        "SELECT outcome FROM control.github_pr_operation_eligible(%s,%s,%s,%s)",
        (token_hash, scope.site_id, context["generation"], revision_id),
    ).fetchone() == ("key_retired",)
    assert (
        service.read(session_token=context["session_token"], site_id=scope.site_id)["key_status"]
        == "not_created"
    )
    with pytest.raises(AuthorizationDenied):
        service.read(session_token=context["session_token"], site_id=scopes[1].site_id)
    admin.execute(
        "UPDATE app.memberships SET role_key='analyst' WHERE id=%s", (context["membership_id"],)
    )
    with pytest.raises(AuthorizationDenied):
        service.read(session_token=context["session_token"], site_id=scope.site_id)
    with pytest.raises(AuthorizationDenied):
        await service.create_key(
            session_token=context["session_token"],
            site_id=scope.site_id,
            key_id=uuid4(),
            secret_options={"transport": httpx2.MockTransport(double.handle)},
        )
    assert double.writes == 2


@pytest.mark.parametrize(
    "table",
    [
        "indexnow_key_intents",
        "indexnow_keys",
        "indexnow_key_retirements",
        "indexnow_key_revisions",
        "indexnow_outbox",
        "indexnow_receipts",
    ],
)
def test_indexnow_tables_have_forced_rls_and_no_runtime_direct_access(admin, identity, table):
    assert admin.execute(
        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
        ("app." + table,),
    ).fetchone() == (True, True)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        identity.execute(f"SELECT * FROM app.{table}")


async def exercise_indexnow_queue(admin, identity, scope, context, operation_id, tmp_path):
    rows = admin.execute(
        "SELECT id,url,state FROM app.indexnow_outbox WHERE change_id=%s", (operation_id,)
    ).fetchall()
    assert len(rows) == 1 and rows[0][2] == "pending"
    if os.environ.get("SIGNAL_TEST_INDEXNOW_JOURNAL_DSN"):
        await exercise_indexnow_dispatch(
            admin, identity, scope, context, operation_id, rows[0][0], tmp_path
        )
        return
    key = OpenBaoIndexNowKeys("https://bao.example.invalid", "synthetic-indexnow-bao-token")
    service = IndexNowService(identity, key, context["generation"])
    worker, attempt = uuid4(), uuid4()
    row = service._call("claim_indexnow", context["session_token"], scope.site_id, worker, attempt)
    assert row[-1] == "EC_142_KEY_NOT_CREATED"
    assert service._call(
        "finish_indexnow",
        context["session_token"],
        scope.site_id,
        rows[0][0],
        worker,
        attempt,
        None,
        None,
        None,
        row[-1],
    ) == ("skipped",)
    projection = service.read(session_token=context["session_token"], site_id=scope.site_id)
    assert projection["submissions"][0]["urls"] == [rows[0][1]]
    assert projection["submissions"][0]["provider_status"] is None
    assert (
        service._call("claim_indexnow", context["session_token"], scope.site_id, worker, uuid4())
        is None
    )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute("DELETE FROM app.indexnow_receipts WHERE id=%s", (attempt,))


async def exercise_indexnow_dispatch(
    admin, identity, scope, context, operation_id, outbox_id, tmp_path
):
    with ExitStack() as stack:
        connections = {
            name: stack.enter_context(
                psycopg.connect(os.environ[f"SIGNAL_TEST_{name.upper()}_DSN"], autocommit=True)
            )
            for name in (
                "api",
                "scheduler",
                "workflow",
                "crawl_admission",
                "crawl_ingest",
                "release_manager",
            )
        }
        journal_connection = stack.enter_context(
            psycopg.connect(os.environ["SIGNAL_TEST_INDEXNOW_JOURNAL_DSN"], autocommit=True)
        )
        journal = WriteIntentJournal(
            journal_connection,
            _JOURNAL_SIGNER.public_key(),
            _JOURNAL_CIPHER_KEY,
            _JOURNAL_SIGNER,
        )
        extension_id = admin.execute(
            "SELECT extension_id FROM app.github_pr_operations WHERE id=%s", (operation_id,)
        ).fetchone()[0]
        extension = read_github_pr_extension(
            identity,
            session_token=context["session_token"],
            site_id=scope.site_id,
            current_recovery_generation=context["generation"],
            extension_id=extension_id,
        )
        service, key_id, key, revision_id, canonical, double = await seed_key_revision(
            admin,
            connections["api"],
            identity,
            connections["release_manager"],
            scope,
            context,
            extension,
            version="1.0." + str(uuid4().int % 1_000_000_000),
        )
        step_up(admin, context)
        decision = decide_authenticated_candidate_recipe_revision(
            identity,
            session_token=context["session_token"],
            requested_site_id=scope.site_id,
            current_recovery_generation=context["generation"],
            revision_id=revision_id,
            expected_revision_sha256=hashlib.sha256(canonical).hexdigest(),
            decision_id=uuid4(),
            decision="approved",
        )
        prepared = identity.execute(
            "SELECT * FROM control.prepare_github_pr_operation(%s,%s,%s,%s,%s,%s,%s)",
            (
                hashlib.sha256(context["session_token"].encode()).digest(),
                scope.site_id,
                context["generation"],
                revision_id,
                bytes.fromhex(decision.revision_sha256),
                uuid4(),
                hashlib.sha256(b"synthetic-key-pr-intent").digest(),
            ),
        ).fetchone()
        assert prepared[-1] == "prepared"
        # Fixture an opened approved PR; Git object creation is qualified separately.
        admin.execute(
            "UPDATE app.github_pr_operations SET state='opened',step='done',pr_number=86,"
            "pr_url='https://github.com/SignalOwner/website/pull/86' WHERE id=%s",
            (prepared[0],),
        )
        origin = admin.execute(
            "SELECT primary_origin FROM app.sites WHERE id=%s", (scope.site_id,)
        ).fetchone()[0]
        store = EncryptedLocalArtifactStore(tmp_path / "indexnow-egress")

        class ProviderDouble:
            def __init__(self):
                self.calls = []
                self.statuses = [200]
                self.key_body = key.encode()
                self.key_status = 200
                self.error = False
                self.key_media = "text/plain"
                self.key_outcome = "fetched"

            def request(self, outbound, *, policy):
                self.calls.append(outbound)
                if outbound.method == "POST" and self.error:
                    raise CrawlFetchUnavailable("synthetic-lost-response")
                body = self.key_body if outbound.method == "GET" else b"{}"
                status = self.key_status if outbound.method == "GET" else self.statuses.pop(0)
                from signal_core.crawl_http import EgressHttpResult

                from tests.control_plane.test_crawl_robots import robots_result

                media = self.key_media if outbound.method == "GET" else "application/json"
                outcome = self.key_outcome if outbound.method == "GET" else "fetched"
                if media not in outbound.accepted_media_types:
                    outcome = "unsupported_media_type"
                if outcome != "fetched":
                    body = b""
                return EgressHttpResult(
                    1,
                    outbound.url,
                    outbound.url,
                    outbound.method,
                    outcome,
                    status,
                    media,
                    (("content-type", media),),
                    robots_result().resolved_address,
                    body,
                    hashlib.sha256(body).hexdigest() if outcome == "fetched" else None,
                    len(body),
                    0,
                )

        provider = ProviderDouble()

        def reset():
            admin.execute(
                "UPDATE app.indexnow_outbox SET state='pending',attempt_count=0,available_at=now() "
                "WHERE id=%s",
                (outbox_id,),
            )
            admin.execute(
                "UPDATE control.origin_buckets SET next_allowed_at=now(),degraded_until=NULL"
            )

        def egress(target):
            run, policy, _ = authority(
                connections["api"],
                connections["scheduler"],
                connections["workflow"],
                connections["crawl_admission"],
                connections["crawl_ingest"],
                scope,
                "indexnow-" + uuid4().hex,
                store,
                origin_override=target,
                verification_profile=True,
                seed_url=origin + "/" + key + ".txt" if target == origin else None,
            )
            return SharedEgressProvider(
                connections["crawl_admission"],
                connections["crawl_ingest"],
                store,
                run,
                policy,
                provider,
                "indexnow.test",
                OriginAdmissionPolicy(),
                None,
                "connector",
            )

        for statuses, expected in [
            ([429, 200], "accepted"),
            ([503, 202], "accepted"),
            ([400], "rejected"),
            ([429] * 4, "exhausted"),
        ]:
            # Administrative resets isolate scenarios; runtime has no reset privilege.
            reset()
            provider.statuses = statuses.copy()
            while provider.statuses:
                key_gateway, submit_gateway = egress(origin), egress("https://api.indexnow.org")
                result = await service.dispatch_one(
                    session_token=context["session_token"],
                    site_id=scope.site_id,
                    worker_id=uuid4(),
                    key_egress=key_gateway,
                    submit_egress=submit_gateway,
                    journal=journal,
                    secret_options={"transport": httpx2.MockTransport(double.handle)},
                )
                if result == "retry":
                    admin.execute(
                        "UPDATE app.indexnow_outbox SET available_at=now() WHERE id=%s",
                        (outbox_id,),
                    )
                    admin.execute(
                        "UPDATE control.origin_buckets SET next_allowed_at=now(),"
                        "degraded_until=NULL"
                    )
                else:
                    assert result == expected
            before = len(provider.calls)
            assert (
                await service.dispatch_one(
                    session_token=context["session_token"],
                    site_id=scope.site_id,
                    worker_id=uuid4(),
                    key_egress=key_gateway,
                    submit_egress=submit_gateway,
                    journal=journal,
                    secret_options={"transport": httpx2.MockTransport(double.handle)},
                )
                == "idle"
            )
            assert len(provider.calls) == before
        _, entries = journal.verify_stream()
        assert all(
            record.url == origin + "/" for record, _ in entries if record.site_id == scope.site_id
        )
        assert all(
            json.loads(call.body)["urlList"] == [origin + "/"]
            for call in provider.calls
            if call.method == "POST"
        )
        for body, status, media, reason in [
            (b"", 404, "text/plain", "EC_142_KEY_MISSING"),
            (b"different", 200, "text/plain", "EC_142_KEY_MISMATCH"),
            (key.encode(), 200, "text/html", "EC_142_KEY_CONTENT_TYPE"),
        ]:
            reset()
            provider.key_body, provider.key_status, provider.key_media = body, status, media
            before = sum(call.method == "POST" for call in provider.calls)
            assert (
                await service.dispatch_one(
                    session_token=context["session_token"],
                    site_id=scope.site_id,
                    worker_id=uuid4(),
                    key_egress=egress(origin),
                    submit_egress=egress("https://api.indexnow.org"),
                    journal=journal,
                    secret_options={"transport": httpx2.MockTransport(double.handle)},
                )
                == "skipped"
            )
            assert (
                service.read(session_token=context["session_token"], site_id=scope.site_id)[
                    "submissions"
                ][0]["reason"]
                == reason
            )
            assert sum(call.method == "POST" for call in provider.calls) == before
        reset()
        provider.key_media = "text/plain"
        provider.key_outcome = "redirect_rejected"
        before = sum(call.method == "POST" for call in provider.calls)
        assert (
            await service.dispatch_one(
                session_token=context["session_token"],
                site_id=scope.site_id,
                worker_id=uuid4(),
                key_egress=egress(origin),
                submit_egress=egress("https://api.indexnow.org"),
                journal=journal,
                secret_options={"transport": httpx2.MockTransport(double.handle)},
            )
            == "skipped"
        )
        assert (
            service.read(session_token=context["session_token"], site_id=scope.site_id)[
                "submissions"
            ][0]["reason"]
            == "EC_142_KEY_REDIRECT"
        )
        assert sum(call.method == "POST" for call in provider.calls) == before

        reset()
        key_gateway, submit_gateway = egress(origin), egress("https://api.indexnow.org")
        before = len(provider.calls)
        recheck = admin.execute(
            "SELECT recheck_at FROM control.public_origin_claims WHERE origin=%s",
            (origin,),
        ).fetchone()[0]
        admin.execute(
            "UPDATE control.public_origin_claims SET recheck_at=now()-interval '1 second' "
            "WHERE origin=%s",
            (origin,),
        )
        try:
            assert (
                await service.dispatch_one(
                    session_token=context["session_token"],
                    site_id=scope.site_id,
                    worker_id=uuid4(),
                    key_egress=key_gateway,
                    submit_egress=submit_gateway,
                    journal=journal,
                    secret_options={"transport": httpx2.MockTransport(double.handle)},
                )
                == "skipped"
            )
            assert (
                service.read(session_token=context["session_token"], site_id=scope.site_id)[
                    "submissions"
                ][0]["reason"]
                == "EC_142_SITE_UNVERIFIED"
            )
            assert len(provider.calls) == before
        finally:
            admin.execute(
                "UPDATE control.public_origin_claims SET recheck_at=%s WHERE origin=%s",
                (recheck, origin),
            )
        reset()
        provider.key_outcome = "fetched"
        provider.key_body, provider.key_status, provider.key_media = key.encode(), 200, "text/plain"
        provider.error = True
        assert (
            await service.dispatch_one(
                session_token=context["session_token"],
                site_id=scope.site_id,
                worker_id=uuid4(),
                key_egress=egress(origin),
                submit_egress=egress("https://api.indexnow.org"),
                journal=journal,
                secret_options={"transport": httpx2.MockTransport(double.handle)},
            )
            == "outcome_unknown"
        )
        # Simulate restoring the primary outbox without losing the independent intent.
        reset()
        provider.error = False
        before = sum(call.method == "POST" for call in provider.calls)
        assert (
            await service.dispatch_one(
                session_token=context["session_token"],
                site_id=scope.site_id,
                worker_id=uuid4(),
                key_egress=egress(origin),
                submit_egress=egress("https://api.indexnow.org"),
                journal=journal,
                secret_options={"transport": httpx2.MockTransport(double.handle)},
            )
            == "outcome_unknown"
        )
        assert sum(call.method == "POST" for call in provider.calls) == before


def step_up(admin, context):
    now = admin.execute("SELECT statement_timestamp()").fetchone()[0]
    admin.execute(
        "UPDATE control.identity_sessions SET authentication_level='mfa',auth_time=%s,"
        "last_seen_at=%s WHERE id=%s",
        (now, now, context["identity_session_id"]),
    )
    admin.execute(
        "UPDATE app.sessions SET mfa_level='mfa',auth_time=%s,last_seen_at=%s WHERE id=%s",
        (now, now, context["tenant_session_id"]),
    )
