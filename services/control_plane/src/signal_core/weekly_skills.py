"""Ordered, durable read/proposal skills under existing standing budgets."""

import asyncio
import secrets
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.database import _clean_transaction
from signal_core.recovery_authority import RecoveryGeneration
from signal_core.session_tokens import hash_session_token


@dataclass(frozen=True)
class SkillRegistration:
    name: str
    work_type: str
    budget_source: str = "standing_authorization"
    cap_source: str = "standing_authorization"
    after: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    phase: str = "research"

    def idempotency_key(self, site_id: UUID, cycle_id: UUID) -> tuple[UUID, UUID, str]:
        return site_id, cycle_id, self.name


SKILLS = (
    SkillRegistration("import_gsc", "research_audit"),
    SkillRegistration("import_bing", "research_audit"),
    SkillRegistration("import_ga4", "research_audit"),
    SkillRegistration(
        "pagespeed_refresh", "research_audit", cap_source="standing_authorization_and_pagespeed"
    ),
    SkillRegistration(
        "visibility_reobserve", "research_audit", cap_source="standing_authorization_and_assistants"
    ),
    SkillRegistration("brain_refresh", "research_audit", after=("observe", "analyze")),
    SkillRegistration(
        "strategy_rebuild",
        "research_audit",
        after=(
            "import_gsc",
            "import_bing",
            "import_ga4",
            "pagespeed_refresh",
            "visibility_reobserve",
            "brain_refresh",
        ),
    ),
    SkillRegistration(
        "internal_link_proposals",
        "draft_patch",
        after=("strategy_rebuild",),
        requires=("strategy_rebuild",),
    ),
    SkillRegistration(
        "brief_proposals",
        "draft_patch",
        cap_source="standing_authorization_and_content_writer",
        after=("strategy_rebuild",),
        requires=("strategy_rebuild",),
    ),
    SkillRegistration(
        "report_delivery",
        "research_audit",
        cap_source="standing_authorization_and_email",
        after=("report",),
        phase="delivery",
    ),
    SkillRegistration(
        "chat_report_delivery",
        "research_audit",
        cap_source="standing_authorization_and_chat",
        after=("report",),
        phase="delivery",
    ),
)


class SkillAuthorityChanged(PermissionError):
    pass


@dataclass(frozen=True)
class SkillPermit:
    tenant_id: UUID
    site_id: UUID
    cycle_id: UUID
    stage: str
    generation: str
    handle: str
    plan: tuple[dict, ...]
    week_start: str

    @property
    def handle_hash(self) -> bytes:
        return hash_session_token(self.handle)


@dataclass(frozen=True)
class SkillResult:
    outcome: str
    detail_code: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self):
        import re

        if self.outcome not in {"completed", "unavailable", "failed"} or not re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,127}", self.detail_code
        ):
            raise ValueError("Invalid skill result.")
        if len(self.evidence_refs) > 64 or any(
            not re.fullmatch(r"[A-Za-z0-9:/._#-]{1,512}", ref) for ref in self.evidence_refs
        ):
            raise ValueError("Invalid skill evidence.")


class SkillPort(Protocol):
    @property
    def configured(self) -> bool: ...

    async def run(self, permit: SkillPermit, guard: Callable[[], None]) -> SkillResult: ...


