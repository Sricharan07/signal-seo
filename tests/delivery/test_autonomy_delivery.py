"""Real PostgreSQL guards for separately attested A2 dispatch authority."""

import asyncio
import json
import os
from dataclasses import replace
from datetime import date, timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
import rfc8785
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from signal_core.autonomy_gate import AutonomyGate
from signal_core.candidate_recipe_inbox import decide_authenticated_candidate_recipe_revision
from signal_core.candidate_sandbox import DockerCandidateSandbox
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.decision_records import PostgresDecisionRecorder
from signal_core.github_pr_delivery import GitHubPrDeliveryUnavailable, open_approved_github_pr
from signal_core.github_pr_extension import observe_github_pr_extension
from signal_core.github_read_binding import GitHubSharedEgressTransport, finish_github_read_binding
from signal_core.jev_decisions import DecisionService
from signal_core.live_verification import SharedLiveVerifier
from signal_core.outbox_dispatch import claim_outbox_batch, mark_outbox_delivered
from signal_core.recipe_autonomy import attest_recipe_autonomy
from signal_core.recipe_releases import transition_recipe_release
from signal_core.shared_egress import SharedEgressProvider
from signal_core.standing_authorization import (
    RecipeRange,
    StandingGrantRequest,
    grant_standing_authorization,
    revoke_standing_authorization,
)
from signal_core.weekly_control import read_weekly_report, set_site_paused
from signal_core.weekly_delivery import (
    DeliveryWeeklyWork,
    WeeklyDeliveryIO,
    WeeklyTechnicalDelivery,
    workload_handle,
)
from signal_core.weekly_delivery_authority import WeeklyDeliveryConnection, stable_identity
from signal_core.weekly_loop import (
    WeeklyActivities,
    WeeklyCycle,
    WeeklyCycleStore,
    WeeklySite,
    WeeklySiteLoop,
    WeeklyStage,
    WeeklyStageResult,
)
from signal_core.weekly_observation import CrawlWeeklyWork
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_contracts import CrawlSiteWorkflowInput, CrawlTerminalInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started
from signal_core.workflow_terminal import record_crawl_terminal
from signal_core.write_intent_journal import WriteIntentJournal
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, Worker

from tests.control_plane import test_slack_binding as slack
from tests.control_plane import test_telegram_binding as telegram
from tests.control_plane.test_autonomy_gate import GenerationSource, JevPrimary
from tests.control_plane.test_full_site_crawl import _executor
from tests.control_plane.test_github_pr_extension import _credential
from tests.control_plane.test_github_read_binding import _owner_site, _prepare, _snapshot, _target
from tests.control_plane.test_recipe_autonomy import (
    test_autonomy_attestation_is_separate_immutable_and_platform_only as test_autonomy_attestation,
)
from tests.control_plane.test_recipe_autonomy import (
    test_forbidden_families_never_become_autonomy_eligible as test_forbidden_families,
)
from tests.control_plane.test_shared_egress import authority
from tests.control_plane.test_technical_recipes import (
    TimedFetcher,
    _register_release,
)
from tests.control_plane.test_technical_recipes import release_manager as release_manager
from tests.delivery.autonomy_delivery_support import (
    SOURCE,
    SYNTHETIC_JOURNAL_ENCRYPTION_KEY,
    SYNTHETIC_JOURNAL_KEY,
    DeliveryRepositoryDouble,
)
from tests.temporal_runtime import start_temporal

_JOURNAL_KEY = SYNTHETIC_JOURNAL_KEY
_JOURNAL_ENCRYPTION_KEY = SYNTHETIC_JOURNAL_ENCRYPTION_KEY

