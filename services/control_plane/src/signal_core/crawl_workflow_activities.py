"""Temporal activity adapters for bounded crawl execution and terminal projection."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from psycopg import Connection
from temporalio import activity
from temporalio.exceptions import ApplicationError

from signal_core.workflow_contracts import (
    CRAWL_ACTIVITY_NAME,
    TERMINAL_ACTIVITY_NAME,
    CrawlHeartbeat,
    CrawlManifestReference,
    CrawlSiteWorkflowInput,
    CrawlTerminalInput,
    CrawlTerminalProjection,
    validate_crawl_heartbeat,
    validate_crawl_manifest_reference,
    validate_crawl_terminal_input,
    validate_crawl_terminal_projection,
    validate_crawl_workflow_input,
)
from signal_core.workflow_terminal import (
    WorkflowTerminalConflict,
    WorkflowTerminalUnavailable,
    record_crawl_terminal,
)


class CrawlExecutionRetryable(Exception):
    """A classified crawl failure may be retried under the same activity identity."""


class CrawlExecutionRejected(Exception):
    """A classified crawl request cannot run under the bound scope or policy."""


class CrawlExecutor(Protocol):
    async def execute(
        self,
        command: CrawlSiteWorkflowInput,
        *,
        heartbeat: Callable[[CrawlHeartbeat], None],
    ) -> CrawlManifestReference: ...


class TerminalStore(Protocol):
    def record(self, terminal: CrawlTerminalInput) -> CrawlTerminalProjection: ...


class CrawlSiteActivities:
    """Run an injected crawl executor without exposing its errors to history."""

    def __init__(self, executor: CrawlExecutor) -> None:
        if not callable(getattr(executor, "execute", None)):
            raise ValueError("Crawl activities require an executor.")
        self._executor = executor

    @activity.defn(name=CRAWL_ACTIVITY_NAME)
    async def execute(self, command: CrawlSiteWorkflowInput) -> CrawlManifestReference:
        try:
            validated = validate_crawl_workflow_input(command)
        except ValueError:
            raise ApplicationError(
                "Crawl input was rejected.",
                type="crawl_rejected",
                non_retryable=True,
            ) from None
        self._heartbeat(CrawlHeartbeat(1, "starting", 0, 0))
        try:
            result = await self._executor.execute(validated, heartbeat=self._heartbeat)
        except CrawlExecutionRejected:
            raise ApplicationError(
                "Crawl execution was rejected.",
                type="crawl_rejected",
                non_retryable=True,
            ) from None
        except CrawlExecutionRetryable:
            raise ApplicationError(
                "Crawl execution is temporarily unavailable.",
                type="crawl_retryable",
            ) from None
        except Exception:
            raise ApplicationError(
                "Crawl execution failed safely.",
                type="crawl_internal",
                non_retryable=True,
            ) from None
        try:
            validated_result = validate_crawl_manifest_reference(result)
        except ValueError:
            raise ApplicationError(
                "Crawl result was rejected.",
                type="invalid_crawl_result",
                non_retryable=True,
            ) from None
        if (
            validated_result.scope_version != validated.scope_version
            or validated_result.crawl_policy_version != validated.crawl_policy_version
        ):
            raise ApplicationError(
                "Crawl result was rejected.",
                type="invalid_crawl_result",
                non_retryable=True,
            ) from None
        self._heartbeat(
            CrawlHeartbeat(
                1,
                "completed",
                validated_result.discovered_count,
                validated_result.terminal_count,
            )
        )
        return validated_result

    @staticmethod
    def _heartbeat(progress: CrawlHeartbeat) -> None:
        try:
            validated = validate_crawl_heartbeat(progress)
        except ValueError:
            raise CrawlExecutionRejected() from None
        activity.heartbeat(validated)


class PostgresTerminalStore:
    """Open a fresh workflow-role connection for each terminal projection."""

    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[Connection]],
    ) -> None:
        if not callable(connection_factory):
            raise ValueError("A workflow connection factory is required.")
        self._connection_factory = connection_factory

    def record(self, terminal: CrawlTerminalInput) -> CrawlTerminalProjection:
        with self._connection_factory() as connection:
            return record_crawl_terminal(connection, terminal)


class WorkflowTerminalActivities:
    """Project terminal state with retryable availability and closed failures."""

    def __init__(self, store: TerminalStore) -> None:
        if not callable(getattr(store, "record", None)):
            raise ValueError("Terminal activities require a durable store.")
        self._store = store

    @activity.defn(name=TERMINAL_ACTIVITY_NAME)
    async def record(self, terminal: CrawlTerminalInput) -> CrawlTerminalProjection:
        try:
            validated = validate_crawl_terminal_input(terminal)
        except ValueError:
            raise ApplicationError(
                "Terminal projection input was rejected.",
                type="terminal_projection_rejected",
                non_retryable=True,
            ) from None
        try:
            projection = await asyncio.to_thread(self._store.record, validated)
        except WorkflowTerminalUnavailable:
            raise ApplicationError(
                "Terminal projection is not ready.",
                type="terminal_projection_unavailable",
            ) from None
        except WorkflowTerminalConflict:
            raise ApplicationError(
                "Terminal projection conflicts with committed evidence.",
                type="terminal_projection_conflict",
                non_retryable=True,
            ) from None
        except Exception:
            raise ApplicationError(
                "Terminal projection is temporarily unavailable.",
                type="terminal_projection_unavailable",
            ) from None
        try:
            return validate_crawl_terminal_projection(projection)
        except ValueError:
            raise ApplicationError(
                "Terminal projection returned invalid evidence.",
                type="terminal_projection_invalid",
                non_retryable=True,
            ) from None
