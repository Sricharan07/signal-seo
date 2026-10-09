"""Configured single-site weekly worker and bounded delivery maintenance."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from signal_core.change_measurement import ChangeMeasurementActivities, ChangeMeasurementWorkflow
from signal_core.chat_reports import ChatDelivery
from signal_core.database import Scope
from signal_core.health_monitoring import HealthMonitor
from signal_core.weekly_loop import (
    WeeklyActivities,
    WeeklyCycle,
    WeeklySite,
    WeeklySiteLoop,
    WeeklyStage,
    WeeklyStageResult,
)
from signal_core.weekly_schedule import WeeklyScheduleReconciler


@dataclass(frozen=True)
class WeeklyDeliveryRuntime:
    temporal: Client
    site: WeeklySite
    activities: WeeklyActivities
    reconciler: WeeklyScheduleReconciler
    health_monitor: HealthMonitor | None = None
    chat_delivery: ChatDelivery | None = None

    def __post_init__(self):
        if self.chat_delivery is not None:
            scope = Scope(UUID(self.site.tenant_id), UUID(self.site.site_id))
            if self.chat_delivery.scope != scope:
                raise ValueError("Weekly chat delivery scope mismatch.")
            if self.health_monitor is not None:
                if self.health_monitor.scope != scope:
                    raise ValueError("Health monitoring scope mismatch.")
                self.health_monitor.chat_alerts = self.chat_delivery.queue_health

    def worker(self) -> Worker:
        if self.activities.delivery is None:
            raise ValueError("The delivery port must be configured explicitly.")
        return Worker(
            self.temporal,
            task_queue="signal.weekly.v1",
            workflows=[WeeklySiteLoop, ChangeMeasurementWorkflow],
            activities=[
                self.activities.open,
                self.activities.run_stage,
                self.activities.run_skills,
                self.activities.gate_candidates,
                self.activities.handoff,
                self.activities.skip,
                self.activities.close,
                self.measurements().run,
            ],
            max_concurrent_activities=1,
        )

    def measurements(self) -> ChangeMeasurementActivities:
        return ChangeMeasurementActivities(self.activities.store.connection_factory)

    async def reconcile_measurements(self) -> None:
        jobs = await asyncio.to_thread(
            self.measurements().jobs, self.site.tenant_id, self.site.site_id
        )
        for job in jobs:
            try:
                await self.temporal.start_workflow(
                    ChangeMeasurementWorkflow.run,
                    job,
                    id=job.workflow_id,
                    task_queue="signal.weekly.v1",
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                )
            except WorkflowAlreadyStartedError:
                pass

    async def qualify_once(self) -> tuple[WeeklyStageResult, ...]:
        """Run one real current-week cycle; a repeat attaches to its exact workflow."""
        cycle = WeeklyCycle.for_window(self.site, datetime.now(UTC).date())
        async with self.worker():
            identifier = f"signal:WeeklyDeliveryQualification:{cycle.cycle_id}"
            try:
                handle = await self.temporal.start_workflow(
                    WeeklySiteLoop.run,
                    self.site,
                    id=identifier,
                    task_queue="signal.weekly.v1",
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                )
            except WorkflowAlreadyStartedError:
                handle = self.temporal.get_workflow_handle(
                    identifier, result_type=tuple[WeeklyStageResult, ...]
                )
            return await handle.result()

    async def serve(self, stop: asyncio.Event, *, interval_seconds: float = 30) -> None:
        if self.health_monitor is not None:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self.health_monitor.serve(stop))
                tasks.create_task(self._serve_work(stop, interval_seconds=interval_seconds))
        else:
            await self._serve_work(stop, interval_seconds=interval_seconds)

    async def _serve_work(self, stop: asyncio.Event, *, interval_seconds: float) -> None:
        if not 30 <= interval_seconds <= 300:
            raise ValueError("Bounded maintenance cadence is required.")
        async with self.worker():
            while not stop.is_set():
                await self.reconciler.reconcile(self.site)
                cycle = WeeklyCycle.for_window(self.site, datetime.now(UTC).date())
                # No new gate decisions are minted here. Exact approvals and existing
                # journaled operations retain their own current-authority checks.
                await self.activities.delivery.dispatch(cycle, ())
                await self.activities.work.run(WeeklyStage(cycle, "verify"))
                await self.reconcile_measurements()
                if self.chat_delivery is not None:
                    await self.chat_delivery.pump()
                try:
                    await asyncio.wait_for(stop.wait(), interval_seconds)
                except TimeoutError:
                    pass