# The registry cases qualify both independently provisioned database suites.
__all__ = ["test_autonomy_attestation", "test_forbidden_families"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def delivery_harness(
    admin,
    api,
    identity,
    workflow,
    scheduler,
    crawl_admission,
    crawl_ingest,
    release_manager,
    scopes,
    identity_context,
    tmp_path,
):
    if "SIGNAL_TEST_WRITE_JOURNAL_DSN" not in os.environ:
        raise pytest.UsageError("Use scripts/run-autonomy-delivery-tests.py for this suite.")
    scope, context = _owner_site(admin, identity, scopes, identity_context)
    admin.execute(
        "UPDATE app.sites SET state='active' WHERE tenant_id=%s AND id=%s",
        (scope.tenant_id, scope.site_id),
    )
    origin = admin.execute(
        "SELECT primary_origin FROM app.sites WHERE id=%s", (scope.site_id,)
    ).fetchone()[0]
    target = _target(content_path="index.html")
    binding = _prepare(identity, scope, context, target=target)
    finish_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=binding,
        snapshot=_snapshot(target),
    )
    store = EncryptedLocalArtifactStore(tmp_path / "delivery-objects")
    double = DeliveryRepositoryDouble(origin)
    github_run, github_policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "github-delivery-" + uuid4().hex,
        store,
        origin_override="https://api.github.com",
        github_profile=True,
    )
    github = SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        github_run,
        github_policy,
        double,
        "worker.weekly-github",
        OriginAdmissionPolicy(),
        None,
        "connector",
    )
    read_transport = GitHubSharedEgressTransport(github)
    credential, bao = _credential()
    extension = await observe_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=binding.id,
        idempotency_key=uuid4(),
        credential=credential,
        github_transport=read_transport,
        openbao_transport=bao,
    )
    assert (extension.framework, extension.content_format) == ("eleventy", "html")
    patch = admin.execute(
        "SELECT coalesce(max(version_patch),99)+1 FROM control.recipe_releases "
        "WHERE recipe_key='technical_structured_data'"
    ).fetchone()[0]
    version = f"1.0.{patch}"
    release_id, actor_id = _register_release(
        admin, release_manager, "technical_structured_data", reviewed=True, version=version
    )
    attest_recipe_autonomy(
        release_manager,
        release_id=release_id,
        recipe_key="technical_structured_data",
        actor_user_id=actor_id,
        attestation_id=uuid4(),
    )
    now = admin.execute("SELECT transaction_timestamp()").fetchone()[0]
    grant = grant_standing_authorization(
        api,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        request=StandingGrantRequest(
            site_id=scope.site_id,
            recipe_ranges=(RecipeRange("technical_structured_data", version, f"1.0.{patch + 1}"),),
            thresholds={"metadata_pr": 0.8},
            weekly_volume_caps={"metadata_pr": 2},
            weekly_total_cap=2,
            weekly_spend_cents=100,
            excluded_paths=(),
            starts_at=now,
            ends_at=now + timedelta(days=7),
            recovery_window_hours=24,
        ),
    )
    generation = GenerationSource(context["generation"])

    def connection_factory():
        return psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True)

    def ingest_factory():
        return psycopg.connect(os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"], autocommit=True)

    def release_factory():
        return psycopg.connect(os.environ["SIGNAL_TEST_API_DSN"], autocommit=True)

    cycle = WeeklyCycle.for_window(
        WeeklySite(str(scope.tenant_id), str(scope.site_id), str(grant.id)), now.date()
    )
    crawl_work = CrawlWeeklyWork(
        connection_factory, ingest_factory, generation, poll_seconds=0.1, max_polls=10
    )
    # The dedicated lab provisions a physically separate journal cluster.
    with psycopg.connect(os.environ["SIGNAL_TEST_WRITE_JOURNAL_DSN"], autocommit=True) as writer:
        journal = WriteIntentJournal(
            writer,
            SYNTHETIC_JOURNAL_KEY.public_key(),
            SYNTHETIC_JOURNAL_ENCRYPTION_KEY,
            SYNTHETIC_JOURNAL_KEY,
        )

        def io_factory(cycle, job, *, verification_path=""):
            live_run, live_policy, _ = authority(
                api,
                scheduler,
                workflow,
                crawl_admission,
                crawl_ingest,
                scope,
                "live-delivery-" + uuid4().hex,
                store,
                origin_override=origin,
                verification_profile=True,
                verification_path=verification_path,
            )
            live = SharedLiveVerifier(
                crawl_admission,
                crawl_ingest,
                store,
                live_run,
                live_policy,
                double,
                "worker.weekly-live",
                OriginAdmissionPolicy(),
            )
            return WeeklyDeliveryIO(
                credential,
                read_transport,
                github,
                DockerCandidateSandbox(),
                journal,
                live,
                "production",
                56,
                openbao_transport=bao,
            )

        delivery = WeeklyTechnicalDelivery(
            connection_factory, release_factory, generation, io_factory, candidate_cost_cents=25
        )
        primary = JevPrimary()
        gate = AutonomyGate(
            workflow,
            generation,
            DecisionService(primary=primary, recorder=PostgresDecisionRecorder(workflow, scope)),
        )
        cycle_store = WeeklyCycleStore(connection_factory)

        class ConsumerWork:
            async def run(self, stage):
                if stage.name == "observe":
                    command_id = crawl_work._admit(stage, context["generation"])
                    envelopes = claim_outbox_batch(
                        scheduler,
                        tenant_id=scope.tenant_id,
                        worker_key="worker.weekly-consumer",
                        batch_size=100,
                    )
                    envelope = next(item for item in envelopes if item.command_id == command_id)
                    admitted = admit_command_event(workflow, envelope)
                    started = record_workflow_started(
                        workflow,
                        admitted,
                        WorkflowStartReceipt(
                            admitted.workflow_id, str(uuid4()), "start_acknowledged"
                        ),
                    )
                    crawl_input = CrawlSiteWorkflowInput(
                        1,
                        str(scope.tenant_id),
                        str(scope.site_id),
                        str(command_id),
                        str(admitted.source_event_id),
                        1,
                        1,
                    )
                    manifest = _executor(tmp_path, TimedFetcher(origin, page_body=SOURCE)).run(
                        crawl_input, first_run_id=started.first_run_id
                    )
                    record_crawl_terminal(
                        workflow,
                        CrawlTerminalInput(
                            1,
                            str(scope.tenant_id),
                            str(scope.site_id),
                            str(command_id),
                            admitted.workflow_id,
                            started.first_run_id,
                            "succeeded",
                            manifest,
                            None,
                        ),
                    )
                    mark_outbox_delivered(
                        scheduler,
                        tenant_id=scope.tenant_id,
                        outbox_id=envelope.outbox_id,
                        worker_key="worker.weekly-consumer",
                        attempt_count=envelope.attempt_count,
                    )
                    return WeeklyStageResult(
                        "completed", "CRAWL_OBSERVED", (f"manifest:{manifest.manifest_id}",)
                    )
                return await crawl_work.run(stage)

        harness = SimpleNamespace(
            scope=scope,
            context=context,
            origin=origin,
            cycle=cycle,
            grant=grant,
            delivery=delivery,
            gate=gate,
            primary=primary,
            store=cycle_store,
            double=double,
            work=DeliveryWeeklyWork(ConsumerWork(), delivery),
            admin=admin,
            api=api,
            workflow=workflow,
            extension=extension,
            release_id=release_id,
            identity=identity,
            release_manager=release_manager,
            actor_id=actor_id,
            io_factory=io_factory,
            read_transport=read_transport,
            credential=credential,
            bao=bao,
        )
        yield harness


@pytest.mark.anyio
async def test_real_evidence_candidate_gate_and_delivery(delivery_harness):
    h = delivery_harness
    ports = WeeklyActivities(
        h.store, work=h.work, candidates=h.delivery, gate=h.gate, delivery=h.delivery
    )
    failed = set()
    stages = (
        "observe",
        "analyze",
        "plan",
        "prepare",
        "gate",
        "handoff",
        "verify",
        "measure",
        "report",
    )

    class RetryAfterCommit:
        blocked = asyncio.Event()
        block_after = 8

        async def admit(self, name):
            if stages.index(name) > self.block_after:
                self.blocked.set()
                await asyncio.Event().wait()

        async def crash_once(self, name, result):
            if name not in failed:
                failed.add(name)
                raise ApplicationError("Synthetic worker exit after committed stage.")
            return result

        @activity.defn(name="signal.weekly.open.v1")
        async def open(self, cycle: WeeklyCycle, first_run_id: str) -> str:
            return await self.crash_once("open", await ports.open(cycle, first_run_id))

        @activity.defn(name="signal.weekly.stage.v1")
        async def stage(self, stage: WeeklyStage) -> WeeklyStageResult:
            await self.admit(stage.name)
            return await self.crash_once(stage.name, await ports.run_stage(stage))

        @activity.defn(name="signal.weekly.gate.v1")
        async def gate(self, stage: WeeklyStage) -> WeeklyStageResult:
            await self.admit(stage.name)
            return await self.crash_once("gate", await ports.gate_candidates(stage))

        @activity.defn(name="signal.weekly.handoff.v1")
        async def handoff(
            self, stage: WeeklyStage, decisions: tuple[str, ...]
        ) -> WeeklyStageResult:
            await self.admit(stage.name)
            return await self.crash_once("handoff", await ports.handoff(stage, decisions))

        @activity.defn(name="signal.weekly.close.v1")
        async def close(self, cycle: WeeklyCycle, status: str, reason: str) -> str:
            return await self.crash_once("close", await ports.close(cycle, status, reason))

    retries = RetryAfterCommit()
    environment = await start_temporal(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    try:
        queue = "delivery-" + uuid4().hex
        handle = None
        for index in range(len(stages)):
            retries.block_after = index
            retries.blocked = asyncio.Event()
            async with Worker(
                environment.client,
                task_queue=queue,
                workflows=[WeeklySiteLoop],
                activities=[
                    retries.open,
                    retries.stage,
                    retries.gate,
                    retries.handoff,
                    retries.close,
                    ports.skip,
                    ports.run_skills,
                ],
                graceful_shutdown_timeout=timedelta(milliseconds=100),
            ):
                if handle is None:
                    handle = await environment.client.start_workflow(
                        WeeklySiteLoop.run,
                        h.cycle.site,
                        id="delivery-" + uuid4().hex,
                        task_queue=queue,
                    )
                if index < len(stages) - 1:
                    await asyncio.wait_for(retries.blocked.wait(), 300)
                else:
                    results = await asyncio.wait_for(handle.result(), 300)
                    history = await handle.fetch_history()
        await Replayer(workflows=[WeeklySiteLoop]).replay_workflow(history)
    finally:
        await environment.shutdown()
    assert results[5].outcome == "completed", results
    assert results[6].detail_code == "LIVE_VERIFIED", results
    assert failed == {
        "open",
        "observe",
        "analyze",
        "plan",
        "prepare",
        "gate",
        "handoff",
        "verify",
        "measure",
        "report",
        "close",
    }
    assert len(h.double.pulls) == 1
    assert "Authorization kind: `standing_grant`" in h.double.pulls[0]["body"]
    assert (await h.delivery.dispatch(h.cycle, ())).outcome == "completed"
    assert len(h.double.pulls) == 1
    assert h.admin.execute(
        "SELECT count(*) FROM app.candidate_recipe_review_decisions WHERE site_id=%s",
        (h.scope.site_id,),
    ).fetchone() == (0,)
    report = read_weekly_report(
        h.api,
        session_token=h.context["session_token"],
        site_id=h.scope.site_id,
        recovery_generation=h.context["generation"],
        week_start=date.fromisoformat(h.cycle.week_start),
    )
    assert len(report["delivery"]) == 1
    assert report["delivery"][0]["delivery_outcome"] == "verified"
    assert report["delivery"][0]["authority_kind"] == "standing_grant"


async def prepare_candidate(h):
    assert h.store.open(h.cycle, str(uuid4()), h.context["generation"]) == "opened"
    for name in ("observe", "analyze", "plan"):
        assert (await h.work.run(WeeklyStage(h.cycle, name))).outcome == "completed"
    candidates = await h.delivery.prepare(WeeklyStage(h.cycle, "prepare"), ())
    assert len(candidates) == 1
    return candidates[0]


@pytest.mark.anyio
@pytest.mark.parametrize("stale", [False, True])
async def test_slack_exact_owner_approval_dispatches_once_or_rejects_stale(
    delivery_harness, scheduler, crawl_admission, crawl_ingest, tmp_path, stale
):
    h = delivery_harness
    candidate = await prepare_candidate(h)
    h.primary.confidence = 0.1
    assert (await h.gate.evaluate(candidate)).outcome.value == "ask_owner"
    row = h.delivery.rows(h.cycle)[0]
    svc, _ = slack.service(h.identity, h.context)
    provider, _ = slack.egress(
        h.api, scheduler, h.workflow, crawl_admission, crawl_ingest, h.scope, tmp_path
    )
    binding = await slack.install(svc, provider, h.context, h.scope.site_id)
    await slack.link(svc, provider, h.admin, h.context, h.scope.site_id, binding)
    outbox = svc.queue_approval(
        session_token=h.context["session_token"],
        site_id=h.scope.site_id,
        binding_id=binding,
        revision_id=UUID(row["revision_id"]),
        revision_sha256=row["revision_sha256"],
        channel_id=slack.CHANNEL,
    )
    await slack.deliver(svc, provider, binding, outbox)
    payload, channel, ts = slack.message(h.admin, outbox)
    callback = payload["blocks"][1]["elements"][1]["value"]
    signed = slack.signed_action(callback, "signal_approve", ts, channel=channel)
    approval = await svc.interact(binding_id=binding, **signed)
    assert approval["outcome"] == "decided"
    assert (await svc.interact(binding_id=binding, **signed))["outcome"] == "replay"
    decision = h.admin.execute(
        "SELECT decision_channel,revision_sha256 FROM app.candidate_recipe_review_decisions "
        "WHERE id=%s",
        (approval["decision_id"],),
    ).fetchone()
    assert decision == ("slack", bytes.fromhex(row["revision_sha256"]))
    if stale:
        # A later sealed revision makes the historic Slack approval unusable.
        with h.admin.cursor(row_factory=dict_row) as cursor:
            revision = cursor.execute(
                "SELECT * FROM app.candidate_recipe_revisions WHERE id=%s",
                (UUID(row["revision_id"]),),
            ).fetchone()
            intent = cursor.execute(
                "SELECT * FROM app.candidate_build_intents WHERE id=%s", (revision["build_id"],)
            ).fetchone()
            receipt = cursor.execute(
                "SELECT * FROM app.candidate_build_receipts WHERE build_id=%s",
                (revision["build_id"],),
            ).fetchone()
        revision.update(id=uuid4(), build_id=uuid4(), idempotency_key=uuid4())
        manifest = json.loads(revision["canonical_manifest"])
        manifest["build_id"] = str(revision["build_id"])
        revision["canonical_manifest"] = rfc8785.dumps(manifest)
        del revision["revision_sha256"]
        del revision["sealed_at"]
        intent.update(id=revision["build_id"], idempotency_key=uuid4())
        receipt["build_id"] = revision["build_id"]
        for table, document in (
            ("candidate_build_intents", intent),
            ("candidate_build_receipts", receipt),
            ("candidate_recipe_revisions", revision),
        ):
            columns = list(document)
            h.admin.execute(
                sql.SQL("INSERT INTO app.{} ({}) VALUES ({})").format(
                    sql.Identifier(table),
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.SQL(",").join(sql.Placeholder() for _ in columns),
                ),
                tuple(
                    Jsonb(document[key])
                    if isinstance(document[key], (list, dict))
                    else document[key]
                    for key in columns
                ),
            )
        result = await h.delivery.dispatch(h.cycle, ())
        assert result.outcome == "waiting_owner"
        assert not h.double.pulls
        assert not any(c.method == "POST" and "/repos/" in c.url for c in h.double.calls)
        return
    assert (await h.delivery.dispatch(h.cycle, ())).outcome == "completed"
    assert (await h.delivery.dispatch(h.cycle, ())).outcome == "completed"
    assert len(h.double.pulls) == 1
    assert "Owner decision channel: `slack`" in h.double.pulls[0]["body"]
    assert "Authorization kind: `owner_inbox`" in h.double.pulls[0]["body"]
    assert h.admin.execute(
        "SELECT authority_kind,decision_id,decision_channel "
        "FROM app.github_pr_operations WHERE id=%s",
        (candidate.operation_id,),
    ).fetchone() == ("owner_inbox", approval["decision_id"], "slack")
    canonical = h.admin.execute(
        "SELECT canonical_receipt FROM app.weekly_pr_execution_receipts WHERE operation_id=%s",
        (candidate.operation_id,),
    ).fetchone()[0]
    assert json.loads(canonical)["decision_channel"] == "slack"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        h.admin.execute(
            "UPDATE app.github_pr_operations SET decision_channel='dashboard' WHERE id=%s",
            (candidate.operation_id,),
        )


def _supersede_owner_review_revision(h, row):
    with h.admin.cursor(row_factory=dict_row) as cursor:
        revision = cursor.execute(
            "SELECT * FROM app.candidate_recipe_revisions WHERE id=%s",
            (UUID(row["revision_id"]),),
        ).fetchone()
        intent = cursor.execute(
            "SELECT * FROM app.candidate_build_intents WHERE id=%s", (revision["build_id"],)
        ).fetchone()
        receipt = cursor.execute(
            "SELECT * FROM app.candidate_build_receipts WHERE build_id=%s",
            (revision["build_id"],),
        ).fetchone()
    revision.update(id=uuid4(), build_id=uuid4(), idempotency_key=uuid4())
    manifest = json.loads(revision["canonical_manifest"])
    manifest["build_id"] = str(revision["build_id"])
    revision["canonical_manifest"] = rfc8785.dumps(manifest)
    del revision["revision_sha256"]
    del revision["sealed_at"]
    intent.update(id=revision["build_id"], idempotency_key=uuid4())
    receipt["build_id"] = revision["build_id"]
    for table, document in (
        ("candidate_build_intents", intent),
        ("candidate_build_receipts", receipt),
        ("candidate_recipe_revisions", revision),
    ):
        columns = list(document)
        h.admin.execute(
            sql.SQL("INSERT INTO app.{} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.SQL(",").join(sql.Placeholder() for _ in columns),
            ),
            tuple(
                Jsonb(document[key]) if isinstance(document[key], (list, dict)) else document[key]
                for key in columns
            ),
        )


@pytest.mark.anyio
@pytest.mark.parametrize("staleness", ["current", "before_callback", "before_dispatch"])
async def test_telegram_exact_owner_approval_dispatches_once_or_rejects_stale(
    delivery_harness, scheduler, crawl_admission, crawl_ingest, tmp_path, staleness
):
    h = delivery_harness
    candidate = await prepare_candidate(h)
    h.primary.confidence = 0.1
    assert (await h.gate.evaluate(candidate)).outcome.value == "ask_owner"
    row = h.delivery.rows(h.cycle)[0]
    svc, bao = telegram.service(h.identity, h.context)
    provider, _ = telegram.egress(
        h.api, scheduler, h.workflow, crawl_admission, crawl_ingest, h.scope, tmp_path
    )
    binding = await telegram.install(svc, provider, h.context, h.scope.site_id)
    await telegram.pair(svc, bao, h.context, h.scope.site_id, binding)
    outbox = svc.queue_approval(
        session_token=h.context["session_token"],
        site_id=h.scope.site_id,
        binding_id=binding,
        revision_id=UUID(row["revision_id"]),
        revision_sha256=row["revision_sha256"],
    )
    await telegram.deliver(svc, provider, binding, outbox)
    payload, message_id = telegram.message(h.admin, outbox)
    callback = payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    body = telegram.update(10, code=callback, message_id=message_id)
    if staleness == "before_callback":
        _supersede_owner_review_revision(h, row)
    approval = await svc.interact(
        binding_id=binding, body=body, secret_header=telegram.secret(bao, binding)
    )
    if staleness == "before_callback":
        assert approval["outcome"] == "stale_revision"
        assert approval["decision_id"] is None
    else:
        assert approval["outcome"] == "decided"
        assert (
            await svc.interact(
                binding_id=binding, body=body, secret_header=telegram.secret(bao, binding)
            )
        )["outcome"] == "replay"
        assert h.admin.execute(
            "SELECT decision_channel,revision_sha256 FROM app.candidate_recipe_review_decisions "
            "WHERE id=%s",
            (approval["decision_id"],),
        ).fetchone() == ("telegram", bytes.fromhex(row["revision_sha256"]))
        if staleness == "before_dispatch":
            _supersede_owner_review_revision(h, row)
    if staleness != "current":
        assert (await h.delivery.dispatch(h.cycle, ())).outcome == "waiting_owner"
        assert not h.double.pulls
        assert not any(c.method == "POST" and "/repos/" in c.url for c in h.double.calls)
        return
    assert (await h.delivery.dispatch(h.cycle, ())).outcome == "completed"
    assert (await h.delivery.dispatch(h.cycle, ())).outcome == "completed"
    assert len(h.double.pulls) == 1
    assert "Owner decision channel: `telegram`" in h.double.pulls[0]["body"]
    assert "Authorization kind: `owner_inbox`" in h.double.pulls[0]["body"]
    assert h.admin.execute(
        "SELECT authority_kind,decision_id,decision_channel FROM app.github_pr_operations "
        "WHERE id=%s",
        (candidate.operation_id,),
    ).fetchone() == ("owner_inbox", approval["decision_id"], "telegram")
    canonical = h.admin.execute(
        "SELECT canonical_receipt FROM app.weekly_pr_execution_receipts WHERE operation_id=%s",
        (candidate.operation_id,),
    ).fetchone()[0]
    assert json.loads(canonical)["decision_channel"] == "telegram"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        h.admin.execute(
            "UPDATE app.github_pr_operations SET decision_channel='dashboard' WHERE id=%s",
            (candidate.operation_id,),
        )


@pytest.mark.anyio
async def test_editorial_candidate_approval_is_not_technical_dispatch_authority(delivery_harness):
    h = delivery_harness
    await prepare_candidate(h)
    row = h.delivery.rows(h.cycle)[0]
    revision = h.admin.execute(
        "SELECT build_id FROM app.candidate_recipe_revisions WHERE id=%s",
        (UUID(row["revision_id"]),),
    ).fetchone()
    brief, draft, editorial = uuid4(), uuid4(), uuid4()
    scope = (h.scope.tenant_id, h.scope.site_id)
    canonical = rfc8785.dumps({"schema_version": 1, "kind": "new_article"})
    digest = sha256(canonical).digest()
    # Seed an independently approved editorial record, not a technical revision.
    h.admin.execute(
        "INSERT INTO app.content_briefs(tenant_id,site_id,id,payload,origin,created_by) "
        "VALUES(%s,%s,%s,'{}','owner',%s)",
        (*scope, brief, h.context["user_id"]),
    )
    h.admin.execute(
        "INSERT INTO app.content_draft_intents(tenant_id,site_id,id,brief_id,"
        "writer_version,input_sha256,fact_snapshot) "
        "VALUES(%s,%s,%s,%s,'content-writer-v1',%s,'{}')",
        (*scope, draft, brief, digest),
    )
    h.admin.execute(
        "INSERT INTO app.content_draft_results(tenant_id,site_id,draft_id,"
        "payload,canonical,sha256) "
        "VALUES(%s,%s,%s,'{}',%s,%s)",
        (*scope, draft, canonical, digest),
    )
    h.admin.execute(
        "INSERT INTO app.content_candidates(tenant_id,site_id,id,draft_id,extension_id,"
        "build_id,manifest,canonical,revision_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            *scope,
            editorial,
            draft,
            h.extension.id,
            revision[0],
            Jsonb(json.loads(canonical)),
            canonical,
            digest,
        ),
    )
    h.admin.execute(
        "INSERT INTO app.content_candidate_reviews(tenant_id,site_id,candidate_id,"
        "decision,actor_id) VALUES(%s,%s,%s,'approved',%s)",
        (*scope, editorial, h.context["user_id"]),
    )
    io = h.io_factory(h.cycle, None)
    with pytest.raises(GitHubPrDeliveryUnavailable, match="PR_OWNER_DECISION_UNAVAILABLE"):
        await open_approved_github_pr(
            h.identity,
            h.api,
            session_token=h.context["session_token"],
            current_recovery_generation=h.context["generation"],
            site_id=h.scope.site_id,
            revision_id=editorial,
            expected_revision_sha256=digest.hex(),
            operation_id=uuid4(),
            worker_id=uuid4(),
            journal=io.journal,
            credential=io.credential,
            github_read_transport=io.github_read_transport,
            github_write_egress=io.github_egress,
            openbao_transport=io.openbao_transport,
        )
    assert not any(c.method == "POST" and "/repos/" in c.url for c in h.double.calls)
    assert h.admin.execute(
        "SELECT count(*) FROM app.github_pr_operations WHERE site_id=%s", (h.scope.site_id,)
    ).fetchone() == (0,)


@pytest.mark.anyio
async def test_next_window_reconsiders_exact_unreserved_deferred_revision(delivery_harness):
    h = delivery_harness
    previous = WeeklyCycle.for_window(
        h.cycle.site, date.fromisoformat(h.cycle.week_start) - timedelta(days=7)
    )
    # Seed a historical week rather than changing the database's current clock.
    h.admin.execute(
        "INSERT INTO app.weekly_cycles(tenant_id,site_id,week_start,id,grant_id,first_run_id) "
        "VALUES(%s,%s,%s,%s,%s,%s)",
        (
            h.scope.tenant_id,
            h.scope.site_id,
            previous.week_start,
            UUID(previous.cycle_id),
            h.grant.id,
            str(uuid4()),
        ),
    )
    for name in ("observe", "analyze", "plan"):
        assert (await h.work.run(WeeklyStage(previous, name))).outcome == "completed"
    original = (await h.delivery.prepare(WeeklyStage(previous, "prepare"), ()))[0]
    h.store.defer(previous, (original.sealed_revision_sha256.hex(),))
    assert h.store.close(previous, "completed", "CYCLE_REPORTED") == "closed"
    assert h.store.open(h.cycle, str(uuid4()), h.context["generation"]) == "opened"
    due = h.store.due(h.cycle)
    assert due == (original.sealed_revision_sha256.hex(),)
    current = (await h.delivery.prepare(WeeklyStage(h.cycle, "prepare"), due))[0]
    assert current.sealed_revision_sha256 == original.sealed_revision_sha256
    assert current.decision_id != original.decision_id
    assert current.operation_id != original.operation_id
    assert (await h.gate.evaluate(current)).outcome.value == "ship"
    assert (await h.delivery.dispatch(h.cycle, (str(current.decision_id),))).outcome == "completed"
    assert len(h.double.pulls) == 1
    assert h.store.due(h.cycle) == ()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["low_confidence", "jev_unavailable"])
