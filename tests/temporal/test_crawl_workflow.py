import asyncio
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from signal_core.crawl_workflow import CrawlSiteWorkflow
from signal_core.crawl_workflow_activities import (
    CrawlExecutionRejected,
    CrawlExecutionRetryable,
    CrawlSiteActivities,
    WorkflowTerminalActivities,
)
from signal_core.workflow_contracts import (
    CrawlHeartbeat,
    CrawlManifestReference,
    CrawlSiteWorkflowInput,
    CrawlTerminalProjection,
)
from signal_core.workflow_terminal import WorkflowTerminalUnavailable
from temporalio.client import WorkflowFailureError, WorkflowHandle
from temporalio.worker import Replayer, Worker

from tests.temporal_runtime import start_temporal


def command() -> CrawlSiteWorkflowInput:
    return CrawlSiteWorkflowInput(
        schema_version=1,
        tenant_id=str(uuid4()),
        site_id=str(uuid4()),
        command_id=str(uuid4()),
        source_event_id=str(uuid4()),
        scope_version=1,
        crawl_policy_version=1,
    )


def manifest() -> CrawlManifestReference:
    return CrawlManifestReference(
        schema_version=1,
        manifest_id=str(uuid4()),
        manifest_sha256="a" * 64,
        coverage="partial",
        discovered_count=5,
        terminal_count=4,
        scope_version=1,
        crawl_policy_version=1,
    )


class RetryOnceExecutor:
    def __init__(self) -> None:
        self.attempts = 0
        self.heartbeats = []
        self.result = manifest()

    async def execute(self, command, *, heartbeat):
        self.attempts += 1
        self.heartbeats.append(CrawlHeartbeat(1, "crawling", 2, 1))
        heartbeat(self.heartbeats[-1])
        if self.attempts == 1:
            raise CrawlExecutionRetryable()
        return self.result


class RejectingExecutor:
    async def execute(self, command, *, heartbeat):
        raise CrawlExecutionRejected()


class AlwaysRetryableExecutor:
    def __init__(self) -> None:
        self.attempts = 0

    async def execute(self, command, *, heartbeat):
        self.attempts += 1
        raise CrawlExecutionRetryable()


class WaitingExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def execute(self, command, *, heartbeat):
        heartbeat(CrawlHeartbeat(1, "crawling", 1, 0))
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class TerminalStore:
    def __init__(self, *, unavailable_attempts=0) -> None:
        self.calls = []
        self.unavailable_attempts = unavailable_attempts

    def record(self, item):
        self.calls.append(item)
        if self.unavailable_attempts > 0:
            self.unavailable_attempts -= 1
            raise WorkflowTerminalUnavailable()
        return CrawlTerminalProjection(
            schema_version=1,
            tenant_id=item.tenant_id,
            site_id=item.site_id,
            progress_event_id=str(uuid4()),
            command_id=item.command_id,
            workflow_id=item.workflow_id,
            first_run_id=item.first_run_id,
            command_status=item.state,
            workflow_state=item.state,
            result_reference=item.result_reference,
            reason=item.reason,
            duplicate=False,
        )


async def start(client, item, task_queue) -> WorkflowHandle:
    return await client.start_workflow(
        CrawlSiteWorkflow.run,
        item,
        id=f"signal:CrawlSite:{item.tenant_id}:{item.command_id}",
        task_queue=task_queue,
        result_type=CrawlTerminalProjection,
    )


