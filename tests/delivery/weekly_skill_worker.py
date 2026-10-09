"""Disposable worker process killed after a committed skill admission."""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import psycopg
from signal_core.recovery_authority import RecoveryGeneration
from signal_core.weekly_loop import (
    WeeklyActivities,
    WeeklyCycleStore,
    WeeklySiteLoop,
    WeeklyStageResult,
)
from signal_core.weekly_skill_ports import local_skill_ports
from signal_core.weekly_skills import WeeklySkills
from temporalio.client import Client
from temporalio.worker import Worker

if __package__:
    from .weekly_candidate_support import UnavailableCandidatePort
else:
    from weekly_candidate_support import UnavailableCandidatePort


class Recovery:
    async def current_generation(self):
        return RecoveryGeneration("test-generation-1", 1)


class Work:
    async def run(self, stage):
        return WeeklyStageResult("unavailable", "SYNTHETIC_NO_PROVIDER")


class BlockedStrategy:
    configured = True
    cost_bound_cents = 3

    async def run(self, permit, guard):
        guard()
        Path(sys.argv[3]).touch(mode=0o600)
        await asyncio.Event().wait()


def connection():
    return psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True)


def activities(*, block=False, stage="strategy_rebuild"):
    ports = local_skill_ports(connection) if stage == "strategy_rebuild" else {}
    if block:
        ports[stage] = BlockedStrategy()
    instance = WeeklyActivities(
        WeeklyCycleStore(connection),
        work=Work(),
        candidates=UnavailableCandidatePort(),
        gate=SimpleNamespace(recovery_source=Recovery()),
        skills=WeeklySkills(connection, Recovery(), ports=ports),
    )
    return [
        instance.open,
        instance.run_stage,
        instance.gate_candidates,
        instance.handoff,
        instance.skip,
        instance.close,
        instance.run_skills,
    ]


async def main():
    client = await Client.connect(sys.argv[1])
    async with Worker(
        client,
        task_queue=sys.argv[2],
        workflows=[WeeklySiteLoop],
        activities=activities(
            block=True, stage=sys.argv[4] if len(sys.argv) > 4 else "strategy_rebuild"
        ),
    ):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
