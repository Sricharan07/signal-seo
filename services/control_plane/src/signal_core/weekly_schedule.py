"""Reconcile one site's verified weekly Temporal schedule from current authority."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from psycopg import Connection
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
)
from temporalio.service import RPCError, RPCStatusCode

from signal_core.database import _clean_transaction
from signal_core.recovery_authority import RecoveryGeneration
from signal_core.weekly_loop import WeeklySite


class RecoverySource(Protocol):
    async def current_generation(self) -> RecoveryGeneration: ...


async def upsert_or_pause_schedule(
    temporal: Client,
    schedule_id: str,
    schedule: Schedule | None,
    *,
    pause_note: str,
    paused_outcome: str = "paused",
) -> str:
    handle = temporal.get_schedule_handle(schedule_id)
    if schedule is None:
        try:
            await handle.pause(note=pause_note)
        except RPCError as error:
            if error.status != RPCStatusCode.NOT_FOUND:
                raise
        return paused_outcome
    try:
        await temporal.create_schedule(schedule_id, schedule)
        return "created"
    except ScheduleAlreadyRunningError:
        pass
    except RPCError as error:
        if error.status != RPCStatusCode.ALREADY_EXISTS:
            raise
    await handle.update(lambda _: ScheduleUpdate(schedule))
    return "updated"


@dataclass(frozen=True)
class WeeklyScheduleReconciler:
    connection: Connection
    temporal: Client
    recovery_source: RecoverySource

    async def reconcile(self, site: WeeklySite) -> str:
        generation = await self.recovery_source.current_generation()
        if not isinstance(generation, RecoveryGeneration):
            raise ValueError("Current recovery authority is unavailable.")
        with _clean_transaction(self.connection):
            eligible = self.connection.execute(
                "SELECT control.weekly_schedule_eligibility(%s,%s,%s,%s)",
                (site.tenant_id, site.site_id, site.grant_id, generation.value),
            ).fetchone()[0]
        schedule_id = f"signal:weekly:{site.tenant_id}:{site.site_id}"
        if not eligible:
            return await upsert_or_pause_schedule(
                self.temporal,
                schedule_id,
                None,
                pause_note="Standing authority or site verification unavailable",
            )
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                "WeeklySiteLoop",
                site,
                id=f"signal:WeeklySiteLoop:{site.tenant_id}:{site.site_id}",
                task_queue="signal.weekly.v1",
            ),
            spec=ScheduleSpec(cron_expressions=["0 9 * * 1"], time_zone_name="UTC"),
            policy=SchedulePolicy(
                overlap=ScheduleOverlapPolicy.SKIP,
                catchup_window=timedelta(minutes=1),
                pause_on_failure=True,
            ),
            state=ScheduleState(paused=False),
        )
        return await upsert_or_pause_schedule(
            self.temporal,
            schedule_id,
            schedule,
            pause_note="Standing authority or site verification unavailable",
        )
