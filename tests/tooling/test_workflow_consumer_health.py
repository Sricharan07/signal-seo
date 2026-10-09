import asyncio
import json
import socket
from dataclasses import replace

import pytest
from signal_core.outbox_worker import DeliveryCycleReport
from signal_core.workflow_consumer_health import (
    WorkflowConsumerHealth,
    WorkflowConsumerHealthError,
    WorkflowConsumerHealthServer,
    validate_workflow_consumer_release,
)

RELEASE = "sha256:" + "a" * 64


def test_health_requires_immutable_release_and_bounded_staleness():
    assert validate_workflow_consumer_release(RELEASE) == RELEASE
    for value in ["a" * 64, "sha256:" + "A" * 64, "sha256:" + "a" * 63, object()]:
        with pytest.raises(WorkflowConsumerHealthError, match="release"):
            validate_workflow_consumer_release(value)
    for value in [True, 4.9, 301, "15"]:
        with pytest.raises(WorkflowConsumerHealthError, match="staleness"):
            WorkflowConsumerHealth(release=RELEASE, stale_after_seconds=value)


def test_readiness_needs_recent_success_and_closes_before_drain():
    now = [100.0]
    health = WorkflowConsumerHealth(
        release=RELEASE,
        stale_after_seconds=10,
        clock=lambda: now[0],
    )

    assert health.snapshot("live").status == "live"
    assert health.snapshot("ready").reason == "starting"
    health.mark_running()
    assert health.snapshot("ready").reason == "awaiting_database"

    health.record_cycle(DeliveryCycleReport())
    snapshot = health.snapshot("ready")
    assert snapshot.status == "ready"
    assert snapshot.ready is True
    assert snapshot.completed_cycles == 1
    assert snapshot.consecutive_failed_cycles == 0

    now[0] += 11
    assert health.snapshot("ready").reason == "stale"
    health.record_cycle(DeliveryCycleReport(storage_failures=1))
    assert health.snapshot("ready").reason == "dependency_unavailable"
    assert health.snapshot("ready").consecutive_failed_cycles == 1
    health.record_cycle(DeliveryCycleReport())
    assert health.snapshot("ready").status == "ready"

    assert health.mark_draining() is True
    assert health.mark_draining() is False
    assert health.snapshot("ready").reason == "draining"
    assert health.snapshot("live").status == "live"
    health.mark_stopped()
    assert health.snapshot("live").status == "not_ready"
    assert health.snapshot("ready").reason == "stopped"


def test_health_rejects_impossible_transitions_and_cycle_shapes():
    health = WorkflowConsumerHealth(release=RELEASE, stale_after_seconds=15)
    with pytest.raises(WorkflowConsumerHealthError, match="running"):
        health.record_cycle(DeliveryCycleReport())
    health.mark_running()
    with pytest.raises(WorkflowConsumerHealthError, match="starting"):
        health.mark_running()
    with pytest.raises(WorkflowConsumerHealthError, match="counters"):
        health.record_cycle(replace(DeliveryCycleReport(), claimed=-1))
    with pytest.raises(WorkflowConsumerHealthError, match="probe"):
        health.snapshot("unknown")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def _request(port: int, request: bytes) -> tuple[int, dict[str, object], bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, body = response.split(b"\r\n\r\n", 1)
    status = int(head.split(b" ", 2)[1])
    return status, json.loads(body), head


def test_local_http_health_contract_covers_ready_denial_and_closed_routes():
    async def exercise():
        health = WorkflowConsumerHealth(release=RELEASE, stale_after_seconds=15)
        port = _free_port()
        async with WorkflowConsumerHealthServer(health, host="127.0.0.1", port=port):
            live = await _request(port, b"GET /health/live HTTP/1.1\r\nHost: localhost\r\n\r\n")
            pending = await _request(port, b"GET /health/ready HTTP/1.1\r\nHost: localhost\r\n\r\n")
            method = await _request(port, b"POST /health/ready HTTP/1.1\r\nHost: localhost\r\n\r\n")
            missing = await _request(port, b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n")
            malformed = await _request(port, b"not-http\r\n\r\n")
            health.mark_running()
            health.record_cycle(DeliveryCycleReport())
            ready = await _request(port, b"GET /health/ready HTTP/1.1\r\nHost: localhost\r\n\r\n")
            health.mark_stopped()
            stopped_live = await _request(
                port, b"GET /health/live HTTP/1.1\r\nHost: localhost\r\n\r\n"
            )
        return live, pending, method, missing, malformed, ready, stopped_live

    live, pending, method, missing, malformed, ready, stopped_live = asyncio.run(exercise())
    assert live[0] == 200
    assert live[1]["status"] == "live"
    assert pending[0] == 503
    assert pending[1]["reason"] == "starting"
    assert method[0] == 405
    assert b"Allow: GET" in method[2]
    assert missing[:2] == (404, {"status": "not_found"})
    assert malformed[:2] == (400, {"status": "bad_request"})
    assert ready[0] == 200
    assert ready[1] == {
        "schema_version": 1,
        "service": "workflow_command_consumer",
        "release": RELEASE,
        "probe": "ready",
        "status": "ready",
        "phase": "running",
        "ready": True,
        "reason": "ready",
        "completed_cycles": 1,
        "consecutive_failed_cycles": 0,
    }
    assert b"Cache-Control: no-store" in ready[2]
    assert stopped_live[0] == 503
    assert stopped_live[1]["reason"] == "stopped"


def test_local_http_health_bounds_oversized_and_slow_requests():
    async def exercise():
        health = WorkflowConsumerHealth(release=RELEASE, stale_after_seconds=15)
        port = _free_port()
        async with WorkflowConsumerHealthServer(health, host="127.0.0.1", port=port):
            oversized = await _request(
                port,
                b"GET /health/live HTTP/1.1\r\nX-Fill: " + b"x" * 9000 + b"\r\n\r\n",
            )
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            slow_response = await reader.read()
            writer.close()
            await writer.wait_closed()
        return oversized, slow_response

    oversized, slow_response = asyncio.run(exercise())
    assert oversized[:2] == (400, {"status": "bad_request"})
    assert slow_response.startswith(b"HTTP/1.1 400 Bad Request\r\n")


@pytest.mark.parametrize(
    ("host", "port"),
    [("0.0.0.0", 8081), ("localhost", 8081), ("127.0.0.1", 80), ("127.0.0.1", True)],
)
def test_health_server_rejects_public_or_privileged_bindings(host, port):
    health = WorkflowConsumerHealth(release=RELEASE, stale_after_seconds=15)
    with pytest.raises(WorkflowConsumerHealthError):
        WorkflowConsumerHealthServer(health, host=host, port=port)
