"""Exact editorial authority on real PostgreSQL, journal and shared GitHub egress."""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
import rfc8785
from anyio import sleep
from psycopg.types.json import Jsonb
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.autonomy_gate import _decision_request
from signal_core.business_brain import (
    FactCategory,
    FactProvenance,
    approve_fact,
    correct_fact,
    propose_fact,
    remove_fact,
)
from signal_core.candidate_build_service import build_candidate_from_github
from signal_core.content_writer import (
    ContentWriterUnavailable,
    grounding_report,
    originality_report,
)
from signal_core.content_writer_service import writer_call
from signal_core.database import scoped_transaction
from signal_core.github_delivery_observation import observe_github_delivery
from signal_core.github_pr_delivery import GitHubPrDeliveryUnavailable, open_approved_github_pr
from signal_core.github_pr_extension import GitHubPrExtensionUnavailable
from signal_core.github_read_binding import revoke_github_read_binding
from signal_core.weekly_delivery import workload_handle
from signal_core.weekly_delivery_authority import WeeklyDeliveryConnection

from tests.control_plane.measurement_support import bind_sources, import_fixture
from tests.control_plane.test_gsc_binding import session_args
from tests.control_plane.test_technical_recipes import release_manager as release_manager
from tests.delivery.test_autonomy_delivery import delivery_harness as delivery_harness
from tests.delivery.test_autonomy_delivery import prepare_candidate


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def editorial(delivery_harness, monkeypatch):
    h = delivery_harness
    h.io = h.io_factory(h.cycle, None)
    from signal_core.github_read_binding import OpenBaoGitHubAppCredential

    original = OpenBaoGitHubAppCredential.credentials

    async def credentials(self, **kwargs):
        return await original(self, transport=h.io.openbao_transport)

    monkeypatch.setattr(OpenBaoGitHubAppCredential, "credentials", credentials)
    h.common = (h.context["session_token"], h.context["generation"], h.scope.site_id)
    h.admin.execute(
        "UPDATE control.identity_sessions SET authentication_level='mfa' WHERE id=%s",
        (h.context["identity_session_id"],),
    )
    h.admin.execute(
        "UPDATE app.sessions SET mfa_level='mfa' WHERE id=%s",
        (h.context["tenant_session_id"],),
    )
    propose_fact(
        h.api,
        session_token=h.common[0],
        current_recovery_generation=h.common[1],
        site_id=h.scope.site_id,
        category=FactCategory.AUDIENCE,
        statement="Founders are our audience.",
        provenance=FactProvenance("owner_statement"),
    )
    h.fact = h.admin.execute(
        "SELECT id FROM app.business_brain_facts WHERE site_id=%s", (h.scope.site_id,)
    ).fetchone()[0]
    approve_fact(
        h.api,
        session_token=h.common[0],
        current_recovery_generation=h.common[1],
        site_id=h.scope.site_id,
        fact_id=h.fact,
    )
    return h


