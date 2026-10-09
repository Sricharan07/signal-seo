"""Actual worker SIGKILL, durable skill admission and Temporal recovery/replay."""

import asyncio
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from signal_core.standing_authorization import revoke_standing_authorization
from signal_core.weekly_control import set_site_paused
from signal_core.weekly_loop import WeeklySiteLoop, WeeklyStageResult
from temporalio.worker import Replayer, Worker

from tests.control_plane.test_chat_reports import chat_env as chat_env
from tests.control_plane.test_pagespeed import setup as setup
from tests.control_plane.test_recipe_releases import release_manager as release_manager
from tests.control_plane.test_visibility_schedule import schedule_context as schedule_context
from tests.control_plane.test_visibility_schedule import settings
from tests.control_plane.test_weekly_skills import report
from tests.control_plane.test_weekly_skills import skill_setup as skill_setup
from tests.delivery.weekly_skill_worker import activities
from tests.temporal_runtime import start_temporal


@pytest.fixture
def psi_setup(skill_setup, setup):
    return setup


@pytest.fixture
def stage_cycle(stage, request, api, skill_setup):
    if stage == "pagespeed_refresh":
        request.getfixturevalue("psi_setup")
    elif stage == "visibility_reobserve":
        scope, context, _, _ = request.getfixturevalue("schedule_context")
        assert settings(api, scope, context) == "updated"
    elif stage == "chat_report_delivery":
        request.getfixturevalue("chat_env")
    return skill_setup()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("stop", [None, "pause", "revoke"])
@pytest.mark.parametrize(
    "stage",
    ["strategy_rebuild", "pagespeed_refresh", "visibility_reobserve", "chat_report_delivery"],
)
async def test_killed_skill_worker_never_reexecutes_unknown_intent_and_stops_new_work(
    admin,
    api,
    identity_context,
    skill_setup,
    tmp_path,
    stop,
    stage,
    stage_cycle,
):
    from uuid import UUID

    cycle = stage_cycle
    environment = await start_temporal(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    process = None
    try:
        queue = "synthetic-weekly-skills-" + uuid4().hex
        marker = tmp_path / "admitted"
        source = Path(__file__).resolve().parents[2] / "services/control_plane/src"
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("weekly_skill_worker.py")),
                environment.client.service_client.config.target_host,
                queue,
                str(marker),
                stage,
            ],
            env={**os.environ, "PYTHONPATH": str(source)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        handle = await environment.client.start_workflow(
            WeeklySiteLoop.run,
            cycle.site,
            id="synthetic-weekly-kill-" + uuid4().hex,
            task_queue=queue,
            result_type=tuple[WeeklyStageResult, ...],
        )
        for _ in range(300):
            if marker.exists():
                break
            assert process.poll() is None, "Disposable worker exited before admission."
            await asyncio.sleep(0.1)
        assert marker.exists(), "Skill admission did not complete."
        assert (
            admin.execute(
                "SELECT count(*) FROM app.weekly_skill_intents WHERE tenant_id=%s",
                (cycle.site.tenant_id,),
            ).fetchone()[0]
            == 1
        )
        process.kill()
        assert await asyncio.to_thread(process.wait, timeout=10) != 0
        if stop == "pause":
            set_site_paused(
                api,
                session_token=identity_context["session_token"],
                site_id=UUID(cycle.site.site_id),
                recovery_generation=identity_context["generation"],
                paused=True,
            )
        elif stop == "revoke":
            revoke_standing_authorization(
                api,
                session_token=identity_context["session_token"],
                current_recovery_generation=identity_context["generation"],
                site_id=UUID(cycle.site.site_id),
                grant_id=UUID(cycle.site.grant_id),
            )
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[WeeklySiteLoop],
            activities=activities(stage=stage),
            graceful_shutdown_timeout=timedelta(seconds=1),
        ):
            results = await asyncio.wait_for(handle.result(), timeout=90)
        assert results
        history = await handle.fetch_history()
        await Replayer(workflows=[WeeklySiteLoop]).replay_workflow(history)
        stages = {s["stage"]: s for s in report(api, cycle, identity_context)["skill_stages"]}
        assert len(stages) == 11
        assert stages[stage]["detail_code"] == "OUTCOME_UNKNOWN"
        assert stages["brief_proposals"]["detail_code"] == (
            "AUTHORITY_CHANGED"
            if stop and stage != "chat_report_delivery"
            else "DEPENDENCY_UNAVAILABLE"
            if stage == "strategy_rebuild"
            else "PORT_UNCONFIGURED"
        )
        assert not admin.execute(
            "SELECT 1 FROM app.seo_strategy_snapshots WHERE tenant_id=%s", (cycle.site.tenant_id,)
        ).fetchall()
        assert admin.execute(
            "SELECT total_count FROM app.autonomy_weekly_usage WHERE tenant_id=%s",
            (cycle.site.tenant_id,),
        ).fetchone()[0] == (
            4 if stage == "pagespeed_refresh" else 3 if stage == "visibility_reobserve" else 1
        )
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.wait, timeout=10)
        await environment.shutdown()