async def test_escalation_uses_real_exact_inbox_approval(delivery_harness, failure):
    h = delivery_harness
    candidate = await prepare_candidate(h)
    if failure == "low_confidence":
        h.primary.confidence = 0.1
    else:
        h.primary.choice = "unavailable"
    decision = await h.gate.evaluate(candidate)
    assert decision.outcome.value == "ask_owner"
    pending = await h.delivery.dispatch(h.cycle, (str(candidate.decision_id),))
    assert pending.outcome == "waiting_owner" and not h.double.pulls
    assert h.admin.execute(
        "SELECT count(*) FROM app.standing_dispatch_authorizations WHERE site_id=%s",
        (h.scope.site_id,),
    ).fetchone() == (0,)
    row = h.delivery.rows(h.cycle)[0]
    # Closed cycles do not synthesize owner authority or block a real exact approval.
    assert h.store.close(h.cycle, "completed", "CYCLE_REPORTED") == "closed"
    approval = decide_authenticated_candidate_recipe_revision(
        h.identity,
        session_token=h.context["session_token"],
        requested_site_id=h.scope.site_id,
        current_recovery_generation=h.context["generation"],
        revision_id=UUID(row["revision_id"]),
        expected_revision_sha256=row["revision_sha256"],
        decision_id=uuid4(),
        decision="approved",
    )
    assert approval.review_status == "approved"
    result = await h.delivery.dispatch(h.cycle, ())
    assert result.outcome == "completed", result
    assert len(h.double.pulls) == 1
    assert "Authorization kind: `owner_inbox`" in h.double.pulls[0]["body"]
    assert (await h.delivery.dispatch(h.cycle, ())).outcome == "completed"
    assert len(h.double.pulls) == 1