async def seal(
    h,
    *,
    kind="new_article",
    flags=False,
    destination=None,
    manifest_path=None,
    strip_flags=False,
    receipt_override=None,
):
    h.brief, h.draft = uuid4(), uuid4()
    destination = destination or ("new.html" if kind == "new_article" else "index.html")
    sentence = {"text": "Founders are our audience.", "fact_ids": [str(h.fact)]}
    a = {
        "title": sentence,
        "meta_description": sentence,
        "sections": [{"heading": sentence, "sentences": [sentence]}],
        "internal_links": [],
    }
    grounding = grounding_report(
        a,
        [
            {
                "fact_id": str(h.fact),
                "category": "audience",
                "statement": "Founders are our audience.",
            }
        ],
        selected_ids={str(h.fact)},
        extra_flags=["title"] if flags else [],
    )
    originality = originality_report(
        a, [{"source_id": str(uuid4()), "text": "Existing research from a synthetic test source."}]
    )
    payload = {
        "state": grounding["state"],
        "article": a,
        "grounding": grounding,
        "originality": originality,
    }
    canonical = rfc8785.dumps(payload)
    scope = (h.scope.tenant_id, h.scope.site_id)
    h.admin.execute(
        "INSERT INTO app.content_briefs(tenant_id,site_id,id,payload,origin,created_by) "
        "VALUES(%s,%s,%s,%s,'owner',%s)",
        (*scope, h.brief, Jsonb({"kind": kind, "fact_ids": [str(h.fact)]}), h.context["user_id"]),
    )
    h.admin.execute(
        "INSERT INTO app.content_brief_acceptances(tenant_id,site_id,brief_id,actor_id) "
        "VALUES(%s,%s,%s,%s)",
        (*scope, h.brief, h.context["user_id"]),
    )
    h.admin.execute(
        "INSERT INTO app.content_draft_intents(tenant_id,site_id,id,brief_id,writer_version,"
        "input_sha256,fact_snapshot) VALUES(%s,%s,%s,%s,'content-writer-v1',%s,%s)",
        (
            *scope,
            h.draft,
            h.brief,
            sha256(canonical).digest(),
            Jsonb(
                [
                    {
                        "fact_id": str(h.fact),
                        "category": "audience",
                        "statement": "Founders are our audience.",
                    }
                ]
            ),
        ),
    )
    h.admin.execute(
        "INSERT INTO app.content_draft_results(tenant_id,site_id,draft_id,payload,canonical,"
        "sha256) "
        "VALUES(%s,%s,%s,%s,%s,%s)",
        (*scope, h.draft, Jsonb(payload), canonical, sha256(canonical).digest()),
    )
    before = h.double.files.get(destination, b"")
    after = (
        b'<html><head><title>Founders</title><meta name="description" '
        b'content="Founders are our audience."></head><body><article>'
        b"Founders are our audience.</article></body></html>"
    )
    build = await build_candidate_from_github(
        h.identity,
        session_token=h.common[0],
        current_recovery_generation=h.common[1],
        site_id=h.scope.site_id,
        extension_id=h.extension.id,
        idempotency_key=uuid4(),
        credential=h.io.credential,
        github_transport=h.io.github_read_transport,
        runner=h.io.runner,
        patch={destination: after},
        approved_paths=frozenset({destination}),
    )
    receipt = asdict(build)
    receipt["id"] = str(build.id)
    receipt["site_id"] = str(h.scope.site_id)
    receipt.update(receipt_override or {})
    if strip_flags:
        grounding = grounding_report(
            a,
            [{"fact_id": str(h.fact), "category": "audience", "statement": sentence["text"]}],
            selected_ids={str(h.fact)},
        )
    manifest = {
        "schema_version": 1,
        "site_id": str(h.scope.site_id),
        "draft_id": str(h.draft),
        "extension_id": str(h.extension.id),
        "build_id": str(build.id),
        "base_sha": build.base_sha,
        "patch_sha256": build.patch_sha256,
        "changed_files": [
            {
                "path": manifest_path or destination,
                "before": before.decode(),
                "after": after.decode(),
                "source_sha256": sha256(before).hexdigest(),
                "result_sha256": sha256(after).hexdigest(),
            }
        ],
        "grounding": grounding,
        "originality": originality,
        "approval_class": "A2",
        "work_type": kind,
        "threshold": 0.95,
        "autonomy_eligible": False,
        "expected_impact": "Owner-reviewed content; no ranking guarantee.",
        "recovery_plan": "Separate owner-reviewed inverse patch; no deletion.",
        "build_receipt": receipt,
    }
    canonical = rfc8785.dumps(manifest)
    h.digest = sha256(canonical).digest()
    h.candidate = uuid4()
    h.operation = uuid4()
    h.worker = uuid4()
    h.manifest = manifest
    assert (
        writer_call(
            h.api,
            *h.common,
            "seal",
            h.candidate,
            h.draft,
            h.extension.id,
            build.id,
            canonical,
            h.digest,
        )["state"]
        == "sealed"
    )
    return h


