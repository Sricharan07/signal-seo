"""Read-only probes for already-configured private dependencies and screened pins."""

import asyncio
import json
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from temporalio.client import WorkflowExecutionStatus

from signal_core.crawl_urls import validate_public_addresses
from signal_core.openbao_http import json_document, request


async def budget_health(reader) -> dict:
    """Read an existing ledger/cap port; this cannot set or enlarge a budget."""
    usage, cap = await reader()
    if type(usage) is not int or type(cap) is not int or min(usage, cap) < 0:
        raise ValueError("Budget observation unavailable.")
    if cap == 0:
        return {"condition": "budget_exhausted"}
    return {"value": usage, "warning": cap * 0.8, "critical": cap}


def compose_health_monitor(
    *,
    store,
    scope,
    recovery_source,
    temporal,
    measurements,
    openbao_authority,
    disk_path,
    pin_paths=(),
    private_reader=None,
    model_budget=None,
    assistant_budget=None,
    openbao_options=None,
    chat_alerts=None,
):
    from signal_core.health_monitoring import HealthMonitor

    probes = {
        "disk": lambda: disk_health(disk_path),
        "openbao": lambda: openbao_health(openbao_authority, **(openbao_options or {})),
    }
    if temporal is not None:
        probes["temporal"] = lambda: temporal_health(temporal)
        probes["weekly_schedule"] = lambda: weekly_health(temporal, scope)
        if measurements is not None:
            probes["measurement_schedule"] = lambda: measurement_health(
                temporal, measurements, scope
            )
    if pin_paths and private_reader is not None:
        probes["egress_pins"] = ScreenedPinHealth(tuple(pin_paths), private_reader)
    if model_budget is not None:
        probes["budget_model"] = lambda: budget_health(model_budget)
    if assistant_budget is not None:
        probes["budget_assistants"] = lambda: budget_health(assistant_budget)
    return HealthMonitor(store, scope, recovery_source, probes, chat_alerts)


async def temporal_health(client) -> dict:
    started = time.monotonic()
    healthy = await client.service_client.check_health(timeout=timedelta(seconds=3))
    if healthy and time.monotonic() - started >= 1:
        return {"value": time.monotonic() - started, "warning": 1, "critical": 3}
    return {"condition": "reachable" if healthy else "unreachable"}


async def weekly_health(client, scope, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    description = await client.get_schedule_handle(
        f"signal:weekly:{scope.tenant_id}:{scope.site_id}"
    ).describe()
    if description.schedule.state.paused:
        return {"condition": "paused"}
    oldest_running = 0.0
    for running in description.info.running_actions:
        execution = await client.get_workflow_handle(running.workflow_id).describe()
        if now - execution.start_time > timedelta(days=1):
            return {"condition": "stuck"}
        oldest_running = max(oldest_running, (now - execution.start_time).total_seconds())
    if description.info.recent_actions:
        recent = description.info.recent_actions[-1]
        execution = await client.get_workflow_handle(recent.action.workflow_id).describe()
        if execution.status in {
            WorkflowExecutionStatus.FAILED,
            WorkflowExecutionStatus.TIMED_OUT,
            WorkflowExecutionStatus.TERMINATED,
            WorkflowExecutionStatus.CANCELED,
        }:
            return {"condition": "stuck"}
    last = max(
        (a.started_at for a in description.info.recent_actions), default=description.info.created_at
    )
    if not description.info.next_action_times or now - last > timedelta(days=8):
        return {"condition": "paused"}
    if oldest_running >= 3600:
        return {"value": oldest_running, "warning": 3600, "critical": 86400}
    return {"condition": "healthy"}


async def measurement_health(client, activities, scope, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    jobs = await asyncio.to_thread(activities.jobs, str(scope.tenant_id), str(scope.site_id))
    if len(jobs) > 100:
        return {"condition": "stuck"}
    overdue = False
    for job in jobs:
        due = datetime.fromisoformat(job.due_at)
        execution = await client.get_workflow_handle(job.workflow_id).describe()
        if execution.status in {
            WorkflowExecutionStatus.FAILED,
            WorkflowExecutionStatus.TIMED_OUT,
            WorkflowExecutionStatus.TERMINATED,
            WorkflowExecutionStatus.CANCELED,
        }:
            return {"condition": "stuck"}
        if execution.status == WorkflowExecutionStatus.RUNNING and now - due > timedelta(days=2):
            return {"condition": "stuck"}
        if execution.status == WorkflowExecutionStatus.RUNNING and due <= now:
            overdue = True
    if overdue:
        return {"condition": "paused"}
    return {"condition": "healthy"}


async def disk_health(path: Path) -> dict:
    usage = await asyncio.to_thread(shutil.disk_usage, path)
    return {"value": (usage.total - usage.free) / usage.total * 100, "warning": 80, "critical": 95}


async def openbao_health(authority, **options) -> dict:
    started = time.monotonic()
    response = await request(
        base_url=authority.base_url,
        token=authority.token,
        method="GET",
        path="/sys/seal-status",
        **options,
    )
    document = json_document(response)
    if response.status_code != 200 or type(document.get("sealed")) is not bool:
        raise ValueError("OpenBao seal observation unavailable.")
    if not document["sealed"] and time.monotonic() - started >= 1:
        return {"value": time.monotonic() - started, "warning": 1, "critical": 3}
    return {"condition": "sealed" if document["sealed"] else "unsealed"}


@dataclass(frozen=True, repr=False)
class ScreenedPinHealth:
    paths: tuple[Path, ...]
    private_reader: object

    async def __call__(self) -> dict:
        if not self.paths:
            return {"condition": "not_configured"}
        expiries = []
        for path in self.paths:
            document = json.loads(await asyncio.to_thread(self.private_reader, path))
            if set(document) not in (
                {"origin", "address", "issued_at", "expires_at"},
                {"hosts", "issued_at", "expires_at"},
            ):
                raise ValueError("Screened pin schema unavailable.")
            issued, expires = document["issued_at"], document["expires_at"]
            if (
                type(issued) is not int
                or type(expires) is not int
                or not 0 < expires - issued <= 3600
            ):
                raise ValueError("Screened pin expiry unavailable.")
            if issued > datetime.now(UTC).timestamp():
                raise ValueError("Screened pin clock unavailable.")
            addresses = (
                tuple(document["hosts"].values()) if "hosts" in document else (document["address"],)
            )
            if not addresses:
                raise ValueError("Screened pin addresses unavailable.")
            validate_public_addresses(addresses)
            expiries.append(datetime.fromtimestamp(expires, UTC))
        return {"expires_at": min(expiries)}
