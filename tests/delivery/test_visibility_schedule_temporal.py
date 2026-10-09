"""Real PostgreSQL holds survive real Temporal worker cancellation and replay."""

import asyncio
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from temporalio import workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Replayer, Worker

from tests.temporal_runtime import start_temporal

with workflow.unsafe.imports_passed_through():
    import psycopg
    from signal_core.ai_visibility_schedule import (
        AiVisibilityReobserve,
        VisibilityActivities,
        VisibilityRun,
        VisibilityScheduleReconciler,
        VisibilitySite,
    )
    from signal_core.recovery_authority import RecoveryGeneration

    from tests.control_plane.test_visibility_schedule import projection, settings
    from tests.control_plane.test_visibility_schedule import schedule_context as schedule_context


def test_schedule_idempotency_worker_loss_retains_hold(api, schedule_context):
    scope, context, _, _ = schedule_context
    settings(api, scope, context, monthly_cap_micros=25_000)

    async def exercise():
        environment = await start_temporal(
            ip="127.0.0.1",
            ui=False,
            download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
            dev_server_download_version="default",
            dev_server_log_level="error",
        )

        @contextmanager
        def connections():
            with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as c:
                yield c

        class Recovery:
            async def current_generation(self):
                return RecoveryGeneration(context["generation"], 1)

        queue = "visibility-qualification-" + uuid4().hex
        site = VisibilitySite(str(scope.tenant_id), str(scope.site_id))
        reconciler = VisibilityScheduleReconciler(
            connections, environment.client, Recovery(), queue
        )
        attempted = []
        blocked = asyncio.Event()

        async def lost(site, operation):
            attempted.append(operation)
            assert projection(api, scope, context)["held_micros"] == 25_000
            blocked.set()
            await asyncio.Event().wait()

        activities = VisibilityActivities(
            connections,
            None,
            Recovery(),
            credentials=object(),
            egress_factory=lost,
            ceilings={"openai": 25_000},
        )
        try:
            assert await reconciler.reconcile(site) == "created"
            assert await reconciler.reconcile(site) == "updated"
            schedule = environment.client.get_schedule_handle(
                f"signal:visibility:{site.tenant_id}:{site.site_id}"
            )
            description = await schedule.describe()
            assert description.schedule.spec.intervals[0].every == timedelta(days=7)
            async with Worker(
                environment.client,
                task_queue=queue,
                workflows=[AiVisibilityReobserve],
                activities=[activities.run],
                graceful_shutdown_timeout=timedelta(milliseconds=100),
            ):
                handle = await environment.client.start_workflow(
                    AiVisibilityReobserve.run,
                    site,
                    id="visibility-" + uuid4().hex,
                    task_queue=queue,
                )
                await asyncio.wait_for(blocked.wait(), 20)
            assert projection(api, scope, context)["held_micros"] == 25_000
            replacement = VisibilityActivities(connections, None, Recovery())
            async with Worker(
                environment.client,
                task_queue=queue,
                workflows=[AiVisibilityReobserve],
                activities=[replacement.run],
            ):
                result = await asyncio.wait_for(handle.result(), 20)
            assert result["state"] == "unavailable" and len(attempted) == 1
            assert projection(api, scope, context)["held_micros"] == 25_000
            await Replayer(workflows=[AiVisibilityReobserve]).replay_workflow(
                await handle.fetch_history()
            )
            await schedule.delete()
        finally:
            await environment.shutdown()

    asyncio.run(exercise())


@workflow.defn
class VisibilityKillProbe:
    @workflow.run
    async def run(self, request: VisibilityRun) -> dict:
        # Short qualification deadlines exercise process death without waiting 65 minutes.
        return await workflow.execute_activity(
            "signal.visibility.reobserve.v1",
            request,
            result_type=dict,
            start_to_close_timeout=timedelta(seconds=3),
            schedule_to_close_timeout=timedelta(seconds=45),
            retry_policy=RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=1)),
        )


async def _kill_worker(endpoint, queue, generation, marker):
    @contextmanager
    def connections():
        with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as c:
            yield c

    class Recovery:
        async def current_generation(self):
            return RecoveryGeneration(generation, 1)

    async def interrupted(site, operation):
        Path(marker).write_text(str(operation))
        await asyncio.Event().wait()

    activities = VisibilityActivities(
        connections,
        None,
        Recovery(),
        credentials=object(),
        egress_factory=interrupted,
        ceilings={"openai": 25_000},
    )
    async with Worker(
        await Client.connect(endpoint), task_queue=queue, activities=[activities.run]
    ):
        await asyncio.Event().wait()


def test_sigkill_worker_keeps_reservation_and_retry_does_not_redispatch(
    api, schedule_context, tmp_path
):
    scope, context, _, _ = schedule_context
    settings(api, scope, context, monthly_cap_micros=25_000)

    async def exercise():
        environment = await start_temporal(
            ip="127.0.0.1",
            ui=False,
            download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
            dev_server_download_version="default",
            dev_server_log_level="error",
        )
        queue = "visibility-kill-" + uuid4().hex
        marker = tmp_path / "synthetic-reserved-operation"
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import asyncio,sys; "
                "from tests.delivery.test_visibility_schedule_temporal import _kill_worker; "
                "asyncio.run(_kill_worker(*sys.argv[1:]))",
                environment.client.service_client.config.target_host,
                queue,
                context["generation"],
                str(marker),
            ],
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            async with Worker(
                environment.client, task_queue=queue, workflows=[VisibilityKillProbe]
            ):
                handle = await environment.client.start_workflow(
                    VisibilityKillProbe.run,
                    VisibilityRun(
                        VisibilitySite(str(scope.tenant_id), str(scope.site_id)), str(uuid4())
                    ),
                    id="visibility-kill-probe-" + uuid4().hex,
                    task_queue=queue,
                )

                async def reserved():
                    while not marker.exists():
                        assert child.poll() is None
                        await asyncio.sleep(0.05)

                await asyncio.wait_for(reserved(), 20)
                assert projection(api, scope, context)["held_micros"] == 25_000
                child.kill()
                assert child.wait(timeout=10) == -9

                @contextmanager
                def connections():
                    with psycopg.connect(
                        os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True
                    ) as c:
                        yield c

                class Recovery:
                    async def current_generation(self):
                        return RecoveryGeneration(context["generation"], 1)

                redispatched = []

                async def forbidden(*args):
                    redispatched.append(args)
                    raise AssertionError("A reserved paid call must never be redispatched.")

                replacement = VisibilityActivities(
                    connections,
                    None,
                    Recovery(),
                    credentials=object(),
                    egress_factory=forbidden,
                    ceilings={"openai": 25_000},
                )
                async with Worker(
                    environment.client, task_queue=queue, activities=[replacement.run]
                ):
                    assert (await asyncio.wait_for(handle.result(), 20))["state"] == "unavailable"
                assert redispatched == []
                assert projection(api, scope, context)["held_micros"] == 25_000
                await Replayer(workflows=[VisibilityKillProbe]).replay_workflow(
                    await handle.fetch_history()
                )
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            await environment.shutdown()

    asyncio.run(exercise())