def approve(h, acknowledgements=()):
    return writer_call(
        h.api, *h.common, "approve_delivery", h.candidate, h.digest, Jsonb(list(acknowledgements))
    )


async def dispatch(h, *, connection=None):
    return await open_approved_github_pr(
        connection or h.identity,
        h.api,
        session_token=h.common[0],
        current_recovery_generation=h.common[1],
        site_id=h.scope.site_id,
        revision_id=h.candidate,
        expected_revision_sha256=h.digest.hex(),
        operation_id=h.operation,
        worker_id=h.worker,
        journal=h.io.journal,
        credential=h.io.credential,
        github_read_transport=h.io.github_read_transport,
        github_write_egress=h.io.github_egress,
        openbao_transport=h.io.openbao_transport,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["new_article", "content_refresh"])
async def test_article_approval_dispatch_replay_and_observation(editorial, kind):
    h = editorial
    bindings, existing_page = bind_sources(h.admin, h.identity, h.scope, h.context)
    page = existing_page + ("new.html" if kind == "new_article" else "")
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    baseline_ids = import_fixture(
        h.admin, h.scope, bindings, page, today - timedelta(days=90), today - timedelta(days=1)
    )
    h = await seal(editorial, kind=kind)
    assert approve(h) == "approved" and approve(h) == "replayed"
    assert (await dispatch(h)).state == "opened"
    count = len(h.double.calls)
    assert (await dispatch(h)).state == "opened" and len(h.double.pulls) == 1
    assert len(h.double.calls) == count
    tree = h.double.trees[h.double.commits[h.double.pulls[0]["head"]["sha"]]["tree"]["sha"]]
    assert {p for p in tree if tree[p] != h.double.files.get(p)} == {
        h.manifest["changed_files"][0]["path"]
    }
    body = h.double.pulls[0]["body"]
    for value in (
        "owner_editorial",
        str(h.context["user_id"]),
        str(h.candidate),
        h.digest.hex(),
        "Grounding:",
        "Originality:",
    ):
        assert value in body
    h.io = h.io_factory(
        h.cycle, None, verification_path="new.html" if kind == "new_article" else ""
    )
    result = await observe_github_delivery(
        h.identity,
        session_token=h.common[0],
        current_recovery_generation=h.common[1],
        site_id=h.scope.site_id,
        operation_id=h.operation,
        attempt_id=uuid4(),
        environment="production",
        trusted_deployment_actor_id=56,
        credential=h.io.credential,
        github_egress=h.io.github_egress,
        live_verifier=h.io.live_verifier,
        openbao_transport=h.io.openbao_transport,
    )
    assert result["delivery_certified"] is False
    assert result["provider"]["stage"] == "deployed"
    assert result["outcome"] == "verified"
    assert result["live"]["matched"] is True
    plans = h.admin.execute(
        "SELECT horizon,baseline_start,baseline_end,baseline,post_start,post_end,due_at,"
        "verified_live_at,verification_attempt_id,page_url FROM app.change_measurement_plans "
        "WHERE operation_id=%s ORDER BY horizon",
        (h.operation,),
    ).fetchall()
    assert [p[0] for p in plans] == [7, 28, 90]
    for horizon, before_start, before_end, baseline, start, end, due, live, _attempt, url in plans:
        assert url == page
        assert (end - start).days == horizon - 1
        assert start == live.astimezone(ZoneInfo("America/Los_Angeles")).date() + timedelta(days=1)
        assert due - live == timedelta(days=horizon)
        if kind == "new_article":
            assert before_start is None and before_end is None
            assert baseline["state"] == "new_page"
            assert baseline["reason"] == "NO_PRE_CHANGE_WINDOW"
            for source in ("gsc_page", "bing_site_context", "bing_page"):
                assert baseline[source] == {
                    "state": "unavailable",
                    "reason": "NO_PRE_CHANGE_WINDOW",
                    "generation_id": None,
                    "coverage": None,
                    "metrics": None,
                }
        else:
            assert before_end == today - timedelta(days=1)
            assert (before_end - before_start).days == horizon - 1
            assert baseline["gsc_page"]["generation_id"] == str(baseline_ids[0])
            assert baseline["gsc_page"]["metrics"]["clicks"] == horizon * 10

        def measure(now, horizon=horizon):
            return h.admin.execute(
                "SELECT control.measure_change_horizon(%s,%s,%s,%s,%s)",
                (h.scope.tenant_id, h.scope.site_id, h.operation, horizon, now),
            ).fetchone()[0]

        assert measure(datetime.now(UTC))["state"] == "not_yet_due"
        closed = datetime.combine(
            end + timedelta(days=1), datetime.min.time(), ZoneInfo("America/Los_Angeles")
        )
        if due < closed:
            assert measure(due)["reason"] == "POST_WINDOW_NOT_CLOSED"
        now = closed + timedelta(hours=1)
        assert measure(now)["state"] == "awaiting_data"
        import_fixture(h.admin, h.scope, bindings, page, start, end, lag=True, clicks=12)
        assert measure(now)["reason"] == "PROVIDER_LAG"
        generation, _, coverage, _ = import_fixture(
            h.admin, h.scope, bindings, page, start, end, clicks=12
        )
        measured = measure(now)
        assert measured["state"] == "measured_as_reported"
        assert measured["post"]["gsc_page"]["generation_id"] == str(generation)
        assert measured["post"]["gsc_page"]["coverage"] == coverage
        assert measured["post"]["gsc_page"]["metrics"]["clicks"] == horizon * 12
        assert measured["observed_change"]["gsc_page"]["clicks"] == (
            None if kind == "new_article" else horizon * 2
        )
        if kind == "new_article":
            assert all(
                value is None
                for metrics in measured["observed_change"].values()
                for value in metrics.values()
            )
        count = h.admin.execute(
            "SELECT count(*) FROM app.change_measurement_observations "
            "WHERE operation_id=%s AND horizon=%s",
            (h.operation, horizon),
        ).fetchone()[0]
        assert measure(now) == measured
        assert (
            h.admin.execute(
                "SELECT count(*) FROM app.change_measurement_observations "
                "WHERE operation_id=%s AND horizon=%s",
                (h.operation, horizon),
            ).fetchone()[0]
            == count
        )

    # Replaying the verified receipt cannot replace the baseline or its evidence.
    for _ in range(2):
        h.admin.execute(
            "SELECT control.capture_change_measurement_baseline(r) "
            "FROM app.github_delivery_receipts r WHERE operation_id=%s",
            (h.operation,),
        )
    assert (
        h.admin.execute(
            "SELECT horizon,baseline_start,baseline_end,baseline,post_start,post_end,due_at,"
            "verified_live_at,verification_attempt_id,page_url FROM app.change_measurement_plans "
            "WHERE operation_id=%s ORDER BY horizon",
            (h.operation,),
        ).fetchall()
        == plans
    )
    projection = h.admin.execute(
        "SELECT control.change_measurement_projection(%s,%s,NULL)",
        (h.scope.tenant_id, h.scope.site_id),
    ).fetchone()[0]
    from signal_api.weekly_report_contracts import ChangeMeasurementResponse

    assert all(ChangeMeasurementResponse.model_validate(item) for item in projection)
    from signal_core.observed_learning import effectiveness
    from signal_core.seo_strategy_service import strategy_call

    sources = strategy_call(
        h.api,
        session_token=h.context["session_token"],
        generation=h.context["generation"],
        site_id=h.scope.site_id,
        action="sources",
    )
    learned = sources["learning"]["measurements"]
    assert [item["horizon"] for item in learned] == [28, 90]
    assert all(item["operation_id"] == str(h.operation) for item in learned)
    assert all(
        item["work_type"] == kind and item["recipe_key"] == "owner_editorial" for item in learned
    )
    panel = effectiveness(learned)
    if kind == "new_article":
        assert all(item["baseline_window"] == {"start": None, "end": None} for item in learned)
        assert panel["groups"] == [] and panel["excluded_measurements"] == 2
    else:
        assert all(
            item["baseline_window"]["start"] and item["baseline_window"]["end"] for item in learned
        )
        assert panel["groups"] and all(
            group["sample_size"] == 1 and group["factor"] == 1 for group in panel["groups"]
        )
    await replay_measurement(h, 90, now)
    assert (
        h.identity.execute(
            "SELECT outcome FROM control.revoke_gsc_binding(%s,%s,%s,%s,%s)",
            (*session_args(h.context, h.scope.site_id), bindings["gsc"], uuid4()),
        ).fetchone()[0]
        == "revoked"
    )
    assert measure(now)["state"] == "unavailable"


