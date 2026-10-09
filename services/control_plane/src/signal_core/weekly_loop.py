"""Durable weekly orchestration with optional exact-authority delivery ports."""

import asyncio
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol
from uuid import UUID, uuid5

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from psycopg import Connection

    from signal_core.autonomy_gate import AutonomyCandidate, AutonomyGate
    from signal_core.database import _clean_transaction
    from signal_core.recovery_authority import RecoveryGeneration
    from signal_core.weekly_skill_ports import local_skill_ports
    from signal_core.weekly_skills import WeeklySkills

_CYCLE_NAMESPACE = UUID("757fd0a6-9af1-4e2b-969f-c4fb65db2404")
_STAGES = (
    "observe",
    "analyze",
    "plan",
    "prepare",
    "gate",
    "handoff",
    "verify",
    "measure",
    "report",
)
_OUTCOMES = {"completed", "unavailable", "deferred", "waiting_owner", "stopped", "failed"}


@dataclass(frozen=True)
class WeeklySite:
    tenant_id: str
    site_id: str
    grant_id: str

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.site_id, self.grant_id):
            if not isinstance(value, str) or str(UUID(value)) != value:
                raise ValueError("Weekly site identities must be canonical UUIDs.")


@dataclass(frozen=True)
class WeeklyCycle:
    site: WeeklySite
    week_start: str
    cycle_id: str

    @classmethod
    def for_window(cls, site: WeeklySite, day: date) -> "WeeklyCycle":
        monday = day - timedelta(days=day.weekday())
        week = monday.isoformat()
        identifier = uuid5(_CYCLE_NAMESPACE, f"{site.tenant_id}:{site.site_id}:{week}")
        return cls(site, week, str(identifier))


@dataclass(frozen=True)
class WeeklyStage:
    cycle: WeeklyCycle
    name: str

    def __post_init__(self) -> None:
        if self.name not in _STAGES:
            raise ValueError("Unknown weekly stage.")


@dataclass(frozen=True)
class WeeklyStageResult:
    outcome: str
    detail_code: str
    evidence_refs: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.outcome not in _OUTCOMES
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", self.detail_code) is None
        ):
            raise ValueError("Invalid weekly stage result.")
        if len(self.evidence_refs) > 64 or len(self.decision_ids) > 64:
            raise ValueError("Weekly stage result is too large.")
        if any(
            not isinstance(ref, str) or re.fullmatch(r"[A-Za-z0-9:/._#-]{1,512}", ref) is None
            for ref in self.evidence_refs
        ):
            raise ValueError("Invalid weekly evidence reference.")
        for value in self.decision_ids:
            if str(UUID(value)) != value:
                raise ValueError("Invalid gate decision identity.")


class WeeklyWorkPort(Protocol):
    async def run(self, stage: WeeklyStage) -> WeeklyStageResult: ...


class WeeklyCandidatePort(Protocol):
    async def prepare(
        self, stage: WeeklyStage, due_revisions: tuple[str, ...]
    ) -> tuple[AutonomyCandidate, ...]: ...


class WeeklyDeliveryPort(Protocol):
    async def dispatch(
        self, cycle: WeeklyCycle, decisions: tuple[str, ...], *, reconcile_only: bool = False
    ) -> WeeklyStageResult: ...


