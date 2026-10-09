import asyncio
from contextlib import contextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
from signal_core.crawl_workflow_activities import (
    CrawlExecutionRejected,
    CrawlExecutionRetryable,
    CrawlSiteActivities,
    PostgresTerminalStore,
    WorkflowTerminalActivities,
)
from signal_core.workflow_contracts import (
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
)
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment


def workflow_input(**overrides):
    tenant_id, command_id = str(uuid4()), str(uuid4())
    values = {
        "schema_version": 1,
        "tenant_id": tenant_id,
        "site_id": str(uuid4()),
        "command_id": command_id,
        "source_event_id": str(uuid4()),
        "scope_version": 1,
        "crawl_policy_version": 1,
        **overrides,
    }
    return CrawlSiteWorkflowInput(**values)


def manifest(**overrides):
    values = {
        "schema_version": 1,
        "manifest_id": str(uuid4()),
        "manifest_sha256": "a" * 64,
        "coverage": "complete",
        "discovered_count": 4,
        "terminal_count": 4,
        "scope_version": 1,
        "crawl_policy_version": 1,
        **overrides,
    }
    return CrawlManifestReference(**values)


def terminal(command=None, **overrides):
    command = command or workflow_input()
    values = {
        "schema_version": 1,
        "tenant_id": command.tenant_id,
        "site_id": command.site_id,
        "command_id": command.command_id,
        "workflow_id": f"signal:CrawlSite:{command.tenant_id}:{command.command_id}",
        "first_run_id": str(uuid4()),
        "state": "succeeded",
        "result_reference": manifest(),
        "reason": None,
        **overrides,
    }
    return CrawlTerminalInput(**values)


def projection(item=None, **overrides):
    item = item or terminal()
    values = {
        "schema_version": 1,
        "tenant_id": item.tenant_id,
        "site_id": item.site_id,
        "progress_event_id": str(uuid4()),
        "command_id": item.command_id,
        "workflow_id": item.workflow_id,
        "first_run_id": item.first_run_id,
        "command_status": item.state,
        "workflow_state": item.state,
        "result_reference": item.result_reference,
        "reason": item.reason,
        "duplicate": False,
        **overrides,
    }
    return CrawlTerminalProjection(**values)


@pytest.mark.parametrize(
    ("validator", "valid", "invalid"),
    [
        (validate_crawl_workflow_input, workflow_input(), workflow_input(schema_version=2)),
        (
            validate_crawl_manifest_reference,
            manifest(),
            manifest(terminal_count=5),
        ),
        (
            validate_crawl_heartbeat,
            CrawlHeartbeat(1, "crawling", 3, 2),
            CrawlHeartbeat(1, "secret-phase", 0, 0),
        ),
        (
            validate_crawl_terminal_input,
            terminal(),
            terminal(state="failed", result_reference=None, reason="provider-secret"),
        ),
        (
            validate_crawl_terminal_projection,
            projection(),
            projection(command_status="processing"),
        ),
    ],
)
def test_workflow_contracts_are_closed_and_bounded(validator, valid, invalid):
    assert validator(valid) == valid
    with pytest.raises(ValueError):
        validator(invalid)


class SuccessfulExecutor:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def execute(self, command, *, heartbeat):
        self.calls.append(command)
        heartbeat(CrawlHeartbeat(1, "crawling", 4, 2))
        return self.result


def test_crawl_activity_validates_versions_and_emits_bounded_heartbeats():
    command = workflow_input()
    result = manifest()
    executor = SuccessfulExecutor(result)
    activity_adapter = CrawlSiteActivities(executor)
    environment = ActivityEnvironment()
    heartbeats = []
    environment.on_heartbeat = heartbeats.append

    actual = asyncio.run(environment.run(activity_adapter.execute, command))

    assert actual == result
    assert executor.calls == [command]
    assert [heartbeat.phase for heartbeat in heartbeats] == [
        "starting",
        "crawling",
        "completed",
    ]
    assert heartbeats[-1].terminal_count == result.terminal_count


@pytest.mark.parametrize(
    ("failure", "error_type", "non_retryable"),
    [
        (CrawlExecutionRetryable("private"), "crawl_retryable", False),
        (CrawlExecutionRejected("private"), "crawl_rejected", True),
        (RuntimeError("provider-secret"), "crawl_internal", True),
    ],
)
def test_crawl_activity_sanitizes_classified_and_unknown_failures(
    failure, error_type, non_retryable
):
    class FailingExecutor:
        async def execute(self, command, *, heartbeat):
            raise failure

    environment = ActivityEnvironment()
    adapter = CrawlSiteActivities(FailingExecutor())

    with pytest.raises(ApplicationError) as captured:
        asyncio.run(environment.run(adapter.execute, workflow_input()))

    assert captured.value.type == error_type
    assert captured.value.non_retryable is non_retryable
    assert "private" not in str(captured.value)
    assert "secret" not in str(captured.value)


