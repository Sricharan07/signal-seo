"""Configured runtime identity and bounded maintenance, without provider calls."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from signal_core.weekly_delivery_runtime import WeeklyDeliveryRuntime
from signal_core.weekly_loop import WeeklySite, WeeklyStageResult
from temporalio.exceptions import WorkflowAlreadyStartedError


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_duplicate_qualification_attaches_with_typed_stage_results(monkeypatch):
    site = WeeklySite(str(uuid4()), str(uuid4()), str(uuid4()))
    result = (WeeklyStageResult("completed", "CYCLE_REPORTED"),)
    temporal = Mock()
    temporal.start_workflow = AsyncMock(
        side_effect=WorkflowAlreadyStartedError("existing", "WeeklySiteLoop")
    )
    temporal.get_workflow_handle.return_value.result = AsyncMock(return_value=result)

    @asynccontextmanager
    async def worker(self):
        yield

    monkeypatch.setattr(WeeklyDeliveryRuntime, "worker", worker)
    runtime = WeeklyDeliveryRuntime(temporal, site, Mock(), Mock())
    assert await runtime.qualify_once() == result
    assert temporal.get_workflow_handle.call_args.kwargs == {
        "result_type": tuple[WeeklyStageResult, ...]
    }
    assert (
        temporal.get_workflow_handle.call_args.args[0]
        == (temporal.start_workflow.call_args.kwargs["id"])
    )


@pytest.mark.anyio
async def test_runtime_rejects_unconfigured_delivery_and_unbounded_cadence():
    activities = Mock(delivery=None)
    runtime = WeeklyDeliveryRuntime(Mock(), Mock(), activities, Mock())
    with pytest.raises(ValueError, match="configured explicitly"):
        runtime.worker()
    for interval in (0, 29, 301):
        with pytest.raises(ValueError, match="Bounded"):
            await runtime.serve(asyncio.Event(), interval_seconds=interval)


@pytest.mark.anyio
async def test_measurement_reconciliation_preserves_change_horizon_identity(monkeypatch):
    from datetime import UTC, datetime

    from signal_core.change_measurement import ChangeMeasurementJob

    site = WeeklySite(str(uuid4()), str(uuid4()), str(uuid4()))
    operation = str(uuid4())
    jobs = tuple(
        ChangeMeasurementJob(
            site.tenant_id, site.site_id, operation, h, datetime.now(UTC).isoformat()
        )
        for h in (7, 28, 90)
    )
    activities = Mock()
    activities.jobs.return_value = jobs
    monkeypatch.setattr(WeeklyDeliveryRuntime, "measurements", lambda self: activities)
    temporal = Mock()
    temporal.start_workflow = AsyncMock(
        side_effect=WorkflowAlreadyStartedError("existing", "ChangeMeasurementWorkflow")
    )
    runtime = WeeklyDeliveryRuntime(temporal, site, Mock(), Mock())
    await runtime.reconcile_measurements()
    await runtime.reconcile_measurements()
    assert temporal.start_workflow.call_count == 6
    assert [c.kwargs["id"] for c in temporal.start_workflow.call_args_list[:3]] == [
        job.workflow_id for job in jobs
    ]


def test_measurement_identity_is_tenant_and_site_bound():
    from dataclasses import replace
    from datetime import UTC, datetime

    from signal_core.change_measurement import ChangeMeasurementJob

    job = ChangeMeasurementJob(
        str(uuid4()), str(uuid4()), str(uuid4()), 7, datetime.now(UTC).isoformat()
    )
    assert replace(job).workflow_id == job.workflow_id
    assert replace(job, tenant_id=str(uuid4())).workflow_id != job.workflow_id
    assert replace(job, site_id=str(uuid4())).workflow_id != job.workflow_id
    assert replace(job, horizon=28).workflow_id != job.workflow_id
