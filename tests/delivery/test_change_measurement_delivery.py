"""Real PostgreSQL and Temporal import-only horizons, restart and report proof."""

import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from signal_core.change_measurement import ChangeMeasurementJob, ChangeMeasurementWorkflow
from signal_core.weekly_control import read_weekly_report
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, Worker
from test_autonomy_delivery import anyio_backend as anyio_backend
from test_autonomy_delivery import delivery_harness as delivery_harness
from test_autonomy_delivery import prepare_candidate

from tests.control_plane.measurement_support import (
    add_confounding_change,
    add_pending_revision,
    bind_sources,
    import_bing_page_fixture,
    import_fixture,
)
from tests.control_plane.test_technical_recipes import release_manager as release_manager
from tests.temporal_runtime import start_temporal


@pytest.mark.anyio
@pytest.mark.parametrize("bing_page_rows", [False, True])
async def test_baseline_horizons_lag_replay_and_owner_report(
    delivery_harness, tmp_path, bing_page_rows
):
    h = delivery_harness
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    bindings, page = bind_sources(h.admin, h.identity, h.scope, h.context)
    baseline_ids = import_fixture(
        h.admin, h.scope, bindings, page, today - timedelta(days=90), today - timedelta(days=1)
    )
    if bing_page_rows:
        page_baseline, page_coverage = import_bing_page_fixture(
            h.admin,
            h.scope,
            bindings,
            page,
            today - timedelta(days=90),
            today - timedelta(days=1),
            clicks=5,
        )
    candidate = await prepare_candidate(h)
    decision = await h.gate.evaluate(candidate)
    assert decision.outcome.value == "ship"
    await h.delivery.dispatch(h.cycle, (str(candidate.decision_id),))
    from signal_core.weekly_loop import WeeklyStage

    assert (await h.delivery.run(WeeklyStage(h.cycle, "verify"))).detail_code == "LIVE_VERIFIED"
    operation = candidate.operation_id
    other = add_confounding_change(h.admin, operation)
    plans = h.admin.execute(
        "SELECT horizon,baseline,post_start,post_end,due_at FROM app.change_measurement_plans "
        "WHERE operation_id=%s ORDER BY horizon",
        (operation,),
    ).fetchall()
    assert [p[0] for p in plans] == [7, 28, 90]
    assert [
        int(r[0])
        for r in h.admin.execute(
            "SELECT extract(epoch FROM due_at-verified_live_at) FROM app.change_measurement_plans "
            "WHERE operation_id=%s ORDER BY horizon",
            (operation,),
        ).fetchall()
    ] == [horizon * 86400 for horizon in (7, 28, 90)]

    def concurrent_measure():
        with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as connection:
            return connection.execute(
                "SELECT control.measure_scheduled_change(%s,%s,%s,%s)",
                (h.scope.tenant_id, h.scope.site_id, operation, 7),
            ).fetchone()[0]

    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(executor.map(lambda _: concurrent_measure(), range(8)))
    assert all(value == values[0] for value in values)
    assert (
        h.admin.execute(
            "SELECT count(*) FROM app.change_measurement_observations "
            "WHERE operation_id=%s AND horizon=7",
            (operation,),
        ).fetchone()[0]
        == 1
    )
    for horizon, baseline, start, end, due in plans:
        assert due > datetime.now(UTC)
        assert baseline["gsc_page"]["generation_id"] == str(baseline_ids[0])
        assert baseline["gsc_page"]["coverage"] == baseline_ids[2]
        assert baseline["gsc_page"]["metrics"]["clicks"] == horizon * 10
        assert baseline["bing_site_context"]["metrics"]["clicks"] == horizon * 20
        if bing_page_rows:
            assert baseline["bing_page"]["generation_id"] == str(page_baseline)
            assert baseline["bing_page"]["coverage"] == page_coverage
            assert baseline["bing_page"]["state"] == "measured_as_reported"
            assert baseline["bing_page"]["metrics"]["clicks"] == horizon * 5
        else:
            assert baseline["bing_page"]["state"] == "unavailable"
        before = h.workflow.execute(
            "SELECT control.measure_scheduled_change(%s,%s,%s,%s)",
            (h.scope.tenant_id, h.scope.site_id, operation, horizon),
        ).fetchone()[0]
        assert before["state"] == "not_yet_due"
        now = datetime.combine(
            end + timedelta(days=1), datetime.min.time(), ZoneInfo("America/Los_Angeles")
        ) + timedelta(hours=1)

        def measure(horizon=horizon, now=now):
            return h.admin.execute(
                "SELECT control.measure_change_horizon(%s,%s,%s,%s,%s)",
                (h.scope.tenant_id, h.scope.site_id, operation, horizon, now),
            ).fetchone()[0]

        assert measure()["state"] == "awaiting_data"
        import_fixture(h.admin, h.scope, bindings, page, start, end, lag=True, clicks=12)
        assert measure()["reason"] == "PROVIDER_LAG"
        g, b, c, bc = import_fixture(h.admin, h.scope, bindings, page, start, end, clicks=12)
        if bing_page_rows:
            post_page, post_coverage = import_bing_page_fixture(
                h.admin,
                h.scope,
                bindings,
                page,
                start,
                end,
                clicks=7,
            )
        result = measure()
        assert result["state"] == "measured_as_reported"
        assert result["post"]["gsc_page"]["coverage"] == c
        assert result["post"]["bing_site_context"]["coverage"] == bc
        assert result["observed_change"]["gsc_page"]["clicks"] == horizon * 2
        assert result["observed_change"]["bing_site_context"]["clicks"] == horizon * 4
        assert result["observed_change"]["bing_site_context"]["position"] is None
        if bing_page_rows:
            assert result["post"]["bing_page"]["generation_id"] == str(post_page)
            assert result["post"]["bing_page"]["coverage"] == post_coverage
            assert result["post"]["bing_page"]["state"] == "measured_as_reported"
            assert result["post"]["bing_page"]["coverage"]["complete"] is False
            assert result["observed_change"]["bing_page"] == {
                "clicks": horizon * 2,
                "impressions": 0,
                "ctr": None,
                "position": None,
            }
        else:
            assert result["post"]["bing_page"]["state"] == "unavailable"
            assert result["observed_change"]["bing_page"] == {
                "clicks": None,
                "impressions": None,
                "ctr": None,
                "position": None,
            }
        assert [c["operation_id"] for c in result["confounders"]] == [str(other)]
        count = h.admin.execute(
            "SELECT count(*) FROM app.change_measurement_observations "
            "WHERE operation_id=%s AND horizon=%s",
            (operation, horizon),
        ).fetchone()[0]
        assert measure() == result
        assert (
            h.admin.execute(
                "SELECT count(*) FROM app.change_measurement_observations "
                "WHERE operation_id=%s AND horizon=%s",
                (operation, horizon),
            ).fetchone()[0]
            == count
        )
        week = due.date() - timedelta(days=due.weekday())
        h.admin.execute(
            "INSERT INTO app.weekly_cycles(tenant_id,site_id,week_start,id,grant_id,first_run_id) "
            "VALUES(%s,%s,%s,%s,%s,'synthetic-measurement-report') ON CONFLICT DO NOTHING",
            (h.scope.tenant_id, h.scope.site_id, week, uuid4(), UUID(h.cycle.site.grant_id)),
        )
        projection = read_weekly_report(
            h.api,
            session_token=h.context["session_token"],
            site_id=h.scope.site_id,
            recovery_generation=h.context["generation"],
            week_start=week,
        )
        from signal_api.weekly_report_contracts import WeeklyReportResponse

        assert WeeklyReportResponse.model_validate(projection).schema_version == 2
        item = next(m for m in projection["measurements"] if m["operation_id"] == str(operation))
        assert item["horizon"] == horizon and item["observation"] == result
    from signal_core.seo_strategy_service import refresh_strategy, strategy_call

    strategy_args = {
        "session_token": h.context["session_token"],
        "generation": h.context["generation"],
        "site_id": h.scope.site_id,
    }
    sources = strategy_call(h.api, **strategy_args, action="sources")
    measured = sources["learning"]["measurements"]
    assert [m["horizon"] for m in measured] == [28, 90]
    assert len({m["id"] for m in measured}) == 2
    assert all(m["work_type"] == "metadata_pr" for m in measured)
    recipe = h.admin.execute(
        "SELECT recipe_key FROM control.recipe_releases WHERE id=%s", (candidate.recipe_release_id,)
    ).fetchone()[0]
    assert all(m["recipe_key"] == recipe for m in measured)
    assert all(len(m["document"]["confounders"]) == 1 for m in measured)
    assert str(operation) in {c["operation_id"] for c in sources["learning"]["changes"]}
    refresh_strategy(h.api, **strategy_args)
    learning = strategy_call(h.api, **strategy_args, action="read")["snapshot"]["payload"][
        "learning"
    ]
    assert all(
        g["sample_size"] == 1 and g["effective_sample_size"] == 0.5 and g["factor"] == 1
        for g in learning["effectiveness"]["groups"]
    )
    assert page in learning["decay"]["excluded_pages"]
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        h.admin.execute(
            "UPDATE app.change_measurement_plans SET baseline='{}' WHERE operation_id=%s",
            (operation,),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        h.admin.execute(
            "DELETE FROM app.change_measurement_observations WHERE operation_id=%s", (operation,)
        )
    # The runtime role has no clock-injection function privilege.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        h.workflow.execute(
            "SELECT control.measure_change_horizon(%s,%s,%s,%s,%s)",
            (h.scope.tenant_id, h.scope.site_id, operation, 7, datetime.now(UTC)),
        )
    assert h.workflow.execute(
        "SELECT control.measure_scheduled_change(%s,%s,%s,%s)",
        (uuid4(), h.scope.site_id, operation, 7),
    ).fetchone() == (None,)
    report = read_weekly_report(
        h.api,
        session_token=h.context["session_token"],
        site_id=h.scope.site_id,
        recovery_generation=h.context["generation"],
        week_start=date.fromisoformat(h.cycle.week_start),
    )
    assert report["schema_version"] == 2
    assert report["needs_decision"]["count"] == 0
    assert report["what_is_next"]["state"] == "unavailable"
    h.admin.execute(
        "INSERT INTO app.weekly_deferred_revisions(tenant_id,site_id,source_week,"
        "sealed_revision_sha256,next_week,reason) VALUES(%s,%s,%s,%s,%s,'WEEKLY_CAP_REACHED')",
        (
            h.scope.tenant_id,
            h.scope.site_id,
            date.fromisoformat(h.cycle.week_start),
            candidate.sealed_revision_sha256,
            date.fromisoformat(h.cycle.week_start) + timedelta(days=7),
        ),
    )
    pending = add_pending_revision(h.admin, operation)
    report = read_weekly_report(
        h.api,
        session_token=h.context["session_token"],
        site_id=h.scope.site_id,
        recovery_generation=h.context["generation"],
        week_start=date.fromisoformat(h.cycle.week_start),
    )
    assert WeeklyReportResponse.model_validate(report).schema_version == 2
    assert report["needs_decision"]["count"] == 1
    assert report["needs_decision"]["items"] == [
        {"revision_id": str(pending), "kind": "technical", "url": f"/approvals?revision={pending}"}
    ]
    assert report["what_is_next"]["state"] == "available"
    assert report["what_is_next"]["items"][0]["reason"] == "WEEKLY_CAP_REACHED"
    assert (
        read_weekly_report(
            h.api,
            session_token=h.context["session_token"],
            site_id=uuid4(),
            recovery_generation=h.context["generation"],
            week_start=date.fromisoformat(h.cycle.week_start),
        )
        is None
    )
    h.admin.execute(
        "UPDATE app.memberships SET role_key='analyst' WHERE id=%s", (h.context["membership_id"],)
    )
    assert (
        read_weekly_report(
            h.api,
            session_token=h.context["session_token"],
            site_id=h.scope.site_id,
            recovery_generation=h.context["generation"],
            week_start=date.fromisoformat(h.cycle.week_start),
        )
        is None
    )

    environment = await start_temporal(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    try:
        queue = "measurement-" + uuid4().hex
        job = ChangeMeasurementJob(
            str(h.scope.tenant_id),
            str(h.scope.site_id),
            str(operation),
            7,
            (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        )
        attempts = 0

        @activity.defn(name="signal.change.measure.v1")
        async def committed_then_lost(job: ChangeMeasurementJob) -> str:
            nonlocal attempts
            attempts += 1
            value = h.admin.execute(
                "SELECT control.measure_change_horizon(%s,%s,%s,%s,%s)",
                (
                    UUID(job.tenant_id),
                    UUID(job.site_id),
                    UUID(job.operation_id),
                    job.horizon,
                    datetime.combine(
                        plans[0][3] + timedelta(days=1),
                        datetime.min.time(),
                        ZoneInfo("America/Los_Angeles"),
                    )
                    + timedelta(hours=1),
                ),
            ).fetchone()[0]
            if attempts == 1:
                raise ApplicationError("Synthetic worker exit after measurement commit.")
            return value["state"]

        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[ChangeMeasurementWorkflow],
            activities=[committed_then_lost],
        ):
            handle = await environment.client.start_workflow(
                ChangeMeasurementWorkflow.run, job, id=job.workflow_id, task_queue=queue
            )
            assert await asyncio.wait_for(handle.result(), 30) == "measured_as_reported"
            history = await handle.fetch_history()
        assert attempts == 2
        await Replayer(workflows=[ChangeMeasurementWorkflow]).replay_workflow(history)
        queue = "measurement-kill-" + uuid4().hex
        marker = tmp_path / "synthetic-commit-marker"
        settings = tmp_path / "synthetic-worker-settings.json"
        observation_time = datetime.combine(
            plans[0][3] + timedelta(days=1), datetime.min.time(), ZoneInfo("America/Los_Angeles")
        ) + timedelta(hours=1)
        settings.write_text(
            json.dumps(
                {
                    "address": environment.client.service_client.config.target_host,
                    "queue": queue,
                    "marker": str(marker),
                    "observation_time": observation_time.isoformat(),
                }
            )
        )
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(["services/control_plane/src", "."]))
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.delivery.measurement_kill_worker", str(settings)], env=env
        )
        try:
            handle = await environment.client.start_workflow(
                ChangeMeasurementWorkflow.run, job, id=job.workflow_id + ":kill", task_queue=queue
            )
            for _ in range(200):
                if marker.exists():
                    break
                if process.poll() is not None:
                    pytest.fail("Synthetic worker exited before measurement commit.")
                await asyncio.sleep(0.1)
            assert marker.exists()
            subprocess.run(["ps", "-p", str(process.pid), "-o", "pid,lstart,command"], check=True)
            process.kill()
            await asyncio.to_thread(process.wait)
            async with Worker(
                environment.client,
                task_queue=queue,
                workflows=[ChangeMeasurementWorkflow],
                activities=[committed_then_lost],
            ):
                assert await asyncio.wait_for(handle.result(), 45) == "measured_as_reported"
            await Replayer(workflows=[ChangeMeasurementWorkflow]).replay_workflow(
                await handle.fetch_history()
            )
        finally:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait)
    finally:
        await environment.shutdown()