def revoke(h):
    revoke_standing_authorization(
        h.api,
        session_token=h.context["session_token"],
        current_recovery_generation=h.context["generation"],
        site_id=h.scope.site_id,
        grant_id=h.grant.id,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "restriction", ["revoke", "pause", "owner_approval", "approve_after_revoke"]
)
async def test_authority_reduction_between_effects_stops_the_next_write(
    delivery_harness, restriction
):
    h = delivery_harness
    candidate = await prepare_candidate(h)
    assert (await h.gate.evaluate(candidate)).outcome.value == "ship"

    def reduce_authority(step):
        if step != "tree":
            return
        h.double.after_write = None
        if restriction in {"revoke", "approve_after_revoke"}:
            revoke(h)
        elif restriction == "pause":
            set_site_paused(
                h.api,
                session_token=h.context["session_token"],
                site_id=h.scope.site_id,
                recovery_generation=h.context["generation"],
                paused=True,
            )
        if restriction in {"owner_approval", "approve_after_revoke"}:
            row = h.delivery.rows(h.cycle)[0]
            approval = decide_authenticated_candidate_recipe_revision(
                h.identity,
                session_token=h.context["session_token"],
                requested_site_id=h.scope.site_id,
                current_recovery_generation=h.context["generation"],
                revision_id=UUID(row["revision_id"]),
                expected_revision_sha256=row["revision_sha256"],
                decision_id=uuid4(),
                decision="approved",
            )
            assert approval.review_status == "approved"

    h.double.after_write = reduce_authority
    result = await h.delivery.dispatch(h.cycle, (str(candidate.decision_id),))
    assert result.outcome == "waiting_owner"
    writes = [call for call in h.double.calls if call.method == "POST" and "/repos/" in call.url]
    assert len(writes) == 1 and writes[0].url.endswith("/git/trees")
    assert not h.double.refs and not h.double.pulls
    assert h.admin.execute(
        "SELECT authority_kind,decision_id FROM app.github_pr_operations WHERE id=%s",
        (candidate.operation_id,),
    ).fetchone() == ("standing_grant", None)
    await h.delivery.dispatch(h.cycle, (), reconcile_only=True)
    assert (
        len([call for call in h.double.calls if call.method == "POST" and "/repos/" in call.url])
        == 1
    )


