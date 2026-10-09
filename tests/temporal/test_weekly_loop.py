"""Real Temporal schedule, stage history, and deterministic replay."""

import asyncio
import os
from datetime import date, timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from signal_core.autonomy_gate import AutonomyCandidate
from signal_core.database import Scope
from signal_core.decision_contracts import Recommendation
from signal_core.recovery_authority import RecoveryGeneration
from signal_core.weekly_loop import (
    WeeklyActivities,
    WeeklyCycle,
    WeeklySite,
    WeeklySiteLoop,
    WeeklyStage,
    WeeklyStageResult,
)
from temporalio import activity
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    WorkflowFailureError,
)
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, Worker

from tests.temporal_runtime import start_temporal


class StageActivities:
    def __init__(self, *, stop_at=None, fail_at=None, block_after=None):
        self.stages = []
        self.cycle_ids = []
        self.closes = []
        self.skill_phases = []
        self.stop_at = stop_at
        self.fail_at = fail_at
        self.block_after = block_after
        self.blocked = asyncio.Event()

    async def _admit(self, name: str) -> None:
        if (
            self.block_after is not None
            and (
                "observe",
                "analyze",
                "plan",
                "prepare",
                "gate",
                "handoff",
                "verify",
                "measure",
                "report",
            ).index(name)
            > self.block_after
        ):
            self.blocked.set()
            await asyncio.Event().wait()

    @activity.defn(name="signal.weekly.open.v1")
    async def open(self, cycle: WeeklyCycle, workflow_id: str) -> str:
        self.cycle_ids.append(cycle.cycle_id)
        return "opened"

    @activity.defn(name="signal.weekly.stage.v1")
    async def stage(self, item: WeeklyStage) -> WeeklyStageResult:
        await self._admit(item.name)
        self.stages.append(item.name)
        if item.name == self.fail_at:
            raise ApplicationError("Synthetic stage failure.", non_retryable=True)
        if item.name == self.stop_at:
            return WeeklyStageResult("stopped", "AUTHORITY_CHANGED")
        if item.name in {"verify", "measure"}:
            return WeeklyStageResult("unavailable", "NO_EXTERNAL_WRITE")
        return WeeklyStageResult("completed", "STAGE_RECORDED")

    @activity.defn(name="signal.weekly.gate.v1")
    async def gate(self, item: WeeklyStage) -> WeeklyStageResult:
        await self._admit(item.name)
        self.stages.append(item.name)
        return WeeklyStageResult("deferred", "NEXT_WEEKLY_WINDOW")

    @activity.defn(name="signal.weekly.handoff.v1")
    async def handoff(self, item: WeeklyStage, decisions: tuple[str, ...]) -> WeeklyStageResult:
        await self._admit(item.name)
        self.stages.append(item.name)
        assert decisions == ()
        return WeeklyStageResult("unavailable", "NO_CURRENT_HANDOFF")

    @activity.defn(name="signal.weekly.skip.v1")
    async def skip(self, item: WeeklyStage) -> WeeklyStageResult:
        await self._admit(item.name)
        self.stages.append(item.name)
        return WeeklyStageResult("unavailable", "UPSTREAM_EVIDENCE_UNAVAILABLE")

    @activity.defn(name="signal.weekly.close.v1")
    async def close(self, cycle: WeeklyCycle, status: str, reason: str) -> str:
        self.closes.append((status, reason))
        return "closed"

    def all(self):
        return [self.open, self.stage, self.gate, self.handoff, self.skip, self.close, self.skills]

    @activity.defn(name="signal.weekly.skills.v1")
    async def skills(self, cycle: WeeklyCycle, phase: str) -> str:
        self.skill_phases.append(phase)
        return "enabled" if phase == "enable" else "recorded"


async def exercise_weekly_schedule():
    environment = await start_temporal(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    try:
        task_queue = f"weekly-{uuid4()}"
        site = WeeklySite(str(uuid4()), str(uuid4()), str(uuid4()))
        activities = StageActivities()
        schedule_id = f"weekly-test-{uuid4()}"
        async with Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[WeeklySiteLoop],
            activities=activities.all(),
        ):
            await environment.client.create_schedule(
                schedule_id,
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        "WeeklySiteLoop",
                        site,
                        id=f"signal:WeeklySiteLoop:{site.tenant_id}:{site.site_id}",
                        task_queue=task_queue,
                    ),
                    spec=ScheduleSpec(cron_expressions=["0 9 * * 1"], time_zone_name="UTC"),
                    policy=SchedulePolicy(
                        overlap=ScheduleOverlapPolicy.SKIP,
                        catchup_window=timedelta(minutes=1),
                    ),
                ),
                trigger_immediately=True,
            )
            for _ in range(100):
                if len(activities.stages) == 9:
                    break
                await asyncio.sleep(0.1)
            assert activities.stages == [
                "observe",
                "analyze",
                "plan",
                "prepare",
                "gate",
                "handoff",
                "verify",
                "measure",
                "report",
            ]
            workflows = [item async for item in environment.client.list_workflows()]
            weekly = [item for item in workflows if item.workflow_type == "WeeklySiteLoop"]
            assert len(weekly) == 1
            handle = environment.client.get_workflow_handle(
                weekly[0].id, result_type=tuple[WeeklyStageResult, ...]
            )
            result = await asyncio.wait_for(handle.result(), timeout=20)
            assert activities.skill_phases == ["enable", "research", "delivery"]
            assert result[4].outcome == "deferred"
            assert result[6].detail_code == "NO_EXTERNAL_WRITE"
            history = await handle.fetch_history()
            assert len(set(activities.cycle_ids)) == 1
            assert activities.closes == [("completed", "CYCLE_REPORTED")]
        await Replayer(workflows=[WeeklySiteLoop]).replay_workflow(history)
    finally:
        await environment.shutdown()