async def exercise_real_workflow() -> None:
    environment = await start_temporal(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    try:
        executor = RetryOnceExecutor()
        terminal_store = TerminalStore(unavailable_attempts=1)
        task_queue = f"crawl-workflow-{uuid4()}"
        async with Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[CrawlSiteWorkflow],
            activities=[
                CrawlSiteActivities(executor).execute,
                WorkflowTerminalActivities(terminal_store).record,
            ],
        ):
            item = command()
            handle = await start(environment.client, item, task_queue)
            result = await asyncio.wait_for(handle.result(), timeout=20)
            assert result.workflow_state == "succeeded"
            assert result.result_reference == executor.result
            assert executor.attempts == 2
            assert [call.state for call in terminal_store.calls] == [
                "succeeded",
                "succeeded",
            ]
            success_history = await handle.fetch_history()

        async with Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[CrawlSiteWorkflow],
            activities=[
                CrawlSiteActivities(RejectingExecutor()).execute,
                WorkflowTerminalActivities(terminal_store).record,
            ],
        ):
            failed_item = command()
            failed = await start(environment.client, failed_item, task_queue)
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(failed.result(), timeout=20)
            assert terminal_store.calls[-1].state == "failed"
            assert terminal_store.calls[-1].reason == "crawl_activity_failed"
            failed_history = await failed.fetch_history()

            terminal_call_count = len(terminal_store.calls)
            invalid = await start(
                environment.client,
                replace(command(), schema_version=2),
                task_queue,
            )
            with pytest.raises(WorkflowFailureError) as invalid_failure:
                await asyncio.wait_for(invalid.result(), timeout=20)
            assert invalid_failure.value.cause.type == "invalid_workflow_input"
            invalid_history = await invalid.fetch_history()

            wrong_identity = command()
            mismatched = await environment.client.start_workflow(
                CrawlSiteWorkflow.run,
                wrong_identity,
                id=f"signal:CrawlSite:{wrong_identity.tenant_id}:{uuid4()}",
                task_queue=task_queue,
                result_type=CrawlTerminalProjection,
            )
            with pytest.raises(WorkflowFailureError) as identity_failure:
                await asyncio.wait_for(mismatched.result(), timeout=20)
            assert identity_failure.value.cause.type == "invalid_workflow_identity"
            mismatched_history = await mismatched.fetch_history()
            assert len(terminal_store.calls) == terminal_call_count

        retrying_executor = AlwaysRetryableExecutor()
        async with Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[CrawlSiteWorkflow],
            activities=[
                CrawlSiteActivities(retrying_executor).execute,
                WorkflowTerminalActivities(terminal_store).record,
            ],
        ):
            exhausted = await start(environment.client, command(), task_queue)
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(exhausted.result(), timeout=20)
            assert retrying_executor.attempts == 3
            assert terminal_store.calls[-1].state == "failed"
            exhausted_history = await exhausted.fetch_history()

        await Replayer(workflows=[CrawlSiteWorkflow]).replay_workflow(success_history)
        await Replayer(workflows=[CrawlSiteWorkflow]).replay_workflow(failed_history)
        await Replayer(workflows=[CrawlSiteWorkflow]).replay_workflow(invalid_history)
        await Replayer(workflows=[CrawlSiteWorkflow]).replay_workflow(mismatched_history)
        await Replayer(workflows=[CrawlSiteWorkflow]).replay_workflow(exhausted_history)
    finally:
        await environment.shutdown()


async def exercise_cancellation() -> None:
    environment = await start_temporal(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    try:
        executor = WaitingExecutor()
        terminal_store = TerminalStore()
        task_queue = f"crawl-cancel-{uuid4()}"
        async with Worker(
            environment.client,
            task_queue=task_queue,
            workflows=[CrawlSiteWorkflow],
            activities=[
                CrawlSiteActivities(executor).execute,
                WorkflowTerminalActivities(terminal_store).record,
            ],
        ):
            handle = await start(environment.client, command(), task_queue)
            await asyncio.wait_for(executor.started.wait(), timeout=10)
            await handle.cancel()
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), timeout=20)
            await asyncio.wait_for(executor.cancelled.wait(), timeout=10)
            assert [call.state for call in terminal_store.calls] == ["cancelled"]
            history = await handle.fetch_history()

        await Replayer(workflows=[CrawlSiteWorkflow]).replay_workflow(history)
    finally:
        await environment.shutdown()


def test_crawl_workflow_retries_projects_terminal_states_and_replays():
    asyncio.run(exercise_real_workflow())


def test_crawl_workflow_cancellation_is_projected_and_replays():
    asyncio.run(exercise_cancellation())