@pytest.mark.anyio
@pytest.mark.parametrize("destination", ["/canonical", "/robots.txt", "/redirects"])
async def test_forbidden_destination_cannot_consume_a_jev_ship(delivery_harness, destination):
    h = delivery_harness
    candidate = await prepare_candidate(h)
    # Even a wrongly classified gate request cannot authorize a different resource.
    decision = await h.gate.evaluate(replace(candidate, resource_path=destination))
    assert decision.outcome.value == "ship" and h.primary.calls == 1
    result = await h.delivery.dispatch(h.cycle, (str(candidate.decision_id),))
    assert result.outcome == "waiting_owner"
    assert not any(call.method == "POST" and "/repos/" in call.url for call in h.double.calls)
    assert h.admin.execute(
        "SELECT count(*) FROM app.standing_dispatch_authorizations WHERE site_id=%s",
        (h.scope.site_id,),
    ).fetchone() == (0,)


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["A3", "pause", "revoked", "cap", "base", "release"])
async def test_changed_authority_never_opens_a_pr(delivery_harness, change):
    h = delivery_harness
    candidate = await prepare_candidate(h)
    if change == "A3":
        candidate = replace(candidate, approval_class="A3")
    decision = await h.gate.evaluate(candidate)
    if change == "A3":
        assert decision.outcome.value != "ship" and h.primary.calls == 0
    elif change == "pause":
        set_site_paused(
            h.api,
            session_token=h.context["session_token"],
            site_id=h.scope.site_id,
            recovery_generation=h.context["generation"],
            paused=True,
        )
    elif change == "revoked":
        revoke(h)
    elif change == "cap":
        h.admin.execute(
            "UPDATE app.autonomy_weekly_usage SET total_count=3 WHERE site_id=%s",
            (h.scope.site_id,),
        )
    elif change == "base":
        h.double.base_sha = "c" * 40
    elif change == "release":
        transition_recipe_release(
            h.release_manager,
            release_id=h.release_id,
            expected_status="REVIEWED",
            new_status="REVOKED",
            actor_user_id=h.actor_id,
            reason="Synthetic revocation",
        )
    result = await h.delivery.dispatch(h.cycle, (str(candidate.decision_id),))
    assert not h.double.pulls, result
    assert not any(call.method == "POST" and "/repos/" in call.url for call in h.double.calls)


