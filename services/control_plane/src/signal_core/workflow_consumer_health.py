"""Closed operational health contract for the workflow command consumer."""

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from signal_core.outbox_worker import DeliveryCycleReport

HealthProbe = Literal["live", "ready"]
HealthStatus = Literal["live", "ready", "not_ready"]
HealthPhase = Literal["starting", "running", "draining", "stopped"]
HealthReason = Literal[
    "live",
    "starting",
    "awaiting_database",
    "dependency_unavailable",
    "stale",
    "ready",
    "draining",
    "stopped",
]


class WorkflowConsumerHealthError(ValueError):
    """Health state or transport input violates the closed process contract."""


@dataclass(frozen=True)
class WorkflowConsumerHealthSnapshot:
    schema_version: int
    service: str
    release: str
    probe: HealthProbe
    status: HealthStatus
    phase: HealthPhase
    ready: bool
    reason: HealthReason
    completed_cycles: int
    consecutive_failed_cycles: int

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "service": self.service,
            "release": self.release,
            "probe": self.probe,
            "status": self.status,
            "phase": self.phase,
            "ready": self.ready,
            "reason": self.reason,
            "completed_cycles": self.completed_cycles,
            "consecutive_failed_cycles": self.consecutive_failed_cycles,
        }


class WorkflowConsumerHealth:
    """Track process life and recent dependency-backed delivery cycles."""

    def __init__(
        self,
        *,
        release: str,
        stale_after_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.release = validate_workflow_consumer_release(release)
        self.stale_after_seconds = _bounded_seconds(stale_after_seconds)
        if not callable(clock):
            raise WorkflowConsumerHealthError("A monotonic health clock is required.")
        self._clock = clock
        self._phase: HealthPhase = "starting"
        self._completed_cycles = 0
        self._consecutive_failed_cycles = 0
        self._last_success: float | None = None
        self._last_cycle_healthy = False

    @property
    def phase(self) -> str:
        return self._phase

    def mark_running(self) -> None:
        if self._phase != "starting":
            raise WorkflowConsumerHealthError("Consumer can run only from starting state.")
        self._phase = "running"

    def mark_draining(self) -> bool:
        if self._phase in {"draining", "stopped"}:
            return False
        self._phase = "draining"
        return True

    def mark_stopped(self) -> None:
        self._phase = "stopped"

    def record_cycle(self, report: DeliveryCycleReport) -> None:
        if self._phase != "running" or not isinstance(report, DeliveryCycleReport):
            raise WorkflowConsumerHealthError("A running consumer cycle is required.")
        values = (
            report.active_tenants,
            report.claimed,
            report.delivered,
            report.rescheduled,
            report.ambiguous,
            report.lease_lost,
            report.storage_failures,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values
        ):
            raise WorkflowConsumerHealthError("Consumer cycle counters are invalid.")
        self._completed_cycles += 1
        self._last_cycle_healthy = report.storage_failures == 0 and report.ambiguous == 0
        if self._last_cycle_healthy:
            self._last_success = self._clock()
            self._consecutive_failed_cycles = 0
        else:
            self._consecutive_failed_cycles += 1

    def snapshot(self, probe: HealthProbe) -> WorkflowConsumerHealthSnapshot:
        if probe not in {"live", "ready"}:
            raise WorkflowConsumerHealthError("Unsupported health probe.")
        if probe == "live":
            live = self._phase != "stopped"
            return self._snapshot(
                probe="live",
                status="live" if live else "not_ready",
                ready=False,
                reason="live" if live else "stopped",
            )

        ready, reason = self._readiness()
        return self._snapshot(
            probe="ready",
            status="ready" if ready else "not_ready",
            ready=ready,
            reason=reason,
        )

    def _readiness(self) -> tuple[bool, HealthReason]:
        if self._phase == "starting":
            return False, "starting"
        if self._phase == "draining":
            return False, "draining"
        if self._phase == "stopped":
            return False, "stopped"
        if self._completed_cycles == 0:
            return False, "awaiting_database"
        if not self._last_cycle_healthy:
            return False, "dependency_unavailable"
        if (
            self._last_success is None
            or self._clock() - self._last_success > self.stale_after_seconds
        ):
            return False, "stale"
        return True, "ready"

    def _snapshot(
        self,
        *,
        probe: HealthProbe,
        status: HealthStatus,
        ready: bool,
        reason: HealthReason,
    ) -> WorkflowConsumerHealthSnapshot:
        return WorkflowConsumerHealthSnapshot(
            schema_version=1,
            service="workflow_command_consumer",
            release=self.release,
            probe=probe,
            status=status,
            phase=self._phase,
            ready=ready,
            reason=reason,
            completed_cycles=self._completed_cycles,
            consecutive_failed_cycles=self._consecutive_failed_cycles,
        )


class WorkflowConsumerHealthServer:
    """Serve bounded local HTTP probes without exposing process configuration."""

    def __init__(self, health: WorkflowConsumerHealth, *, host: str, port: int) -> None:
        if not isinstance(health, WorkflowConsumerHealth):
            raise WorkflowConsumerHealthError("A workflow consumer health state is required.")
        if host not in {"127.0.0.1", "::1"}:
            raise WorkflowConsumerHealthError("Health probes must bind to literal loopback.")
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise WorkflowConsumerHealthError("Health probe port is invalid.")
        self._health = health
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> "WorkflowConsumerHealthServer":
        self._server = await asyncio.start_server(
            self._handle,
            host=self._host,
            port=self._port,
            limit=_MAX_REQUEST_BYTES,
        )
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"),
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            status, body, allow = self._response(request)
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            status, body, allow = 400, {"status": "bad_request"}, None
        except Exception:
            status, body, allow = 500, {"status": "unavailable"}, None
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("ascii")
        reason = {
            200: "OK",
            400: "Bad Request",
            404: "Not Found",
            405: "Method Not Allowed",
            500: "Internal Server Error",
            503: "Service Unavailable",
        }[status]
        headers = [
            f"HTTP/1.1 {status} {reason}",
            "Content-Type: application/json",
            f"Content-Length: {len(encoded)}",
            "Cache-Control: no-store",
            "Connection: close",
        ]
        if allow is not None:
            headers.append(f"Allow: {allow}")
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + encoded)
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def _response(self, request: bytes) -> tuple[int, dict[str, object], str | None]:
        if len(request) > _MAX_REQUEST_BYTES or b"\x00" in request:
            return 400, {"status": "bad_request"}, None
        lines = request.split(b"\r\n")
        if not lines or len(lines[0]) > _MAX_REQUEST_LINE_BYTES:
            return 400, {"status": "bad_request"}, None
        try:
            method, target, version = lines[0].decode("ascii").split(" ")
        except (UnicodeDecodeError, ValueError):
            return 400, {"status": "bad_request"}, None
        if version not in {"HTTP/1.0", "HTTP/1.1"}:
            return 400, {"status": "bad_request"}, None
        if method != "GET":
            return 405, {"status": "method_not_allowed"}, "GET"
        probes: dict[str, HealthProbe] = {"/health/live": "live", "/health/ready": "ready"}
        probe = probes.get(target)
        if probe is None:
            return 404, {"status": "not_found"}, None
        snapshot = self._health.snapshot(probe)
        status = 200 if snapshot.status in {"live", "ready"} else 503
        return status, snapshot.as_dict(), None


def validate_workflow_consumer_release(value: object) -> str:
    if not isinstance(value, str) or _RELEASE.fullmatch(value) is None:
        raise WorkflowConsumerHealthError("Workflow consumer release identity is invalid.")
    return value


def _bounded_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkflowConsumerHealthError("Health staleness interval is invalid.")
    converted = float(value)
    if not 5.0 <= converted <= 300.0:
        raise WorkflowConsumerHealthError("Health staleness interval is invalid.")
    return converted


_RELEASE = re.compile(r"sha256:[a-f0-9]{64}")
_MAX_REQUEST_BYTES = 8192
_MAX_REQUEST_LINE_BYTES = 2048
_REQUEST_TIMEOUT_SECONDS = 1.0