async def replay_measurement(h, horizon, observation_time):
    import asyncio
    import os

    from signal_core.change_measurement import ChangeMeasurementJob, ChangeMeasurementWorkflow
    from temporalio import activity
    from temporalio.exceptions import ApplicationError
    from temporalio.worker import Replayer, Worker

    from tests.temporal_runtime import start_temporal

    attempts = 0

    @activity.defn(name="signal.change.measure.v1")
    async def committed_then_lost(job: ChangeMeasurementJob) -> str:
        nonlocal attempts
        attempts += 1
        value = h.admin.execute(
            "SELECT control.measure_change_horizon(%s,%s,%s,%s,%s)",
            (h.scope.tenant_id, h.scope.site_id, h.operation, job.horizon, observation_time),
        ).fetchone()[0]
        if attempts == 1:
            raise ApplicationError("Synthetic worker exit after article measurement commit.")
        return value["state"]

    environment = await start_temporal(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    try:
        job = ChangeMeasurementJob(
            str(h.scope.tenant_id),
            str(h.scope.site_id),
            str(h.operation),
            horizon,
            (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        )
        async with Worker(
            environment.client,
            task_queue="article-measurement-" + uuid4().hex,
            workflows=[ChangeMeasurementWorkflow],
            activities=[committed_then_lost],
        ) as worker:
            handle = await environment.client.start_workflow(
                ChangeMeasurementWorkflow.run,
                job,
                id=job.workflow_id,
                task_queue=worker.task_queue,
            )
            assert await asyncio.wait_for(handle.result(), 30) == "measured_as_reported"
            history = await handle.fetch_history()
        assert attempts == 2
        await Replayer(workflows=[ChangeMeasurementWorkflow]).replay_workflow(history)
    finally:
        await environment.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [
        "brief",
        "removed_fact",
        "superseded_fact",
        "base",
        "binding",
        "rebuild",
        "role",
        "tenant",
        "generation",
    ],
)
async def test_stale_approval_and_dispatch_make_no_writes(editorial, change):
    h = await seal(editorial)
    assert approve(h) == "approved"
    if change == "brief":
        h.admin.execute(
            "INSERT INTO app.content_briefs(tenant_id,site_id,id,payload,origin,supersedes_id,"
            "created_by) VALUES(%s,%s,%s,'{}','owner',%s,%s)",
            (h.scope.tenant_id, h.scope.site_id, uuid4(), h.brief, h.context["user_id"]),
        )
    elif change in {"removed_fact", "superseded_fact"}:
        fn = remove_fact if change == "removed_fact" else correct_fact
        fn(
            h.api,
            session_token=h.common[0],
            current_recovery_generation=h.common[1],
            site_id=h.scope.site_id,
            fact_id=h.fact,
            **(
                {"statement": "A changed approved statement."}
                if change == "superseded_fact"
                else {}
            ),
        )
    elif change == "base":
        h.double.base_sha = "c" * 40
    elif change == "binding":
        revoke_github_read_binding(
            h.identity,
            session_token=h.common[0],
            current_recovery_generation=h.common[1],
            site_id=h.scope.site_id,
            binding_id=h.extension.binding_id,
        )
    elif change == "rebuild":
        h.admin.execute(
            "UPDATE app.candidate_build_intents SET patch_sha256=%s WHERE id=%s",
            ("f" * 64, UUID(h.manifest["build_id"])),
        )
    elif change == "role":
        h.admin.execute(
            "UPDATE app.memberships SET role_key='viewer',"
            "authorization_epoch=authorization_epoch+1 "
            "WHERE id=%s",
            (h.context["membership_id"],),
        )
    elif change == "tenant":
        h.common = (h.common[0], h.common[1], uuid4())
        h.scope = replace(h.scope, site_id=h.common[2])
    elif change == "generation":
        h.common = (h.common[0], "synthetic-new-generation", h.common[2])
    if change in {"role", "tenant", "generation"}:
        with pytest.raises(ContentWriterUnavailable, match="owner_access_denied"):
            approve(h)
    elif change != "base":
        assert approve(h) == "candidate_stale"
    with pytest.raises(
        (
            GitHubPrDeliveryUnavailable,
            GitHubPrExtensionUnavailable,
            AuthorizationDenied,
            InvalidSession,
        )
    ):
        await dispatch(h)
    assert not h.double.pulls and not any(
        c.method == "POST" and "/repos/" in c.url for c in h.double.calls
    )


@pytest.mark.anyio
@pytest.mark.parametrize("session", ["primary", "stale"])
async def test_article_approval_requires_fresh_dashboard_mfa(editorial, session):
    h = await seal(editorial)
    if session == "stale":
        h.admin.execute(
            "UPDATE app.sessions SET auth_time=now()-interval '6 minutes' WHERE id=%s",
            (h.context["tenant_session_id"],),
        )
        h.admin.execute(
            "UPDATE control.identity_sessions SET auth_time="
            "(SELECT auth_time FROM app.sessions WHERE id=%s) WHERE id=%s",
            (h.context["tenant_session_id"], h.context["identity_session_id"]),
        )
    else:
        h.admin.execute(
            "UPDATE control.identity_sessions SET authentication_level='primary' WHERE id=%s",
            (h.context["identity_session_id"],),
        )
        h.admin.execute(
            "UPDATE app.sessions SET mfa_level='primary' WHERE id=%s",
            (h.context["tenant_session_id"],),
        )
    assert approve(h) == "step_up_required"
    assert not h.double.pulls


@pytest.mark.anyio
async def test_owner_required_exact_sentence_acknowledgements(editorial):
    h = await seal(editorial, flags=True)
    assert approve(h) == "acknowledgements_required"
    assert approve(h, ["not-the-sentence"]) == "acknowledgements_required"
    assert approve(h, ["title", "title"]) == "acknowledgements_required"
    assert approve(h, ["title"]) == "approved"
    assert h.admin.execute(
        "SELECT acknowledged_sentences FROM app.content_delivery_decisions WHERE candidate_id=%s",
        (h.candidate,),
    ).fetchone() == (["title"],)
    assert (await dispatch(h)).state == "opened"


@pytest.mark.anyio
async def test_sealed_manifest_cannot_erase_draft_owner_required_flags(editorial):
    h = await seal(editorial, flags=True, strip_flags=True)
    assert approve(h) == "candidate_stale"
    with pytest.raises(GitHubPrDeliveryUnavailable):
        await dispatch(h)
    assert not h.double.pulls


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["rejected", "changes_requested"])
async def test_editorial_denial_after_delivery_approval_stops_dispatch(editorial, decision):
    h = await seal(editorial)
    assert approve(h) == "approved"
    assert writer_call(h.api, *h.common, "review", h.candidate, h.digest, decision) == "reviewed"
    assert approve(h) == "conflict"
    with pytest.raises(GitHubPrDeliveryUnavailable, match="editorial_authority_stale"):
        await dispatch(h)
    assert not h.double.pulls and not any(
        c.method == "POST" and "/repos/" in c.url for c in h.double.calls
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field,value", [("id", str(uuid4())), ("base_sha", "f" * 40), ("patch_sha256", "f" * 64)]
)
async def test_article_exact_build_receipt_required(editorial, field, value):
    h = await seal(editorial, receipt_override={field: value})
    assert approve(h) == "candidate_stale"
    with pytest.raises(GitHubPrDeliveryUnavailable):
        await dispatch(h)
    assert not h.double.pulls