@pytest.mark.anyio
@pytest.mark.parametrize("step", ["tree", "commit", "branch", "pr"])
async def test_lost_write_response_is_reconciled_without_duplicate(delivery_harness, step):
    h = delivery_harness
    candidate = await prepare_candidate(h)
    assert (await h.gate.evaluate(candidate)).outcome.value == "ship"
    h.double.lost_step = step
    first = await h.delivery.dispatch(h.cycle, (str(candidate.decision_id),))
    assert first.detail_code == "IN_FLIGHT_OUTCOME_UNKNOWN", first
    # Simulate the killed worker's lease expiring, then revoke before recovery reads.
    h.admin.execute(
        "UPDATE app.github_pr_operations SET lease_until="
        "transaction_timestamp()-interval '1 second' WHERE site_id=%s",
        (h.scope.site_id,),
    )
    revoke(h)
    before = len([c for c in h.double.calls if c.method == "POST" and "/repos/" in c.url])
    recovered = await h.delivery.dispatch(h.cycle, (), reconcile_only=True)
    assert recovered.detail_code != "IN_FLIGHT_OUTCOME_UNKNOWN", recovered
    after = len([c for c in h.double.calls if c.method == "POST" and "/repos/" in c.url])
    assert before == after
    assert len(h.double.pulls) == (1 if step == "pr" else 0)