class WeeklyCycleStore:
    def __init__(
        self, connection_factory: Callable[[], AbstractContextManager[Connection]]
    ) -> None:
        self.connection_factory = connection_factory

    def open(self, cycle: WeeklyCycle, first_run_id: str, generation: str) -> str:
        with self.connection_factory() as connection, _clean_transaction(connection):
            return connection.execute(
                "SELECT control.open_weekly_cycle(%s,%s,%s,%s,%s,%s,%s)",
                (
                    UUID(cycle.site.tenant_id),
                    UUID(cycle.site.site_id),
                    UUID(cycle.site.grant_id),
                    date.fromisoformat(cycle.week_start),
                    UUID(cycle.cycle_id),
                    first_run_id,
                    generation,
                ),
            ).fetchone()[0]

    def authority(self, cycle: WeeklyCycle, generation: str) -> str:
        with self.connection_factory() as connection, _clean_transaction(connection):
            return connection.execute(
                "SELECT control.weekly_cycle_authority(%s,%s,%s,%s,%s,%s)",
                (
                    UUID(cycle.site.tenant_id),
                    UUID(cycle.site.site_id),
                    date.fromisoformat(cycle.week_start),
                    UUID(cycle.cycle_id),
                    UUID(cycle.site.grant_id),
                    generation,
                ),
            ).fetchone()[0]

    def read(self, stage: WeeklyStage) -> WeeklyStageResult | None:
        with self.connection_factory() as connection, _clean_transaction(connection):
            row = connection.execute(
                "SELECT * FROM control.read_weekly_stage(%s,%s,%s,%s,%s)",
                (
                    UUID(stage.cycle.site.tenant_id),
                    UUID(stage.cycle.site.site_id),
                    date.fromisoformat(stage.cycle.week_start),
                    UUID(stage.cycle.cycle_id),
                    stage.name,
                ),
            ).fetchone()
        if row is None:
            return None
        refs = tuple(row[2])
        decisions = tuple(ref.removeprefix("gate:") for ref in refs if ref.startswith("gate:"))
        return WeeklyStageResult(row[0], row[1], refs, decisions)

    def record(self, stage: WeeklyStage, result: WeeklyStageResult) -> str:
        with self.connection_factory() as connection, _clean_transaction(connection):
            return connection.execute(
                "SELECT control.record_weekly_stage(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    UUID(stage.cycle.site.tenant_id),
                    UUID(stage.cycle.site.site_id),
                    date.fromisoformat(stage.cycle.week_start),
                    UUID(stage.cycle.cycle_id),
                    UUID(stage.cycle.site.grant_id),
                    stage.name,
                    result.outcome,
                    list(result.evidence_refs),
                    result.detail_code,
                ),
            ).fetchone()[0]

    def handoff(self, cycle: WeeklyCycle, decision_id: str, generation: str) -> str:
        with self.connection_factory() as connection, _clean_transaction(connection):
            return connection.execute(
                "SELECT control.record_weekly_handoff(%s,%s,%s,%s,%s,%s)",
                (
                    UUID(cycle.site.tenant_id),
                    UUID(cycle.site.site_id),
                    date.fromisoformat(cycle.week_start),
                    UUID(cycle.cycle_id),
                    UUID(decision_id),
                    generation,
                ),
            ).fetchone()[0]

    def gate_receipt(self, candidate: AutonomyCandidate) -> tuple[str, str] | None:
        with self.connection_factory() as connection, _clean_transaction(connection):
            return connection.execute(
                "SELECT * FROM control.read_weekly_gate_outcome(" + ",".join(["%s"] * 10) + ")",
                (
                    candidate.scope.tenant_id,
                    candidate.scope.site_id,
                    candidate.decision_id,
                    candidate.grant_id,
                    candidate.recipe_release_id,
                    candidate.sealed_revision_sha256,
                    candidate.operation_id,
                    candidate.work_type,
                    candidate.resource_path,
                    candidate.cost_cents,
                ),
            ).fetchone()

    def preflight_reason(self, candidate: AutonomyCandidate, generation: str) -> str:
        with self.connection_factory() as connection, _clean_transaction(connection):
            row = connection.execute(
                "SELECT reason FROM control.autonomy_gate_preflight(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    candidate.scope.tenant_id,
                    candidate.scope.site_id,
                    candidate.grant_id,
                    generation,
                    candidate.recipe_release_id,
                    candidate.work_type,
                    candidate.resource_path,
                    candidate.cost_cents,
                ),
            ).fetchone()
        if row is None or not isinstance(row[0], str):
            raise RuntimeError("Current gate preflight is unavailable.")
        return row[0]

    def due(self, cycle: WeeklyCycle) -> tuple[str, ...]:
        with self.connection_factory() as connection, _clean_transaction(connection):
            rows = connection.execute(
                "SELECT * FROM control.due_weekly_revisions(%s,%s,%s,%s)",
                (
                    UUID(cycle.site.tenant_id),
                    UUID(cycle.site.site_id),
                    date.fromisoformat(cycle.week_start),
                    UUID(cycle.cycle_id),
                ),
            ).fetchall()
        return tuple(bytes(row[0]).hex() for row in rows)

    def defer(self, cycle: WeeklyCycle, revisions: tuple[str, ...]) -> None:
        with self.connection_factory() as connection, _clean_transaction(connection):
            count = connection.execute(
                "SELECT control.defer_weekly_revisions(%s,%s,%s,%s,%s)",
                (
                    UUID(cycle.site.tenant_id),
                    UUID(cycle.site.site_id),
                    date.fromisoformat(cycle.week_start),
                    UUID(cycle.cycle_id),
                    [bytes.fromhex(item) for item in revisions],
                ),
            ).fetchone()[0]
        if count != len(revisions):
            raise RuntimeError("Weekly deferral was not recorded.")

    def close(self, cycle: WeeklyCycle, status: str, reason: str) -> str:
        with self.connection_factory() as connection, _clean_transaction(connection):
            return connection.execute(
                "SELECT control.close_weekly_cycle(%s,%s,%s,%s,%s,%s)",
                (
                    UUID(cycle.site.tenant_id),
                    UUID(cycle.site.site_id),
                    date.fromisoformat(cycle.week_start),
                    UUID(cycle.cycle_id),
                    status,
                    reason,
                ),
            ).fetchone()[0]


