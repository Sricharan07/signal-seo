"""Health observations against real, invocation-owned Temporal schedules."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from signal_core.database import Scope
from signal_core.health_monitoring import evaluate
from signal_core.health_probes import measurement_health, temporal_health, weekly_health
from temporalio.client import Schedule, ScheduleActionStartWorkflow, ScheduleSpec

from tests.temporal_runtime import start_temporal


def test_real_temporal_reachability_schedule_pause_missing_and_stuck():
    async def exercise():
        environment = await start_temporal(
            ip="127.0.0.1",
            ui=False,
            download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
            dev_server_download_version="default",
            dev_server_log_level="error",
        )
        scope = Scope(uuid4(), uuid4())
        now = datetime.now(UTC)
        client = environment.client
        identifier = f"signal:weekly:{scope.tenant_id}:{scope.site_id}"
        try:
            assert evaluate("temporal", await temporal_health(client), now).state == "ok"
            handle = await client.create_schedule(
                identifier,
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        "WeeklySiteLoop", id=f"synthetic-{uuid4()}", task_queue="synthetic-health"
                    ),
                    spec=ScheduleSpec(intervals=[], cron_expressions=["0 9 * * 1"]),
                ),
            )
            assert (
                evaluate("weekly_schedule", await weekly_health(client, scope), now).state == "ok"
            )
            await handle.pause()
            assert (
                evaluate("weekly_schedule", await weekly_health(client, scope), now).state
                == "warning"
            )
            await handle.unpause()
            await handle.trigger()
            for _ in range(30):
                if (await handle.describe()).info.running_actions:
                    break
                await asyncio.sleep(0.1)
            assert (
                evaluate(
                    "weekly_schedule",
                    await weekly_health(client, scope, now=now + timedelta(days=2)),
                    now,
                ).state
                == "critical"
            )
            await handle.delete()
            try:
                observation = await weekly_health(client, scope)
            except Exception:
                observation = None
            assert evaluate("weekly_schedule", observation, now).state == "unknown"

            class Measurements:
                def jobs(self, tenant, site):
                    return ()

            assert (
                evaluate(
                    "measurement_schedule",
                    await measurement_health(client, Measurements(), scope),
                    now,
                ).state
                == "ok"
            )
        finally:
            await environment.shutdown()

    asyncio.run(exercise())