@pytest.mark.anyio
async def test_standing_dispatch_is_bound_to_exact_revision(delivery_harness):
    h = delivery_harness
    candidate = await prepare_candidate(h)
    assert (await h.gate.evaluate(candidate)).outcome.value == "ship"
    row = h.delivery.rows(h.cycle)[0]
    handle = workload_handle(h.cycle.cycle_id, UUID(row["finding_id"]))
    args = (sha256(handle.encode("ascii")).digest(), h.scope.site_id, h.context["generation"])
    assert h.workflow.execute(
        "SELECT control.authorize_weekly_dispatch(%s,%s,%s,%s)",
        (*args, stable_identity(row["id"] + ":standing-dispatch")),
    ).fetchone()[0] == stable_identity(row["id"] + ":standing-dispatch")
    scoped = WeeklyDeliveryConnection(
        h.workflow, handle=handle, site_id=h.scope.site_id, generation=h.context["generation"]
    )
    authority = scoped.dispatch_authority(
        revision_id=UUID(row["revision_id"]),
        revision_sha256=row["revision_sha256"],
        operation_id=candidate.operation_id,
    )
    assert authority.kind == "standing_grant"
    for revision, digest, operation in [
        (uuid4(), row["revision_sha256"], candidate.operation_id),
        (UUID(row["revision_id"]), "0" * 64, candidate.operation_id),
        (UUID(row["revision_id"]), row["revision_sha256"], uuid4()),
    ]:
        with pytest.raises(PermissionError):
            scoped.dispatch_authority(
                revision_id=revision, revision_sha256=digest, operation_id=operation
            )
    with pytest.raises(PermissionError):
        scoped.execute(
            "SELECT control.decide_authenticated_candidate_recipe_revision(%s,%s,%s)", args
        )