class WeeklyActivities:
    """A separate, idempotency-keyed activity for each durable stage."""

    def __init__(
        self,
        store: WeeklyCycleStore,
        *,
        work: WeeklyWorkPort,
        candidates: WeeklyCandidatePort,
        gate: AutonomyGate,
        delivery: WeeklyDeliveryPort | None = None,
        skills: WeeklySkills | None = None,
        strategy_research: object | None = None,
    ) -> None:
        self.store = store
        self.work = work
        self.candidates = candidates
        self.gate = gate
        self.delivery = delivery
        self.skills = skills
        self.strategy_research = strategy_research

    async def _current_generation(self) -> str:
        try:
            generation = await self.gate.recovery_source.current_generation()
        except Exception as error:
            raise ApplicationError("Weekly recovery authority is unavailable.") from error
        if not isinstance(generation, RecoveryGeneration):
            raise ApplicationError("Weekly recovery authority is invalid.")
        return generation.value

    async def _prepared(self, stage: WeeklyStage) -> tuple[AutonomyCandidate, ...]:
        due = await asyncio.to_thread(self.store.due, stage.cycle)
        prepared = await self.candidates.prepare(stage, due)
        if (
            not isinstance(prepared, tuple)
            or len(prepared) > 32
            or any(not isinstance(item, AutonomyCandidate) for item in prepared)
        ):
            raise ApplicationError("Candidate port returned invalid work.", non_retryable=True)
        if due and tuple(item.sealed_revision_sha256.hex() for item in prepared[: len(due)]) != due:
            if prepared:
                raise ApplicationError("Deferred work was not prioritized.", non_retryable=True)
        return prepared

    @activity.defn(name="signal.weekly.open.v1")
    async def open(self, cycle: WeeklyCycle, first_run_id: str) -> str:
        generation = await self._current_generation()
        return await asyncio.to_thread(self.store.open, cycle, first_run_id, generation)

    @activity.defn(name="signal.weekly.skills.v1")
    async def run_skills(self, cycle: WeeklyCycle, phase: str) -> str:
        if self.skills is None:
            self.skills = WeeklySkills(
                self.store.connection_factory,
                self.gate.recovery_source,
                ports=local_skill_ports(
                    self.store.connection_factory, strategy_research=self.strategy_research
                ),
            )
        if phase == "enable":
            return await self.skills.enable(cycle)

        async def heartbeat():
            while True:
                activity.heartbeat(phase)
                await asyncio.sleep(2)

        pulse = asyncio.create_task(heartbeat()) if activity.in_activity() else None
        try:
            return await self.skills.run(cycle, phase)
        finally:
            if pulse is not None:
                pulse.cancel()
                await asyncio.gather(pulse, return_exceptions=True)

    @activity.defn(name="signal.weekly.stage.v1")
    async def run_stage(self, stage: WeeklyStage) -> WeeklyStageResult:
        existing = await asyncio.to_thread(self.store.read, stage)
        if existing is not None:
            return existing
        generation = await self._current_generation()
        authority = await asyncio.to_thread(self.store.authority, stage.cycle, generation)
        if authority != "active":
            return WeeklyStageResult("stopped", "AUTHORITY_CHANGED")
        if stage.name == "prepare":
            prepared = await self._prepared(stage)
            result = WeeklyStageResult(
                "completed" if prepared else "unavailable",
                "CANDIDATES_PREPARED"
                if prepared
                else getattr(self.candidates, "prepare_detail", "CANDIDATE_PORT_UNAVAILABLE"),
                tuple(f"revision:{item.sealed_revision_sha256.hex()}" for item in prepared)
                + getattr(self.candidates, "prepare_failure_refs", ()),
            )
        else:
            result = await self.work.run(stage)
        if not isinstance(result, WeeklyStageResult):
            raise ApplicationError("Invalid weekly stage result.", non_retryable=True)
        recorded = await asyncio.to_thread(self.store.record, stage, result)
        if recorded != "recorded":
            raise ApplicationError("Weekly stage evidence conflicts.", non_retryable=True)
        return result

    @activity.defn(name="signal.weekly.gate.v1")
    async def gate_candidates(self, stage: WeeklyStage) -> WeeklyStageResult:
        existing = await asyncio.to_thread(self.store.read, stage)
        if existing is not None:
            return existing
        generation = await self._current_generation()
        authority = await asyncio.to_thread(self.store.authority, stage.cycle, generation)
        if authority != "active":
            return WeeklyStageResult("stopped", "AUTHORITY_CHANGED")
        candidates = await self._prepared(WeeklyStage(stage.cycle, "prepare"))
        decisions = []
        outcomes = []
        exhausted = False
        changed = False
        deferred_revisions = []
        for index, candidate in enumerate(candidates):
            if (
                str(candidate.scope.tenant_id),
                str(candidate.scope.site_id),
                str(candidate.grant_id),
            ) != (
                stage.cycle.site.tenant_id,
                stage.cycle.site.site_id,
                stage.cycle.site.grant_id,
            ):
                raise ApplicationError("Candidate scope mismatch.", non_retryable=True)
            previous = await asyncio.to_thread(self.store.gate_receipt, candidate)
            if previous is None:
                gate_result = await self.gate.evaluate(candidate)
                gate_outcome, gate_reason = gate_result.outcome.value, gate_result.reason
            else:
                gate_outcome, gate_reason = previous
            decisions.append(str(candidate.decision_id))
            outcomes.append(gate_outcome)
            if gate_reason == "BUDGET_OR_AUTHORITY_CHANGED":
                current_generation = await self._current_generation()
                current_authority = await asyncio.to_thread(
                    self.store.authority, stage.cycle, current_generation
                )
                if current_authority != "active":
                    return WeeklyStageResult("stopped", "AUTHORITY_CHANGED")
                current_reason = await asyncio.to_thread(
                    self.store.preflight_reason, candidate, current_generation
                )
                if current_reason != "weekly_cap_reached":
                    changed = True
                    break
            if gate_reason in {"WEEKLY_CAP_REACHED", "BUDGET_OR_AUTHORITY_CHANGED"}:
                exhausted = True
                deferred_revisions = [
                    f"deferred_revision:{item.sealed_revision_sha256.hex()}"
                    for item in candidates[index:]
                ]
                break
        if exhausted:
            await asyncio.to_thread(
                self.store.defer,
                stage.cycle,
                tuple(item.sealed_revision_sha256.hex() for item in candidates[index:]),
            )
            outcome, code = "deferred", "NEXT_WEEKLY_WINDOW"
        elif changed:
            outcome, code = "waiting_owner", "GATE_AUTHORITY_CHANGED"
        elif "ship" in outcomes:
            outcome, code = "completed", "GATE_RECORDED"
        elif "ask_owner" in outcomes:
            outcome, code = "waiting_owner", "OWNER_REVIEW_OR_WEEKLY_CAP"
        else:
            outcome, code = "unavailable", "NO_APPROVED_CANDIDATE"
        result = WeeklyStageResult(
            outcome,
            code,
            tuple(f"gate:{item}" for item in decisions) + tuple(deferred_revisions),
            tuple(decisions),
        )
        recorded = await asyncio.to_thread(self.store.record, stage, result)
        if recorded != "recorded":
            raise ApplicationError("Gate stage evidence conflicts.", non_retryable=True)
        return result

    @activity.defn(name="signal.weekly.skip.v1")
    async def skip(self, stage: WeeklyStage) -> WeeklyStageResult:
        existing = await asyncio.to_thread(self.store.read, stage)
        if existing is not None:
            return existing
        generation = await self._current_generation()
        authority = await asyncio.to_thread(self.store.authority, stage.cycle, generation)
        if authority != "active":
            return WeeklyStageResult("stopped", "AUTHORITY_CHANGED")
        result = WeeklyStageResult("unavailable", "UPSTREAM_EVIDENCE_UNAVAILABLE")
        recorded = await asyncio.to_thread(self.store.record, stage, result)
        if recorded != "recorded":
            raise ApplicationError("Skipped stage evidence conflicts.", non_retryable=True)
        return result

    @activity.defn(name="signal.weekly.handoff.v1")
    async def handoff(self, stage: WeeklyStage, decisions: tuple[str, ...]) -> WeeklyStageResult:
        existing = await asyncio.to_thread(self.store.read, stage)
        if existing is not None:
            return existing
        if self.delivery is not None:
            generation = await self._current_generation()
            authority = await asyncio.to_thread(self.store.authority, stage.cycle, generation)
            receipt = await self.delivery.dispatch(
                stage.cycle, decisions, reconcile_only=authority != "active"
            )
            if authority != "active":
                return receipt
            recorded = await asyncio.to_thread(self.store.record, stage, receipt)
            if recorded != "recorded":
                raise ApplicationError("Delivery handoff evidence conflicts.", non_retryable=True)
            return receipt
        generation = await self._current_generation()
        refs = []
        for decision_id in decisions:
            result = await asyncio.to_thread(
                self.store.handoff, stage.cycle, decision_id, generation
            )
            if result == "recorded":
                refs.append(f"handoff:{decision_id}")
            elif result == "authority_changed":
                break
        outcome = "completed" if refs else "unavailable"
        receipt = WeeklyStageResult(
            outcome, "RECORD_ONLY_HANDOFF" if refs else "NO_CURRENT_HANDOFF", tuple(refs)
        )
        recorded = await asyncio.to_thread(self.store.record, stage, receipt)
        if recorded != "recorded":
            raise ApplicationError("Handoff evidence conflicts.", non_retryable=True)
        return receipt

    @activity.defn(name="signal.weekly.close.v1")
    async def close(self, cycle: WeeklyCycle, status: str, reason: str) -> str:
        if self.delivery is not None and status in {"stopped", "failed"}:
            await self.delivery.dispatch(cycle, (), reconcile_only=True)
        return await asyncio.to_thread(self.store.close, cycle, status, reason)


