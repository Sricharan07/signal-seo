import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from signal_core.workflow_admission import WorkflowAdmission
from signal_core.workflow_contracts import CrawlSiteWorkflowInput
from signal_core.workflow_start import (
    TemporalWorkflowStartConfig,
    TemporalWorkflowStarter,
    WorkflowStartOutcomeUnknown,
    WorkflowStartReceipt,
    record_workflow_started,
)
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError


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


class StubTemporalClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def start_workflow(self, workflow, arg, **kwargs):
        self.calls.append((workflow, arg, kwargs))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def test_starter_sends_only_bounded_identity_input_with_non_reuse_policies():
    admitted = admission()
    run_id = str(uuid4())
    client = StubTemporalClient(SimpleNamespace(first_execution_run_id=run_id))
    starter = TemporalWorkflowStarter(
        client,
        config=TemporalWorkflowStartConfig(
            task_queue="signal.crawl.v1",
            rpc_timeout_seconds=2.5,
        ),
    )

    receipt = asyncio.run(starter.start(admitted))

    assert receipt == WorkflowStartReceipt(
        workflow_id=admitted.workflow_id,
        first_run_id=run_id,
        evidence_kind="start_acknowledged",
    )
    assert client.calls == [
        (
            "CrawlSite",
            CrawlSiteWorkflowInput(
                schema_version=1,
                tenant_id=str(admitted.tenant_id),
                site_id=str(admitted.site_id),
                command_id=str(admitted.command_id),
                source_event_id=str(admitted.source_event_id),
            ),
            {
                "id": admitted.workflow_id,
                "task_queue": "signal.crawl.v1",
                "id_reuse_policy": WorkflowIDReusePolicy.REJECT_DUPLICATE,
                "id_conflict_policy": WorkflowIDConflictPolicy.USE_EXISTING,
                "rpc_timeout": timedelta(seconds=2.5),
            },
        )
    ]


def test_starter_accepts_exact_already_started_run_as_positive_existence_evidence():
    admitted = admission()
    run_id = str(uuid4())
    error = WorkflowAlreadyStartedError(
        admitted.workflow_id,
        admitted.workflow_type,
        run_id=run_id,
    )
    client = StubTemporalClient(error)

    assert asyncio.run(TemporalWorkflowStarter(client).start(admitted)) == WorkflowStartReceipt(
        workflow_id=admitted.workflow_id,
        first_run_id=run_id,
        evidence_kind="already_started",
    )


@pytest.mark.parametrize(
    "outcome",
    [
        RuntimeError("provider detail must not cross the boundary"),
        TimeoutError(),
        SimpleNamespace(first_execution_run_id=None),
        SimpleNamespace(first_execution_run_id="not-a-run-id"),
        SimpleNamespace(first_execution_run_id=str(uuid4()).upper()),
    ],
)
def test_starter_collapses_unprovable_outcomes_without_exception_details(outcome):
    client = StubTemporalClient(outcome)

    with pytest.raises(WorkflowStartOutcomeUnknown) as captured:
        asyncio.run(TemporalWorkflowStarter(client).start(admission()))

    assert str(captured.value) == ""


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda value: WorkflowAlreadyStartedError(
            f"{value.workflow_id}-other", value.workflow_type, run_id=str(uuid4())
        ),
        lambda value: WorkflowAlreadyStartedError(
            value.workflow_id, "OtherWorkflow", run_id=str(uuid4())
        ),
        lambda value: WorkflowAlreadyStartedError(
            value.workflow_id, value.workflow_type, run_id=None
        ),
    ],
)
def test_starter_rejects_mismatched_or_incomplete_already_started_evidence(error_factory):
    admitted = admission()
    client = StubTemporalClient(error_factory(admitted))

    with pytest.raises(WorkflowStartOutcomeUnknown):
        asyncio.run(TemporalWorkflowStarter(client).start(admitted))


def test_starter_does_not_swallow_cooperative_cancellation():
    client = StubTemporalClient(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(TemporalWorkflowStarter(client).start(admission()))


@pytest.mark.parametrize(
    "candidate",
    [
        object(),
        replace(admission(), state="running"),
        replace(admission(), workflow_type="OtherWorkflow"),
        replace(admission(), workflow_id="signal:CrawlSite:invalid"),
    ],
)
def test_starter_rejects_invalid_admission_before_call(candidate):
    client = StubTemporalClient(SimpleNamespace(first_execution_run_id=str(uuid4())))

    with pytest.raises(ValueError, match="admission"):
        asyncio.run(TemporalWorkflowStarter(client).start(candidate))

    assert client.calls == []


@pytest.mark.parametrize(
    "config",
    [
        {"task_queue": ""},
        {"task_queue": "Signal.Crawl"},
        {"task_queue": "a" * 65},
        {"rpc_timeout_seconds": True},
        {"rpc_timeout_seconds": 0.09},
        {"rpc_timeout_seconds": 60.01},
    ],
)
def test_start_configuration_is_closed_and_bounded(config):
    with pytest.raises(ValueError):
        TemporalWorkflowStartConfig(**config)


def test_starter_requires_callable_client():
    with pytest.raises(ValueError, match="client"):
        TemporalWorkflowStarter(object())


@pytest.mark.parametrize(
    "receipt_factory",
    [
        lambda value: WorkflowStartReceipt(
            workflow_id=f"{value.workflow_id}-other",
            first_run_id=str(uuid4()),
            evidence_kind="start_acknowledged",
        ),
        lambda value: WorkflowStartReceipt(
            workflow_id=value.workflow_id,
            first_run_id="not-a-run-id",
            evidence_kind="start_acknowledged",
        ),
        lambda value: WorkflowStartReceipt(
            workflow_id=value.workflow_id,
            first_run_id=str(uuid4()),
            evidence_kind="unverified",
        ),
    ],
)
def test_recording_rejects_bad_evidence_before_database_access(receipt_factory):
    admitted = admission()
    with pytest.raises(ValueError, match="evidence"):
        record_workflow_started(object(), admitted, receipt_factory(admitted))