class WeeklySkills:
    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[Connection]],
        recovery_source,
        *,
        ports: Mapping[str, SkillPort] | None = None,
        brain_cost_bound_cents: int = 0,
    ):
        self.connection_factory = connection_factory
        self.recovery_source = recovery_source
        self.ports = dict(ports or {})
        if set(self.ports) - {skill.name for skill in SKILLS}:
            raise ValueError("Unregistered weekly skill.")
        if type(brain_cost_bound_cents) is not int or not 0 <= brain_cost_bound_cents <= 100000000:
            raise ValueError("A bounded per-source model cost is required.")
        self.brain_cost_bound_cents = brain_cost_bound_cents

    def _call(self, name, args):
        with self.connection_factory() as connection, _clean_transaction(connection):
            return connection.execute(
                f"SELECT control.{name}(" + ",".join(["%s"] * len(args)) + ")", args
            ).fetchone()[0]

    async def enable(self, cycle):
        return await asyncio.to_thread(
            self._call,
            "enable_weekly_skills",
            (UUID(cycle.site.tenant_id), UUID(cycle.site.site_id), UUID(cycle.cycle_id)),
        )

    async def _generation(self):
        generation = await self.recovery_source.current_generation()
        if not isinstance(generation, RecoveryGeneration):
            raise SkillAuthorityChanged("Recovery authority unavailable.")
        return generation.value

    async def run(self, cycle, phase: str) -> str:
        if phase not in {"research", "delivery"}:
            raise ValueError("Unknown skill phase.")
        identity = (UUID(cycle.site.tenant_id), UUID(cycle.site.site_id), UUID(cycle.cycle_id))
        for skill in SKILLS:
            if skill.phase != phase:
                continue
            scope = (identity[0], *skill.idempotency_key(identity[1], identity[2]))
            existing = await asyncio.to_thread(self._call, "weekly_skill_result", scope)
            if existing is not None:
                continue
            port = self.ports.get(skill.name)
            handle = secrets.token_urlsafe(32)
            try:
                generation = await self._generation()
                admitted = await asyncio.to_thread(
                    self._call,
                    "admit_weekly_skill",
                    (
                        *identity,
                        UUID(cycle.site.grant_id),
                        generation,
                        skill.name,
                        hash_session_token(handle),
                        self.brain_cost_bound_cents
                        if skill.name == "brain_refresh"
                        else getattr(port, "cost_bound_cents", 0)
                        if skill.name == "visibility_reobserve"
                        else 0,
                        port is not None and port.configured,
                    ),
                )
                state = admitted["state"]
                if state == "replayed":
                    continue
                if state != "started":
                    result = SkillResult(
                        "failed" if state == "unknown" else "unavailable", admitted["reason"]
                    )
                else:
                    permit = SkillPermit(
                        *identity,
                        skill.name,
                        generation,
                        handle,
                        tuple(admitted["plan"]),
                        cycle.week_start,
                    )
                    loop = asyncio.get_running_loop()

                    async def check(permit=permit):
                        if await self._generation() != permit.generation:
                            raise SkillAuthorityChanged("Recovery authority changed.")
                        try:
                            await asyncio.to_thread(
                                self._call,
                                "weekly_skill_context",
                                (permit.handle_hash, permit.generation, permit.site_id),
                            )
                        except Exception:
                            raise SkillAuthorityChanged(
                                "Current skill authority unavailable."
                            ) from None

                    def guard(check=check, loop=loop):
                        # Provider services run off the activity event loop. This
                        # bridge obtains fresh recovery authority before every I/O.
                        try:
                            asyncio.run_coroutine_threadsafe(check(), loop).result(timeout=20)
                        except Exception:
                            raise SkillAuthorityChanged(
                                "Current skill authority unavailable."
                            ) from None

                    try:
                        result = await asyncio.to_thread(
                            lambda port=port, permit=permit, guard=guard: asyncio.run(
                                port.run(permit, guard)
                            )
                        )
                    except Exception:
                        # Provider completion can itself be denied after a
                        # mid-flight pause. Distinguish that from provider failure.
                        await check()
                        raise
                    if not isinstance(result, SkillResult):
                        raise ValueError("Invalid skill port result.")
            except SkillAuthorityChanged:
                result = SkillResult("unavailable", "AUTHORITY_CHANGED")
            except Exception:
                # No exception text from providers, documents or secrets enters
                # the report. The intent remains available for operator inspection.
                result = SkillResult("failed", "SKILL_FAILED")
            recorded = await asyncio.to_thread(
                self._call,
                "record_weekly_skill",
                (*scope, result.outcome, result.detail_code, Jsonb(list(result.evidence_refs))),
            )
            if recorded != "recorded":
                raise RuntimeError("Skill evidence was not recorded.")
        return "recorded"
