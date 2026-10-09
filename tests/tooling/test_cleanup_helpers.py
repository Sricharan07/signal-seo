from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from signal_core.weekly_schedule import upsert_or_pause_schedule
from temporalio.service import RPCError, RPCStatusCode

from tests.temporal_runtime import start_temporal


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_schedule_upsert_pause_and_provider_failures():
    handle = SimpleNamespace(pause=AsyncMock(), update=AsyncMock())
    temporal = SimpleNamespace(get_schedule_handle=lambda _: handle, create_schedule=AsyncMock())
    schedule = object()
    assert (
        await upsert_or_pause_schedule(
            temporal, "synthetic-schedule", schedule, pause_note="Synthetic pause"
        )
        == "created"
    )
    temporal.create_schedule.side_effect = RPCError(
        "synthetic-existing", RPCStatusCode.ALREADY_EXISTS, b""
    )
    assert (
        await upsert_or_pause_schedule(
            temporal, "synthetic-schedule", schedule, pause_note="Synthetic pause"
        )
        == "updated"
    )
    assert handle.update.call_args.args[0](None).schedule is schedule
    handle.pause.side_effect = RPCError("synthetic-missing", RPCStatusCode.NOT_FOUND, b"")
    assert (
        await upsert_or_pause_schedule(
            temporal, "synthetic-schedule", None, pause_note="Synthetic pause"
        )
        == "paused"
    )
    for method in (handle.pause, temporal.create_schedule):
        method.side_effect = RPCError("synthetic-provider-failure", RPCStatusCode.UNAVAILABLE, b"")
    with pytest.raises(RPCError):
        await upsert_or_pause_schedule(
            temporal, "synthetic-schedule", None, pause_note="Synthetic pause"
        )
    with pytest.raises(RPCError):
        await upsert_or_pause_schedule(
            temporal, "synthetic-schedule", schedule, pause_note="Synthetic pause"
        )


@pytest.mark.anyio
async def test_temporal_startup_retries_are_bounded_and_do_not_retry_other_failures(monkeypatch):
    from tests import temporal_runtime

    start = AsyncMock(
        side_effect=[RuntimeError("Failed to start within 5 seconds"), "synthetic-started"]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(temporal_runtime.WorkflowEnvironment, "start_local", start)
    monkeypatch.setattr(temporal_runtime.asyncio, "sleep", sleep)
    assert await start_temporal(namespace="synthetic-namespace") == "synthetic-started"
    assert start.call_count == 2 and sleep.call_count == 1
    assert start.call_args.kwargs == {"namespace": "synthetic-namespace"}
    start.reset_mock(side_effect=True)
    start.side_effect = RuntimeError("Failed to start within 5 seconds")
    with pytest.raises(RuntimeError):
        await start_temporal()
    assert start.call_count == 3
    start.reset_mock(side_effect=True)
    start.side_effect = RuntimeError("synthetic-action-failure")
    with pytest.raises(RuntimeError):
        await start_temporal()
    assert start.call_count == 1