def test_weekly_schedule_runs_stages_and_replays():
    asyncio.run(exercise_weekly_schedule())


async def exercise_stopped_and_failed():
    environment = await start_temporal(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    try:
        site = WeeklySite(str(uuid4()), str(uuid4()), str(uuid4()))
        queue = f"weekly-state-{uuid4()}"
        stopped = StageActivities(stop_at="analyze")
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[WeeklySiteLoop],
            activities=stopped.all(),
        ):
            handle = await environment.client.start_workflow(
                WeeklySiteLoop.run,
                site,
                id=f"weekly-stop-{uuid4()}",
                task_queue=queue,
                result_type=tuple[WeeklyStageResult, ...],
            )
            result = await asyncio.wait_for(handle.result(), timeout=20)
            assert [item.outcome for item in result] == ["completed", "stopped"]
            assert stopped.closes == [("stopped", "AUTHORITY_CHANGED")]
            stopped_history = await handle.fetch_history()
        failed = StageActivities(fail_at="plan")
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[WeeklySiteLoop],
            activities=failed.all(),
        ):
            handle = await environment.client.start_workflow(
                WeeklySiteLoop.run,
                site,
                id=f"weekly-fail-{uuid4()}",
                task_queue=queue,
                result_type=tuple[WeeklyStageResult, ...],
            )
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), timeout=20)
            assert failed.closes == [("failed", "ACTIVITY_FAILED")]
            failed_history = await handle.fetch_history()
        await Replayer(workflows=[WeeklySiteLoop]).replay_workflow(stopped_history)
        await Replayer(workflows=[WeeklySiteLoop]).replay_workflow(failed_history)
    finally:
        await environment.shutdown()


def test_weekly_authority_stop_and_activity_failure_replay():
    asyncio.run(exercise_stopped_and_failed())


async def exercise_restart_after_each_stage():
    environment = await start_temporal(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    try:
        site = WeeklySite(str(uuid4()), str(uuid4()), str(uuid4()))
        queue = f"weekly-restart-{uuid4()}"
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
        handle = None
        observed = []
        for index, name in enumerate(stages):
            activities = StageActivities(block_after=index if index < len(stages) - 1 else None)
            async with Worker(
                environment.client,
                task_queue=queue,
                workflows=[WeeklySiteLoop],
                activities=activities.all(),
                graceful_shutdown_timeout=timedelta(milliseconds=100),
            ):
                if handle is None:
                    handle = await environment.client.start_workflow(
                        WeeklySiteLoop.run,
                        site,
                        id=f"weekly-restart-{uuid4()}",
                        task_queue=queue,
                        result_type=tuple[WeeklyStageResult, ...],
                    )
                if index < len(stages) - 1:
                    await asyncio.wait_for(activities.blocked.wait(), timeout=20)
                else:
                    result = await asyncio.wait_for(handle.result(), timeout=20)
                    assert len(result) == len(stages)
            assert activities.stages == [name]
            observed.extend(activities.stages)
        assert tuple(observed) == stages
        await Replayer(workflows=[WeeklySiteLoop]).replay_workflow(await handle.fetch_history())
    finally:
        await environment.shutdown()


def test_weekly_worker_can_restart_after_every_durable_stage():
    asyncio.run(exercise_restart_after_each_stage())


def test_ambiguous_finalizer_result_defers_only_confirmed_cap_exhaustion():
    async def exercise(reason):
        site = WeeklySite(str(uuid4()), str(uuid4()), str(uuid4()))
        stage = WeeklyStage(WeeklyCycle.for_window(site, date.today()), "gate")
        candidate = AutonomyCandidate(
            scope=Scope(UUID(site.tenant_id), UUID(site.site_id)),
            decision_id=uuid4(),
            operation_id=uuid4(),
            grant_id=UUID(site.grant_id),
            recipe_release_id=uuid4(),
            sealed_revision_sha256=sha256(b"sealed").digest(),
            work_type="draft_patch",
            approval_class="A1",
            resource_path="/",
            cost_cents=1,
            summary="Bounded candidate",
        )

        class Store:
            deferred = ()

            def read(self, item):
                return None

            def authority(self, cycle, generation):
                return "active"

            def due(self, cycle):
                return ()

            def gate_receipt(self, item):
                return None

            def preflight_reason(self, item, generation):
                return reason

            def defer(self, cycle, revisions):
                self.deferred = revisions

            def record(self, item, result):
                return "recorded"

        class Candidates:
            async def prepare(self, item, due):
                return (candidate,)

        class Generation:
            async def current_generation(self):
                return RecoveryGeneration("test-generation-1", 1)

        class Gate:
            recovery_source = Generation()

            async def evaluate(self, item):
                return SimpleNamespace(
                    outcome=Recommendation.ASK_OWNER,
                    reason="BUDGET_OR_AUTHORITY_CHANGED",
                )

        store = Store()
        activities = WeeklyActivities(store, work=None, candidates=Candidates(), gate=Gate())
        return await activities.gate_candidates(stage), store.deferred

    changed, not_deferred = asyncio.run(exercise("recipe_release_revoked"))
    assert changed.outcome == "waiting_owner"
    assert not_deferred == ()
    exhausted, deferred = asyncio.run(exercise("weekly_cap_reached"))
    assert exhausted.outcome == "deferred"
    assert len(deferred) == 1
