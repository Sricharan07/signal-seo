"""Weekly authority and durable stage tests on non-owner PostgreSQL roles."""

from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from psycopg.errors import InsufficientPrivilege
from signal_core.recipe_releases import VERIFIED_HOMEPAGE_METADATA_RELEASE_ID
from signal_core.standing_authorization import (
    RecipeRange,
    StandingGrantRequest,
    grant_standing_authorization,
)
from signal_core.weekly_control import read_weekly_report, set_site_paused
from signal_core.weekly_loop import WeeklyCycle, WeeklySite


def _grant(admin, api, scope, identity_context):
    admin.execute(
        "UPDATE app.memberships SET role_key='owner' WHERE tenant_id=%s AND user_id=%s",
        (scope.tenant_id, identity_context["user_id"]),
    )
    admin.execute(
        "UPDATE app.sites SET state='active', ownership_status='verified' "
        "WHERE tenant_id=%s AND id=%s",
        (scope.tenant_id, scope.site_id),
    )
    now = admin.execute("SELECT transaction_timestamp()").fetchone()[0]
    return grant_standing_authorization(
        api,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
        request=StandingGrantRequest(
            site_id=scope.site_id,
            recipe_ranges=(RecipeRange("title_description_improvement", "1.0.0", "1.0.1"),),
            thresholds={"draft_patch": 0.8},
            weekly_volume_caps={"draft_patch": 2},
            weekly_total_cap=2,
            weekly_spend_cents=100,
            excluded_paths=(),
            starts_at=now,
            ends_at=now + timedelta(days=7),
            recovery_window_hours=24,
        ),
    )


def _cycle(scope, grant, admin):
    day = admin.execute("SELECT (transaction_timestamp() AT TIME ZONE 'UTC')::date").fetchone()[0]
    return WeeklyCycle.for_window(
        WeeklySite(str(scope.tenant_id), str(scope.site_id), str(grant.id)), day
    )


def _open(workflow, cycle, *, first_run_id=None):
    return workflow.execute(
        "SELECT control.open_weekly_cycle(%s,%s,%s,%s,%s,%s,%s)",
        (
            cycle.site.tenant_id,
            cycle.site.site_id,
            cycle.site.grant_id,
            cycle.week_start,
            cycle.cycle_id,
            first_run_id or cycle.cycle_id,
            "test-generation-1",
        ),
    ).fetchone()[0]


def test_weekly_cycle_is_unique_and_owner_report_contains_only_recorded_evidence(
    admin, api, workflow, scheduler, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)
    cycle = _cycle(scope, grant, admin)
    assert (
        workflow.execute(
            "SELECT control.open_weekly_cycle(%s,%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                grant.id,
                cycle.week_start,
                cycle.cycle_id,
                str(uuid4()),
                "rotated-generation",
            ),
        ).fetchone()[0]
        == "authority_unavailable"
    )
    assert not scheduler.execute(
        "SELECT control.weekly_schedule_eligibility(%s,%s,%s,%s)",
        (scope.tenant_id, scope.site_id, grant.id, "rotated-generation"),
    ).fetchone()[0]
    assert _open(workflow, cycle) == "opened"
    assert _open(workflow, cycle) == "opened"
    assert _open(workflow, cycle, first_run_id=str(uuid4())) == "cycle_exists"
    assert (
        workflow.execute(
            "SELECT control.weekly_cycle_authority(%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                cycle.week_start,
                cycle.cycle_id,
                grant.id,
                identity_context["generation"],
            ),
        ).fetchone()[0]
        == "active"
    )
    assert (
        workflow.execute(
            "SELECT control.record_weekly_stage(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                cycle.week_start,
                cycle.cycle_id,
                grant.id,
                "observe",
                "unavailable",
                [],
                "CRAWL_NOT_CONNECTED",
            ),
        ).fetchone()[0]
        == "recorded"
    )
    assert (
        workflow.execute(
            "SELECT control.record_weekly_stage(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                cycle.week_start,
                cycle.cycle_id,
                grant.id,
                "observe",
                "completed",
                [],
                "FABRICATED",
            ),
        ).fetchone()[0]
        == "stage_conflict"
    )
    assert (
        workflow.execute(
            "SELECT control.close_weekly_cycle(%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                cycle.week_start,
                cycle.cycle_id,
                "completed",
                "CYCLE_REPORTED",
            ),
        ).fetchone()[0]
        == "closed"
    )
    report = read_weekly_report(
        api,
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        recovery_generation=identity_context["generation"],
        week_start=admin.execute("SELECT %s::date", (cycle.week_start,)).fetchone()[0],
    )
    assert report["status"] == "completed"
    assert report["stages"] == [
        {
            "stage": "observe",
            "outcome": "unavailable",
            "detail_code": "CRAWL_NOT_CONNECTED",
            "evidence_refs": [],
            "recorded_at": report["stages"][0]["recorded_at"],
        }
    ]
    assert report["handoffs"] == []
    assert "metrics" not in report
    with pytest.raises(InsufficientPrivilege):
        workflow.execute("SELECT count(*) FROM app.weekly_handoffs")


