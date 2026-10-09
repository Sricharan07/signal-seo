"""Deterministic Temporal workflow for one bounded CrawlSite command."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError
from temporalio.workflow import ActivityCancellationType

from signal_core.workflow_contracts import (
    CRAWL_ACTIVITY_NAME,
    TERMINAL_ACTIVITY_NAME,
    CrawlManifestReference,
    CrawlSiteWorkflowInput,
    CrawlTerminalInput,
    CrawlTerminalProjection,
    validate_crawl_manifest_reference,
    validate_crawl_terminal_projection,
    validate_crawl_workflow_input,
)


@workflow.defn(name="CrawlSite")
class CrawlSiteWorkflow:
    """Run one crawl activity and commit its terminal business projection."""

    @workflow.run
    async def run(self, command: CrawlSiteWorkflowInput) -> CrawlTerminalProjection:
        try:
            validated = validate_crawl_workflow_input(command)
        except ValueError:
            raise ApplicationError(
                "CrawlSite input was rejected.",
                type="invalid_workflow_input",
                non_retryable=True,
            ) from None
        info = workflow.info()
        expected_id = f"signal:CrawlSite:{validated.tenant_id}:{validated.command_id}"
        if info.workflow_id != expected_id or info.workflow_type != "CrawlSite":
            raise ApplicationError(
                "CrawlSite identity was rejected.",
                type="invalid_workflow_identity",
                non_retryable=True,
            )

        try:
            result = await workflow.execute_activity(
                CRAWL_ACTIVITY_NAME,
                validated,
                result_type=CrawlManifestReference,
                schedule_to_close_timeout=timedelta(hours=3, minutes=15),
                start_to_close_timeout=timedelta(hours=1, minutes=5),
                heartbeat_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2,
                    maximum_interval=timedelta(seconds=5),
                    maximum_attempts=3,
                    non_retryable_error_types=[
                        "crawl_rejected",
                        "crawl_internal",
                        "invalid_crawl_result",
                    ],
                ),
                cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                summary="Execute bounded site crawl",
            )
            validate_crawl_manifest_reference(result)
            if (
                result.scope_version != validated.scope_version
                or result.crawl_policy_version != validated.crawl_policy_version
            ):
                raise ValueError("Crawl result version mismatch.")
        except asyncio.CancelledError:
            terminal = _terminal_input(
                validated,
                workflow_id=info.workflow_id,
                first_run_id=info.first_execution_run_id,
                state="cancelled",
                result=None,
                reason="crawl_cancelled",
            )
            await asyncio.shield(_record_terminal(terminal))
            raise
        except ActivityError as error:
            if (
                isinstance(error.cause, CancelledError)
                or workflow.cancellation_reason() is not None
            ):
                terminal = _terminal_input(
                    validated,
                    workflow_id=info.workflow_id,
                    first_run_id=info.first_execution_run_id,
                    state="cancelled",
                    result=None,
                    reason="crawl_cancelled",
                )
                await asyncio.shield(_record_terminal(terminal))
                raise asyncio.CancelledError() from None
            terminal = _terminal_input(
                validated,
                workflow_id=info.workflow_id,
                first_run_id=info.first_execution_run_id,
                state="failed",
                result=None,
                reason="crawl_activity_failed",
            )
            await _record_terminal(terminal)
            raise
        except ValueError:
            terminal = _terminal_input(
                validated,
                workflow_id=info.workflow_id,
                first_run_id=info.first_execution_run_id,
                state="failed",
                result=None,
                reason="crawl_activity_failed",
            )
            await _record_terminal(terminal)
            raise ApplicationError(
                "Crawl result was rejected.",
                type="invalid_crawl_result",
                non_retryable=True,
            ) from None

        terminal = _terminal_input(
            validated,
            workflow_id=info.workflow_id,
            first_run_id=info.first_execution_run_id,
            state="succeeded",
            result=result,
            reason=None,
        )
        projection = await _record_terminal(terminal)
        if (
            projection.tenant_id != terminal.tenant_id
            or projection.site_id != terminal.site_id
            or projection.command_id != terminal.command_id
            or projection.workflow_id != terminal.workflow_id
            or projection.first_run_id != terminal.first_run_id
            or projection.workflow_state != terminal.state
            or projection.result_reference != terminal.result_reference
            or projection.reason != terminal.reason
        ):
            raise ApplicationError(
                "Terminal projection evidence was rejected.",
                type="terminal_projection_invalid",
                non_retryable=True,
            )
        return projection


async def _record_terminal(terminal: CrawlTerminalInput) -> CrawlTerminalProjection:
    projection = await workflow.execute_activity(
        TERMINAL_ACTIVITY_NAME,
        terminal,
        result_type=CrawlTerminalProjection,
        schedule_to_close_timeout=timedelta(minutes=2),
        start_to_close_timeout=timedelta(seconds=15),
        retry_policy=RetryPolicy(
            initial_interval=timedelta(seconds=1),
            backoff_coefficient=2,
            maximum_interval=timedelta(seconds=10),
            maximum_attempts=8,
            non_retryable_error_types=[
                "terminal_projection_rejected",
                "terminal_projection_conflict",
                "terminal_projection_invalid",
            ],
        ),
        cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        summary="Record crawl terminal projection",
    )
    try:
        return validate_crawl_terminal_projection(projection)
    except ValueError:
        raise ApplicationError(
            "Terminal projection evidence was rejected.",
            type="terminal_projection_invalid",
            non_retryable=True,
        ) from None


def _terminal_input(
    command: CrawlSiteWorkflowInput,
    *,
    workflow_id: str,
    first_run_id: str,
    state: str,
    result: CrawlManifestReference | None,
    reason: str | None,
) -> CrawlTerminalInput:
    return CrawlTerminalInput(
        schema_version=1,
        tenant_id=command.tenant_id,
        site_id=command.site_id,
        command_id=command.command_id,
        workflow_id=workflow_id,
        first_run_id=first_run_id,
        state=state,
        result_reference=result,
        reason=reason,
    )