def test_crawl_activity_rejects_invalid_result_and_version_mismatch():
    for result in ("invalid", manifest(scope_version=2)):
        environment = ActivityEnvironment()
        adapter = CrawlSiteActivities(SuccessfulExecutor(result))
        with pytest.raises(ApplicationError) as captured:
            asyncio.run(environment.run(adapter.execute, workflow_input()))
        assert captured.value.type == "invalid_crawl_result"
        assert captured.value.non_retryable is True


def test_invalid_crawl_input_is_rejected_before_executor_access():
    executor = SuccessfulExecutor(manifest())
    environment = ActivityEnvironment()
    with pytest.raises(ApplicationError) as crawl_failure:
        asyncio.run(
            environment.run(
                CrawlSiteActivities(executor).execute,
                workflow_input(scope_version=0),
            )
        )
    assert crawl_failure.value.type == "crawl_rejected"
    assert executor.calls == []


class FakeTerminalStore:
    def __init__(self, result=None, failure=None):
        self.result = result
        self.failure = failure
        self.calls = []

    def record(self, item):
        self.calls.append(item)
        if self.failure is not None:
            raise self.failure
        return self.result


def test_invalid_terminal_input_is_rejected_before_store_access():
    store = FakeTerminalStore(result=None)
    invalid_terminal = terminal(
        state="failed",
        result_reference=None,
        reason="crawl_cancelled",
    )
    with pytest.raises(ApplicationError) as terminal_failure:
        asyncio.run(
            ActivityEnvironment().run(
                WorkflowTerminalActivities(store).record,
                invalid_terminal,
            )
        )
    assert terminal_failure.value.type == "terminal_projection_rejected"
    assert store.calls == []


def test_terminal_activity_returns_only_validated_projection():
    item = terminal()
    expected = projection(item)
    store = FakeTerminalStore(expected)
    environment = ActivityEnvironment()

    actual = asyncio.run(environment.run(WorkflowTerminalActivities(store).record, item))

    assert actual == expected
    assert store.calls == [item]


@pytest.mark.parametrize(
    ("failure", "error_type", "non_retryable"),
    [
        (WorkflowTerminalUnavailable("dsn-secret"), "terminal_projection_unavailable", False),
        (WorkflowTerminalConflict("dsn-secret"), "terminal_projection_conflict", True),
        (RuntimeError("dsn-secret"), "terminal_projection_unavailable", False),
    ],
)
def test_terminal_activity_classifies_and_sanitizes_storage_failures(
    failure, error_type, non_retryable
):
    environment = ActivityEnvironment()
    adapter = WorkflowTerminalActivities(FakeTerminalStore(failure=failure))

    with pytest.raises(ApplicationError) as captured:
        asyncio.run(environment.run(adapter.record, terminal()))

    assert captured.value.type == error_type
    assert captured.value.non_retryable is non_retryable
    assert "secret" not in str(captured.value)


def test_terminal_activity_rejects_invalid_store_result():
    environment = ActivityEnvironment()
    adapter = WorkflowTerminalActivities(FakeTerminalStore(result="invalid"))
    with pytest.raises(ApplicationError) as captured:
        asyncio.run(environment.run(adapter.record, terminal()))
    assert captured.value.type == "terminal_projection_invalid"
    assert captured.value.non_retryable is True


def test_activity_adapters_require_ports_and_postgres_store_uses_fresh_context(monkeypatch):
    with pytest.raises(ValueError, match="executor"):
        CrawlSiteActivities(object())
    with pytest.raises(ValueError, match="store"):
        WorkflowTerminalActivities(object())
    with pytest.raises(ValueError, match="factory"):
        PostgresTerminalStore(object())

    opened = []

    @contextmanager
    def factory():
        connection = object()
        opened.append(connection)
        yield connection

    item = terminal()
    expected = projection(item)
    monkeypatch.setattr(
        "signal_core.crawl_workflow_activities.record_crawl_terminal",
        lambda connection, terminal: expected,
    )
    store = PostgresTerminalStore(factory)

    assert store.record(item) == expected
    assert len(opened) == 1


def test_failed_and_cancelled_terminal_contracts_require_exact_reason():
    item = terminal()
    for state, reason in [
        ("failed", "crawl_activity_failed"),
        ("cancelled", "crawl_cancelled"),
    ]:
        valid = replace(item, state=state, result_reference=None, reason=reason)
        assert validate_crawl_terminal_input(valid) == valid
        with pytest.raises(ValueError):
            validate_crawl_terminal_input(replace(valid, reason=None))

        swapped_reason = "crawl_cancelled" if state == "failed" else "crawl_activity_failed"
        with pytest.raises(ValueError):
            validate_crawl_terminal_input(replace(valid, reason=swapped_reason))
        with pytest.raises(ValueError):
            validate_crawl_terminal_projection(projection(valid, reason=swapped_reason))