def test_pause_revokes_grant_atomically_and_resume_never_restores_it(
    admin, api, workflow, scheduler, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)
    cycle = _cycle(scope, grant, admin)
    assert _open(workflow, cycle) == "opened"
    assert scheduler.execute(
        "SELECT control.weekly_schedule_eligibility(%s,%s,%s,%s)",
        (scope.tenant_id, scope.site_id, grant.id, identity_context["generation"]),
    ).fetchone()[0]
    paused = set_site_paused(
        api,
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        recovery_generation=identity_context["generation"],
        paused=True,
    )
    assert paused.state == "paused"
    assert (
        workflow.execute(
            "SELECT control.weekly_cycle_authority(%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                cycle.week_start,
                cycle.cycle_id,
                grant.id,
                identity_context["generation"],
            ),
        ).fetchone()[0]
        == "paused"
    )
    assert not scheduler.execute(
        "SELECT control.weekly_schedule_eligibility(%s,%s,%s,%s)",
        (scope.tenant_id, scope.site_id, grant.id, identity_context["generation"]),
    ).fetchone()[0]
    assert (
        workflow.execute(
            "SELECT * FROM control.standing_grant_eligibility(%s,%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                grant.id,
                identity_context["generation"],
                VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,
                "draft_patch",
                "/",
            ),
        ).fetchone()[1]
        == "site_paused"
    )
    assert (
        admin.execute(
            "SELECT count(*) FROM control.authority_restriction_outbox o "
            "JOIN app.standing_authorization_revocations r ON r.id=o.event_id "
            "WHERE r.grant_id=%s",
            (grant.id,),
        ).fetchone()[0]
        == 1
    )
    resumed = set_site_paused(
        api,
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        recovery_generation=identity_context["generation"],
        paused=False,
    )
    assert resumed.state == "pause_cleared"
    assert (
        workflow.execute(
            "SELECT control.weekly_cycle_authority(%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                cycle.week_start,
                cycle.cycle_id,
                grant.id,
                identity_context["generation"],
            ),
        ).fetchone()[0]
        == "authority_changed"
    )
    assert not scheduler.execute(
        "SELECT control.weekly_schedule_eligibility(%s,%s,%s,%s)",
        (scope.tenant_id, scope.site_id, grant.id, identity_context["generation"]),
    ).fetchone()[0]


def test_weekly_observation_command_is_idempotent_and_handoff_requires_ship(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)
    cycle = _cycle(scope, grant, admin)
    assert _open(workflow, cycle) == "opened"
    args = (scope.tenant_id, scope.site_id, cycle.week_start, cycle.cycle_id, grant.id)
    first = workflow.execute(
        "SELECT control.admit_weekly_observation(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (*args, identity_context["generation"], uuid4(), uuid4(), uuid4()),
    ).fetchone()[0]
    assert first is not None
    assert (
        workflow.execute(
            "SELECT control.admit_weekly_observation(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (*args, identity_context["generation"], uuid4(), uuid4(), uuid4()),
        ).fetchone()[0]
        == first
    )
    assert (
        admin.execute(
            "SELECT count(*) FROM app.commands WHERE tenant_id=%s AND id=%s",
            (scope.tenant_id, first),
        ).fetchone()[0]
        == 1
    )
    assert (
        admin.execute(
            "SELECT count(*) FROM app.outbox WHERE tenant_id=%s AND aggregate_id=%s",
            (scope.tenant_id, first),
        ).fetchone()[0]
        == 1
    )
    report = read_weekly_report(
        api,
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        recovery_generation=identity_context["generation"],
        week_start=admin.execute("SELECT %s::date", (cycle.week_start,)).fetchone()[0],
    )
    assert report["observation_command"] == {
        "command_id": str(first),
        "status": "accepted",
        "result_reference": None,
        "status_url": f"/v1/sites/{scope.site_id}/weekly-cycles/{cycle.week_start}"
        f"/observation/{first}",
    }
    assert (
        workflow.execute(
            "SELECT control.record_weekly_handoff(%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                cycle.week_start,
                cycle.cycle_id,
                uuid4(),
                identity_context["generation"],
            ),
        ).fetchone()[0]
        == "gate_unavailable"
    )


def test_exhausted_work_is_queued_for_next_week_without_metric_claims(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)
    cycle = _cycle(scope, grant, admin)
    assert _open(workflow, cycle) == "opened"
    revision = sha256(b"sealed-synthetic-deferred-candidate").digest()
    args = (scope.tenant_id, scope.site_id, cycle.week_start, cycle.cycle_id, [revision])
    assert (
        workflow.execute("SELECT control.defer_weekly_revisions(%s,%s,%s,%s,%s)", args).fetchone()[
            0
        ]
        == 1
    )
    assert (
        workflow.execute("SELECT control.defer_weekly_revisions(%s,%s,%s,%s,%s)", args).fetchone()[
            0
        ]
        == 1
    )
    assert admin.execute(
        "SELECT next_week,reason FROM app.weekly_deferred_revisions "
        "WHERE tenant_id=%s AND site_id=%s AND source_week=%s",
        (scope.tenant_id, scope.site_id, cycle.week_start),
    ).fetchone() == (
        admin.execute("SELECT %s::date+7", (cycle.week_start,)).fetchone()[0],
        "WEEKLY_CAP_REACHED",
    )
    assert (
        admin.execute(
            "SELECT count(*) FROM app.weekly_deferred_revisions WHERE tenant_id=%s AND site_id=%s",
            (scope.tenant_id, scope.site_id),
        ).fetchone()[0]
        == 1
    )