async def _execute(name: str, *args: object) -> object:
    return await workflow.execute_activity(
        name,
        args=args,
        result_type=str
        if name in {"signal.weekly.open.v1", "signal.weekly.close.v1", "signal.weekly.skills.v1"}
        else WeeklyStageResult,
        schedule_to_close_timeout=timedelta(minutes=70),
        start_to_close_timeout=timedelta(minutes=65),
        heartbeat_timeout=timedelta(seconds=20) if name == "signal.weekly.skills.v1" else None,
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


@workflow.defn(name="WeeklySiteLoop")
class WeeklySiteLoop:
    @workflow.run
    async def run(self, site: WeeklySite) -> tuple[WeeklyStageResult, ...]:
        if not isinstance(site, WeeklySite):
            raise ApplicationError("Invalid weekly site.", non_retryable=True)
        day = workflow.info().start_time.date()
        cycle = WeeklyCycle.for_window(site, day)
        opened = await _execute(
            "signal.weekly.open.v1", cycle, workflow.info().first_execution_run_id
        )
        if opened != "opened":
            raise ApplicationError("Weekly cycle was not admitted.", non_retryable=True)
        skills_enabled = workflow.patched("weekly-skills-0135")
        if skills_enabled:
            if await _execute("signal.weekly.skills.v1", cycle, "enable") != "enabled":
                raise ApplicationError("Weekly skill registry unavailable.", non_retryable=True)
        results = []
        actionable = True
        skills_started = False

        async def finish(status, reason):
            nonlocal skills_started
            if skills_enabled and not skills_started:
                await _execute("signal.weekly.skills.v1", cycle, "research")
                skills_started = True
            await _execute("signal.weekly.close.v1", cycle, status, reason)
            if skills_enabled:
                await _execute("signal.weekly.skills.v1", cycle, "delivery")

        try:
            for name in _STAGES:
                stage = WeeklyStage(cycle, name)
                if name in {"prepare", "gate", "handoff"} and not actionable:
                    result = await _execute("signal.weekly.skip.v1", stage)
                elif name == "gate":
                    result = await _execute("signal.weekly.gate.v1", stage)
                elif name == "handoff":
                    decisions = next(
                        (item.decision_ids for item in results[-1:] if item.decision_ids), ()
                    )
                    result = await _execute("signal.weekly.handoff.v1", stage, decisions)
                else:
                    result = await _execute("signal.weekly.stage.v1", stage)
                if not isinstance(result, WeeklyStageResult):
                    raise ApplicationError("Invalid weekly activity result.", non_retryable=True)
                results.append(result)
                if name == "analyze" and skills_enabled:
                    await _execute("signal.weekly.skills.v1", cycle, "research")
                    skills_started = True
                if (
                    name in {"observe", "analyze", "plan", "prepare"}
                    and result.outcome != "completed"
                ):
                    actionable = False
                if result.outcome == "stopped":
                    await finish("stopped", "AUTHORITY_CHANGED")
                    return tuple(results)
                if result.outcome == "failed":
                    await finish("failed", "STAGE_FAILED")
                    return tuple(results)
            await finish("completed", "CYCLE_REPORTED")
            return tuple(results)
        except ActivityError:
            await finish("failed", "ACTIVITY_FAILED")
            raise
