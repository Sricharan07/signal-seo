import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

from signal_core.workflow_admission import WorkflowAdmission
from signal_core.workflow_contracts import CrawlSiteWorkflowInput
from signal_core.workflow_start import TemporalWorkflowStarter
from temporalio import workflow
from temporalio.worker import Worker

from tests.temporal_runtime import start_temporal


@workflow.defn(name="CrawlSite")
class TestCrawlSiteWorkflow:
    @workflow.run
    async def run(self, command: CrawlSiteWorkflowInput) -> CrawlSiteWorkflowInput:
        return command


def admission() -> WorkflowAdmission:
    tenant_id, command_id = uuid4(), uuid4()
    return WorkflowAdmission(
        tenant_id=tenant_id,
        source_event_id=uuid4(),
        progress_event_id=uuid4(),
        command_id=command_id,
        site_id=uuid4(),
        workflow_id=f"signal:CrawlSite:{tenant_id}:{command_id}",
        workflow_type="CrawlSite",
        state="admitted",
        processed_at=datetime.now(UTC),
        duplicate=False,
    )


async def listed_executions(client, workflow_id):
    return [item async for item in client.list_workflows(f'WorkflowId = "{workflow_id}"')]


async def exercise_real_server() -> None:
    environment = await start_temporal(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    try:
        admitted = admission()
        starter = TemporalWorkflowStarter(environment.client)
        async with Worker(
            environment.client,
            task_queue="signal.crawl.v1",
            workflows=[TestCrawlSiteWorkflow],
        ):
            first = await asyncio.wait_for(starter.start(admitted), timeout=15)
            duplicate = await asyncio.wait_for(starter.start(admitted), timeout=15)
            assert duplicate.first_run_id == first.first_run_id

            handle = environment.client.get_workflow_handle(
                admitted.workflow_id,
                first_execution_run_id=first.first_run_id,
                result_type=CrawlSiteWorkflowInput,
            )
            assert await asyncio.wait_for(handle.result(), timeout=15) == CrawlSiteWorkflowInput(
                schema_version=1,
                tenant_id=str(admitted.tenant_id),
                site_id=str(admitted.site_id),
                command_id=str(admitted.command_id),
                source_event_id=str(admitted.source_event_id),
            )

            after_close = await asyncio.wait_for(starter.start(admitted), timeout=15)
            assert after_close.first_run_id == first.first_run_id
            assert after_close.evidence_kind == "already_started"
            executions = await asyncio.wait_for(
                listed_executions(environment.client, admitted.workflow_id),
                timeout=15,
            )
            assert len(executions) == 1
            assert executions[0].id == admitted.workflow_id
            assert executions[0].run_id == first.first_run_id
    finally:
        await environment.shutdown()


def test_deterministic_start_is_one_execution_before_and_after_completion():
    asyncio.run(exercise_real_server())