@pytest.mark.anyio
@pytest.mark.parametrize("step", ["tree", "commit", "branch", "pr"])
async def test_article_lost_response_reconciliation_no_duplicate(editorial, step):
    h = await seal(editorial)
    assert approve(h) == "approved"
    h.double.lost_step = step
    with pytest.raises(GitHubPrDeliveryUnavailable, match="OUTCOME_UNKNOWN"):
        await dispatch(h)
    assert (await dispatch(h)).state == "opened" and len(h.double.pulls) == 1
    assert (await dispatch(h)).state == "opened" and len(h.double.pulls) == 1


@pytest.mark.anyio
async def test_editorial_authority_immutable_and_cap_rechecked(editorial):
    h = await seal(editorial)
    assert approve(h) == "approved"
    assert (await dispatch(h)).state == "opened"
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="immutable_record"):
        h.admin.execute(
            "UPDATE app.content_delivery_decisions SET acknowledged_sentences='[]' "
            "WHERE candidate_id=%s",
            (h.candidate,),
        )
    for field, value in [
        ("authority_kind", "standing_grant"),
        ("editorial_decision_id", str(uuid4())),
        ("revision_sha256", b"x" * 32),
        ("recovery_generation", "synthetic-other"),
    ]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            h.admin.execute(
                f"UPDATE app.github_pr_operations SET {field}=%s WHERE id=%s", (value, h.operation)
            )
    await seal(h, destination="second.html")
    assert approve(h) == "approved"
    writer_call(h.api, *h.common, "set_cap", 1)
    with pytest.raises(GitHubPrDeliveryUnavailable, match="article_cap_exhausted"):
        await dispatch(h)
    assert len(h.double.pulls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["planned", "opened"])
