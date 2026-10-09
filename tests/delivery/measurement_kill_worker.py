"""Invocation-owned synthetic worker, intentionally killed by the integration test."""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

import psycopg
from signal_core.change_measurement import ChangeMeasurementJob, ChangeMeasurementWorkflow
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker


async def main():
    settings = json.loads(Path(sys.argv[1]).read_text())

    @activity.defn(name="signal.change.measure.v1")
    async def commit_and_block(job: ChangeMeasurementJob) -> str:
        with psycopg.connect(os.environ["SIGNAL_TEST_ADMIN_DSN"], autocommit=True) as connection:
            connection.execute(
                "SELECT control.measure_change_horizon(%s,%s,%s,%s,%s)",
                (
                    UUID(job.tenant_id),
                    UUID(job.site_id),
                    UUID(job.operation_id),
                    job.horizon,
                    datetime.fromisoformat(settings["observation_time"]),
                ),
            ).fetchone()
        activity.heartbeat()
        Path(settings["marker"]).write_text("synthetic-committed")
        await asyncio.Event().wait()
        return "failed"

    client = await Client.connect(settings["address"])
    async with Worker(
        client,
        task_queue=settings["queue"],
        workflows=[ChangeMeasurementWorkflow],
        activities=[commit_and_block],
    ):
        await asyncio.Event().wait()


if __name__ == "__main__":
    if os.environ.get("SIGNAL_DATABASE_LAB") != "1" or os.environ.get("SIGNAL_TEMPORAL_LAB") != "1":
        raise SystemExit("Only disposable lab state is accepted.")
    asyncio.run(main())