@pytest.mark.anyio
async def test_unprotected_acceptance_never_dispatches_standing_but_exact_owner_inbox_can(
    delivery_harness,
):
    from signal_core.github_read_binding import accept_github_unprotected_base

    from tests.control_plane.test_github_unprotected_base import fresh_mfa

    h = delivery_harness
    candidate = await prepare_candidate(h)
    assert (await h.gate.evaluate(candidate)).outcome.value == "ship"
    h.double.protected = False
    fresh_mfa(h.admin, h.context)
    binding_id = h.admin.execute(
        "SELECT binding_id FROM app.github_pr_extensions WHERE id=%s", (h.extension.id,)
    ).fetchone()[0]
    binding = await accept_github_unprotected_base(
        h.identity,
        session_token=h.context["session_token"],
        current_recovery_generation=h.context["generation"],
        site_id=h.scope.site_id,
        binding_id=binding_id,
        credential=h.credential,
        github_transport=h.read_transport,
        openbao_transport=h.bao,
    )
    assert binding.owner_accepted_unprotected
    result = await h.delivery.dispatch(h.cycle, (str(candidate.decision_id),))
    assert result.outcome == "waiting_owner" and not h.double.pulls
    assert not any(c.method == "POST" and "/repos/" in c.url for c in h.double.calls)
    assert h.admin.execute(
        "SELECT count(*) FROM app.standing_dispatch_authorizations WHERE site_id=%s",
        (h.scope.site_id,),
    ).fetchone() == (0,)
    row = h.delivery.rows(h.cycle)[0]
    approval = decide_authenticated_candidate_recipe_revision(
        h.identity,
        session_token=h.context["session_token"],
        requested_site_id=h.scope.site_id,
        current_recovery_generation=h.context["generation"],
        revision_id=UUID(row["revision_id"]),
        expected_revision_sha256=row["revision_sha256"],
        decision_id=uuid4(),
        decision="approved",
    )
    assert approval.decision_channel == "dashboard"
    result = await h.delivery.dispatch(h.cycle, ())
    assert result.outcome == "completed", result
    assert len(h.double.pulls) == 1
    assert "Authorization kind: `owner_inbox`" in h.double.pulls[0]["body"]
