"""Observe through the existing 0066 command consumer; analyze 0068 evidence."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date
from typing import Protocol
from uuid import UUID, uuid4

from psycopg import Connection
from temporalio import activity

from signal_core.crawl_audit import analyze_crawl_manifest
from signal_core.database import _clean_transaction
from signal_core.recovery_authority import RecoveryGeneration
from signal_core.weekly_loop import WeeklyStage, WeeklyStageResult


class RecoverySource(Protocol):
    async def current_generation(self) -> RecoveryGeneration: ...


class CrawlWeeklyWork:
    """No direct crawl I/O: the existing outbox and CrawlSite worker own it."""

    def __init__(
        self,
        workflow_connection: Callable[[], AbstractContextManager[Connection]],
        ingest_connection: Callable[[], AbstractContextManager[Connection]],
        recovery_source: RecoverySource,
        *,
        poll_seconds: float = 2.0,
        max_polls: int = 1800,
    ) -> None:
        if not 0.1 <= poll_seconds <= 30 or not 1 <= max_polls <= 3600:
            raise ValueError("Bounded observation polling is required.")
        self.workflow_connection = workflow_connection
        self.ingest_connection = ingest_connection
        self.recovery_source = recovery_source
        self.poll_seconds = poll_seconds
        self.max_polls = max_polls

    def _admit(self, stage: WeeklyStage, generation: str) -> UUID | None:
        cycle = stage.cycle
        with self.workflow_connection() as connection, _clean_transaction(connection):
            return connection.execute(
                "SELECT control.admit_weekly_observation(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    UUID(cycle.site.tenant_id),
                    UUID(cycle.site.site_id),
                    date.fromisoformat(cycle.week_start),
                    UUID(cycle.cycle_id),
                    UUID(cycle.site.grant_id),
                    generation,
                    uuid4(),
                    uuid4(),
                    uuid4(),
                ),
            ).fetchone()[0]

    def _status(self, stage: WeeklyStage) -> tuple[str, dict | None] | None:
        cycle = stage.cycle
        with self.workflow_connection() as connection, _clean_transaction(connection):
            return connection.execute(
                "SELECT * FROM control.weekly_observation_status(%s,%s,%s,%s)",
                (
                    UUID(cycle.site.tenant_id),
                    UUID(cycle.site.site_id),
                    date.fromisoformat(cycle.week_start),
                    UUID(cycle.cycle_id),
                ),
            ).fetchone()

    async def run(self, stage: WeeklyStage) -> WeeklyStageResult:
        if stage.name == "observe":
            generation = await self.recovery_source.current_generation()
            if not isinstance(generation, RecoveryGeneration):
                raise RuntimeError("Current recovery authority is unavailable.")
            command_id = await asyncio.to_thread(self._admit, stage, generation.value)
            if command_id is None:
                return WeeklyStageResult("stopped", "AUTHORITY_CHANGED")
            for _ in range(self.max_polls):
                status = await asyncio.to_thread(self._status, stage)
                activity.heartbeat(str(command_id))
                if status and status[0] == "succeeded" and status[1]:
                    ref = status[1]
                    if ref.get("kind") != "crawl_manifest" or not ref.get("manifest_id"):
                        return WeeklyStageResult("failed", "CRAWL_RESULT_INVALID")
                    return WeeklyStageResult(
                        "completed",
                        "CRAWL_OBSERVED",
                        (f"command:{command_id}", f"manifest:{ref['manifest_id']}"),
                    )
                if status and status[0] in {"failed", "cancelled"}:
                    return WeeklyStageResult(
                        "failed", "CRAWL_NOT_COMPLETED", (f"command:{command_id}",)
                    )
                await asyncio.sleep(self.poll_seconds)
            return WeeklyStageResult("deferred", "CRAWL_STILL_RUNNING", (f"command:{command_id}",))
        if stage.name in {"analyze", "plan"}:
            status = await asyncio.to_thread(self._status, stage)
            if not status or status[0] != "succeeded" or not status[1]:
                return WeeklyStageResult("unavailable", "CRAWL_EVIDENCE_UNAVAILABLE")
            manifest_id = UUID(status[1]["manifest_id"])
            with self.ingest_connection() as connection:
                report = await asyncio.to_thread(
                    analyze_crawl_manifest,
                    connection,
                    tenant_id=UUID(stage.cycle.site.tenant_id),
                    site_id=UUID(stage.cycle.site.site_id),
                    manifest_id=manifest_id,
                )
            if stage.name == "analyze":
                return WeeklyStageResult(
                    "completed",
                    "FINDINGS_SEALED",
                    (f"audit:{report.id}", f"manifest:{manifest_id}"),
                )
            ordered = sorted(
                report.findings,
                key=lambda item: (
                    {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(item.severity, 4),
                    item.key,
                    str(item.id),
                ),
            )
            return WeeklyStageResult(
                "completed",
                "EVIDENCE_PRIORITY_ONLY",
                (f"audit:{report.id}",) + tuple(f"finding:{item.id}" for item in ordered[:63]),
            )
        if stage.name == "report":
            return WeeklyStageResult(
                "completed", "CYCLE_REPORT_AVAILABLE", (f"cycle:{stage.cycle.cycle_id}",)
            )
        return WeeklyStageResult("unavailable", "CAPABILITY_NOT_CONNECTED")
