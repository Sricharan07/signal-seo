"""Imported-data-only measurement; no provider or credential ports."""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from signal_core.database import _clean_transaction
    from signal_core.weekly_loop import WeeklyStage, WeeklyStageResult


class WeeklyChangeMeasurements:
    def __init__(self, connection_factory, recovery_source):
        self.connection_factory = connection_factory
        self.recovery_source = recovery_source

    def _run(self, stage, generation):
        c = stage.cycle
        with self.connection_factory() as connection, _clean_transaction(connection):
            return connection.execute(
                "SELECT control.run_weekly_change_measurements(%s,%s,%s,%s,%s,%s)",
                (
                    UUID(c.site.tenant_id),
                    UUID(c.site.site_id),
                    date.fromisoformat(c.week_start),
                    UUID(c.cycle_id),
                    UUID(c.site.grant_id),
                    generation,
                ),
            ).fetchone()[0]

    async def run(self, stage: WeeklyStage) -> WeeklyStageResult:
        generation = (await self.recovery_source.current_generation()).value
        count = await asyncio.to_thread(self._run, stage, generation)
        return WeeklyStageResult(
            "stopped" if count is None else "completed",
            "AUTHORITY_CHANGED" if count is None else "PROVIDER_REPORTED_MEASUREMENTS",
            (f"cycle:{stage.cycle.cycle_id}",) if count is not None else (),
        )


@dataclass(frozen=True)
class ChangeMeasurementJob:
    tenant_id: str
    site_id: str
    operation_id: str
    horizon: int
    due_at: str

    def __post_init__(self):
        for value in (self.tenant_id, self.site_id, self.operation_id):
            if str(UUID(value)) != value:
                raise ValueError("Canonical measurement identities are required.")
        if self.horizon not in (7, 28, 90) or datetime.fromisoformat(self.due_at).tzinfo is None:
            raise ValueError("An exact horizon and aware due time are required.")

    @property
    def workflow_id(self):
        return (
            f"signal:ChangeMeasurement:{self.tenant_id}:{self.site_id}:"
            f"{self.operation_id}:{self.horizon}"
        )


class ChangeMeasurementActivities:
    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    def jobs(self, tenant_id: str, site_id: str) -> tuple[ChangeMeasurementJob, ...]:
        with self.connection_factory() as connection, _clean_transaction(connection):
            rows = connection.execute(
                "SELECT * FROM control.list_change_measurement_schedule(%s,%s)",
                (UUID(tenant_id), UUID(site_id)),
            ).fetchall()
        return tuple(
            ChangeMeasurementJob(tenant_id, site_id, str(r[0]), r[1], r[2].isoformat())
            for r in rows
        )

    def measure(self, job: ChangeMeasurementJob) -> str:
        with self.connection_factory() as connection, _clean_transaction(connection):
            row = connection.execute(
                "SELECT control.measure_scheduled_change(%s,%s,%s,%s)",
                (UUID(job.tenant_id), UUID(job.site_id), UUID(job.operation_id), job.horizon),
            ).fetchone()[0]
        if row is None:
            raise ApplicationError("Measurement scope unavailable.", non_retryable=True)
        return row["state"]

    @activity.defn(name="signal.change.measure.v1")
    async def run(self, job: ChangeMeasurementJob) -> str:
        task = asyncio.create_task(asyncio.to_thread(self.measure, job))
        while not task.done():
            activity.heartbeat()
            await asyncio.wait({task}, timeout=2)
        return await task


@workflow.defn
class ChangeMeasurementWorkflow:
    @workflow.run
    async def run(self, job: ChangeMeasurementJob) -> str:
        due_at = datetime.fromisoformat(job.due_at)
        if due_at > workflow.now():
            await workflow.sleep(due_at - workflow.now())
        # The first calendar window may close after the elapsed-day due time.
        # Provider lag is polled from imported evidence only, never via egress.
        for _ in range(31):
            state = await workflow.execute_activity(
                "signal.change.measure.v1",
                job,
                result_type=str,
                start_to_close_timeout=timedelta(minutes=2),
                heartbeat_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            if state not in {"not_yet_due", "awaiting_data"}:
                return state
            await workflow.sleep(timedelta(days=1))
        return "awaiting_data"
