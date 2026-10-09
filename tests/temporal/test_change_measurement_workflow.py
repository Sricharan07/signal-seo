"""Real Temporal horizon timers and deterministic history, without provider I/O."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from signal_core.change_measurement import ChangeMeasurementJob, ChangeMeasurementWorkflow
from temporalio import activity
from temporalio.worker import Replayer, Worker

from tests.temporal_runtime import start_temporal


@pytest.mark.parametrize("horizon", [7, 28, 90])
def test_horizon_timer_and_reported_state_replay(horizon):
    async def exercise():
        environment = await start_temporal(
            ip="127.0.0.1",
            ui=False,
            download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
            dev_server_download_version="default",
            dev_server_log_level="error",
        )
        calls = []
        try:
            job = ChangeMeasurementJob(
                str(uuid4()),
                str(uuid4()),
                str(uuid4()),
                horizon,
                (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
            )

            @activity.defn(name="signal.change.measure.v1")
            async def measure(value: ChangeMeasurementJob) -> str:
                assert datetime.now(UTC) >= datetime.fromisoformat(value.due_at)
                calls.append(value.horizon)
                return "measured_as_reported"

            async with Worker(
                environment.client,
                task_queue=job.workflow_id,
                workflows=[ChangeMeasurementWorkflow],
                activities=[measure],
            ):
                handle = await environment.client.start_workflow(
                    ChangeMeasurementWorkflow.run,
                    job,
                    id=job.workflow_id,
                    task_queue=job.workflow_id,
                )
                assert await asyncio.wait_for(handle.result(), 20) == "measured_as_reported"
                history = await handle.fetch_history()
            assert calls == [horizon]
            await Replayer(workflows=[ChangeMeasurementWorkflow]).replay_workflow(history)
        finally:
            await environment.shutdown()

    asyncio.run(exercise())