async def test_article_weekly_cap_cannot_roll_off_a_recent_effect_or_resume_aged_intent(
    editorial, state
):
    h = await seal(editorial)
    assert approve(h) == "approved"
    # Seed elapsed time without bypassing the immutable authority trigger.
    with scoped_transaction(h.admin, h.scope):
        seed_aged_operation(h, state)
    if state == "opened":
        await seal(h, destination="second.html")
        assert approve(h) == "approved"
        writer_call(h.api, *h.common, "set_cap", 1)
    with pytest.raises(
        GitHubPrDeliveryUnavailable,
        match="article_cap_exhausted" if state == "opened" else "editorial_authority_stale",
    ):
        await dispatch(h)
    assert not h.double.pulls


def seed_aged_operation(h, state):
    h.admin.execute(
        "INSERT INTO app.github_pr_operations(tenant_id,site_id,id,candidate_revision_id,"
        "revision_sha256,intent_sha256,extension_id,binding_id,requested_by_user_id,"
        "recovery_generation,membership_epoch,site_epoch,base_sha,branch_name,step,state,"
        "authority_kind,editorial_decision_id,decision_channel,created_at,updated_at,"
        "pr_number,pr_url) "
        "SELECT c.tenant_id,c.site_id,%s,c.id,c.revision_sha256,%s,c.extension_id,e.binding_id,"
        "d.owner_user_id,d.recovery_generation,d.membership_epoch,d.site_epoch,c.manifest->>'base_sha',"
        "%s,%s,%s,'owner_editorial',d.id,'dashboard',now()-interval '8 days',now(),%s,%s "
        "FROM app.content_candidates c JOIN app.content_delivery_decisions d "
        "ON d.tenant_id=c.tenant_id AND d.site_id=c.site_id AND d.candidate_id=c.id "
        "JOIN app.github_pr_extensions e ON e.tenant_id=c.tenant_id AND e.site_id=c.site_id "
        "AND e.id=c.extension_id WHERE c.id=%s",
        (
            h.operation,
            b"x" * 32,
            "signal/" + h.operation.hex,
            "done" if state == "opened" else "tree",
            state,
            7 if state == "opened" else None,
            "https://github.com/SignalOwner/website/pull/7" if state == "opened" else None,
            h.candidate,
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path", [".github/workflows/build.html", "auth/login.html", "../escape.html"]
)
async def test_article_protected_manifest_has_no_effect(editorial, path):
    h = await seal(editorial, manifest_path=path)
    assert approve(h) == "approved"
    with pytest.raises(GitHubPrDeliveryUnavailable):
        await dispatch(h)
    assert not h.double.pulls and not any(
        c.method == "POST" and "/repos/" in c.url for c in h.double.calls
    )


@pytest.mark.anyio
@pytest.mark.parametrize("channel", ["slack", "telegram"])
async def test_article_cannot_acquire_chat_authority(editorial, channel):
    h = await seal(editorial)
    assert approve(h) == "approved"
    with pytest.raises(psycopg.errors.CheckViolation):
        h.admin.execute(
            "INSERT INTO app.content_delivery_decisions SELECT tenant_id,site_id,%s,"
            "candidate_id,revision_sha256,owner_user_id,recovery_generation,membership_epoch,site_epoch,"
            "%s,acknowledged_sentences,authenticated_at,created_at "
            "FROM app.content_delivery_decisions "
            "WHERE candidate_id=%s",
            (uuid4(), channel, h.candidate),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writer_call(h.workflow, *h.common, "approve_delivery", h.candidate, h.digest, Jsonb([]))


@pytest.mark.anyio
async def test_article_standing_grant_and_jev_ship_never_dispatch(editorial):
    h = editorial
    technical = await prepare_candidate(h)
    assert (await h.gate.evaluate(technical)).outcome.value == "ship"
    row = h.delivery.rows(h.cycle)[0]
    handle = workload_handle(h.cycle.cycle_id, UUID(row["finding_id"]))
    await seal(h)
    assert approve(h) == "approved"
    # Record ship advice for the article itself, without granting deterministic eligibility.
    article = replace(
        technical,
        decision_id=uuid4(),
        operation_id=h.operation,
        sealed_revision_sha256=h.digest,
        work_type="new_article_pr",
        resource_path="/new.html",
    )
    request = _decision_request(article, h.common[1], 0.95)
    advice = await h.gate.decision_service.recommend(request)
    assert advice.recommendation.value == "ship"
    assert request.state["subject_revision_sha256"] == h.digest.hex()
    assert h.admin.execute(
        "SELECT outcome,input_sha256 FROM app.decision_records WHERE id=%s",
        (article.decision_id,),
    ).fetchone() == ("ship", request.input_sha256)
    connection = WeeklyDeliveryConnection(
        h.workflow, handle=handle, site_id=h.scope.site_id, generation=h.common[1]
    )
    h.common = (handle, h.common[1], h.common[2])
    with pytest.raises(GitHubPrDeliveryUnavailable):
        await dispatch(h, connection=connection)
    assert not h.double.pulls


@pytest.mark.anyio
async def test_article_worker_kill_after_effect_reconciles(editorial):
    h = await seal(editorial)
    assert approve(h) == "approved"

    class WorkerKilled(BaseException):
        pass

    def kill(step):
        if step == "pr":
            raise WorkerKilled()

    h.double.after_write = kill
    with pytest.raises(WorkerKilled):
        await dispatch(h)
    h.double.after_write = None
    remaining = h.admin.execute(
        "SELECT greatest(0,extract(epoch FROM max(expires_at)-clock_timestamp())) "
        "FROM control.admission_leases WHERE released_at IS NULL"
    ).fetchone()[0]
    assert 0 <= remaining <= 120
    await sleep(float(remaining) + 0.1)
    h.admin.execute(
        "UPDATE app.github_pr_operations SET lease_until=now()-interval '1 second' WHERE id=%s",
        (h.operation,),
    )
    h.worker = uuid4()
    before = sum(c.method == "POST" and "/repos/" in c.url for c in h.double.calls)
    assert (await dispatch(h)).state == "opened" and len(h.double.pulls) == 1
    assert sum(c.method == "POST" and "/repos/" in c.url for c in h.double.calls) == before
