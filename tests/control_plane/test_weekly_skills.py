"""Real PostgreSQL standing authority for weekly read/proposal stages."""

import asyncio
import os
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
import rfc8785
from psycopg.errors import InsufficientPrivilege
from psycopg.types.json import Jsonb
from signal_core.database import _clean_transaction
from signal_core.recipe_releases import register_recipe_release
from signal_core.recovery_authority import RecoveryGeneration
from signal_core.session_tokens import hash_session_token
from signal_core.standing_authorization import (
    RecipeRange,
    StandingGrantRequest,
    grant_standing_authorization,
)
from signal_core.weekly_control import read_weekly_report, set_site_paused
from signal_core.weekly_skill_ports import WeeklySkillConnection, local_skill_ports
from signal_core.weekly_skills import SKILLS, SkillPermit, WeeklySkills

from tests.control_plane.test_gsc_binding import owner_and_verified_origin
from tests.control_plane.test_recipe_releases import (
    _actor,
    _key,
    _manifest,
    _review,
)
from tests.control_plane.test_recipe_releases import (
    release_manager as release_manager,
)
from tests.control_plane.test_weekly_loop import _cycle, _open


class Recovery:
    async def current_generation(self):
        return RecoveryGeneration("test-generation-1", 1)


def workflow_connection():
    return psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True)


@pytest.fixture
def skill_setup(admin, api, identity, scopes, identity_context, release_manager):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    actor, key, release = _actor(admin), _key(release_manager), uuid4()
    family = "synthetic_weekly_" + uuid4().hex
    body = rfc8785.dumps(
        {**_manifest(release, "1.0.0", key=family, mode="proposal_only"), "approval_class": "A0"}
    )
    register_recipe_release(
        release_manager,
        release_id=release,
        recipe_key=family,
        version="1.0.0",
        canonical_body=body,
        signing_key_id=key[0],
        signature=key[1].sign(body),
        actor_user_id=actor,
    )
    _review(release_manager, release, actor)

    def create(*, research=True, drafts=True, cap=25, spend=100, excluded=(), lifetime=604800):
        now = admin.execute("SELECT transaction_timestamp()").fetchone()[0]
        types = {
            **({"research_audit": 0.0} if research else {}),
            **({"draft_patch": 0.8} if drafts else {}),
        }
        ranges = (
            *((RecipeRange(family, "1.0.0", "1.0.1"),) if research else ()),
            *((RecipeRange("title_description_improvement", "1.0.0", "1.0.1"),) if drafts else ()),
        )
        grant = grant_standing_authorization(
            api,
            session_token=identity_context["session_token"],
            current_recovery_generation=identity_context["generation"],
            request=StandingGrantRequest(
                site_id=scope.site_id,
                recipe_ranges=ranges,
                thresholds=types,
                weekly_volume_caps={name: cap for name in types},
                weekly_total_cap=cap,
                weekly_spend_cents=spend,
                excluded_paths=excluded,
                starts_at=now,
                ends_at=now + timedelta(seconds=lifetime),
                recovery_window_hours=24,
            ),
        )
        return _cycle(scope, grant, admin)

    return create


def open_skills(workflow, cycle):
    assert _open(workflow, cycle) == "opened"
    with _clean_transaction(workflow):
        assert (
            workflow.execute(
                "SELECT control.enable_weekly_skills(%s,%s,%s)",
                (cycle.site.tenant_id, cycle.site.site_id, cycle.cycle_id),
            ).fetchone()[0]
            == "enabled"
        )


def admit(workflow, cycle, stage, *, configured=True, cost=0, handle=None):
    handle = handle or secrets.token_urlsafe(32)
    with _clean_transaction(workflow):
        result = workflow.execute(
            "SELECT control.admit_weekly_skill(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                cycle.site.tenant_id,
                cycle.site.site_id,
                cycle.cycle_id,
                cycle.site.grant_id,
                "test-generation-1",
                stage,
                hash_session_token(handle),
                cost,
                configured,
            ),
        ).fetchone()[0]
    return result, handle


def report(api, cycle, context):
    from datetime import date

    return read_weekly_report(
        api,
        session_token=context["session_token"],
        site_id=UUID(cycle.site.site_id),
        recovery_generation=context["generation"],
        week_start=date.fromisoformat(cycle.week_start),
    )


def test_model_ports_are_workload_scoped_and_cap_setting_is_not_copied(
    admin, workflow, skill_setup, scopes
):
    cycle = skill_setup()
    open_skills(workflow, cycle)
    result, handle = admit(workflow, cycle, "strategy_rebuild")
    assert result["state"] == "started"
    digest = hash_session_token(handle)
    site = UUID(cycle.site.site_id)
    with _clean_transaction(workflow):
        assert (
            workflow.execute(
                "SELECT control.weekly_skill_model_budget_read(%s,%s,%s)",
                (digest, "test-generation-1", site),
            ).fetchone()[0]["tenant_id"]
            == cycle.site.tenant_id
        )
    for generation, target in [
        ("wrong-generation", site),
        ("test-generation-1", scopes[1].site_id),
    ]:
        with pytest.raises(InsufficientPrivilege), _clean_transaction(workflow):
            workflow.execute(
                "SELECT control.weekly_skill_model_budget_read(%s,%s,%s)",
                (digest, generation, target),
            )
    with pytest.raises(InsufficientPrivilege), _clean_transaction(workflow):
        workflow.execute(
            "SELECT control.weekly_skill_model_budget_reserve(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                digest,
                "test-generation-1",
                site,
                uuid4(),
                b"a" * 32,
                "fact_extraction",
                Jsonb({}),
                1,
            ),
        )
    assert (
        admin.execute(
            "SELECT to_regprocedure("
            "'control.weekly_skill_model_budget_set_cap(bytea,text,uuid,bigint)')"
        ).fetchone()[0]
        is None
    )
    assert not admin.execute(
        "SELECT has_function_privilege('signal_workflow',"
        "'control.model_budget_set_cap(bytea,text,uuid,bigint)','EXECUTE')"
    ).fetchone()[0]


def test_registry_and_current_covering_grant_are_required(
    admin, api, workflow, scopes, identity_context, skill_setup
):
    assert len(SKILLS) == 11 and len({s.name for s in SKILLS}) == 11
    assert all(s.work_type in {"research_audit", "draft_patch"} for s in SKILLS)
    positions = {s.name: i for i, s in enumerate(SKILLS)}
    for skill in SKILLS:
        assert all(positions[d] < positions[skill.name] for d in skill.after if d in positions)
        assert set(skill.requires) <= set(skill.after)
        site, cycle_id = uuid4(), uuid4()
        assert skill.idempotency_key(site, cycle_id) == (site, cycle_id, skill.name)
    cycle = skill_setup(research=False)
    open_skills(workflow, cycle)
    for stage in SKILLS:
        if stage.work_type == "research_audit":
            assert admit(workflow, cycle, stage.name)[0]["reason"] == "WORK_TYPE_NOT_GRANTED"
    assert admit(workflow, cycle, "brief_proposals")[0]["reason"] == "DEPENDENCY_UNAVAILABLE"
    engine = WeeklySkills(
        workflow_connection, Recovery(), ports=local_skill_ports(workflow_connection)
    )
    asyncio.run(engine.run(cycle, "research"))
    assert all(
        s["outcome"] == "unavailable" for s in report(api, cycle, identity_context)["skill_stages"]
    )
    assert not admin.execute(
        "SELECT 1 FROM app.autonomy_reservations WHERE tenant_id=%s", (scopes[0].tenant_id,)
    ).fetchall()


def test_strategy_rebuild_is_worker_scoped_replayable_and_reports_partial_sources(
    admin, api, workflow, identity_context, skill_setup
):
    cycle = skill_setup()
    open_skills(workflow, cycle)
    engine = WeeklySkills(
        workflow_connection, Recovery(), ports=local_skill_ports(workflow_connection)
    )
    asyncio.run(engine.run(cycle, "research"))
    stages = {s["stage"]: s for s in report(api, cycle, identity_context)["skill_stages"]}
    assert stages["strategy_rebuild"]["detail_code"] == "STRATEGY_RECORDED"
    assert stages["strategy_rebuild"]["units"] == 1
    assert stages["import_gsc"]["detail_code"] == "PORT_UNCONFIGURED"
    assert stages["brief_proposals"]["detail_code"] == "NO_ELIGIBLE_BRIEFS"
    before = admin.execute(
        "SELECT count(*) FROM app.seo_strategy_snapshots WHERE tenant_id=%s",
        (cycle.site.tenant_id,),
    ).fetchone()[0]
    asyncio.run(engine.run(cycle, "research"))
    assert (
        admin.execute(
            "SELECT count(*) FROM app.seo_strategy_snapshots WHERE tenant_id=%s",
            (cycle.site.tenant_id,),
        ).fetchone()[0]
        == before
        == 1
    )
    assert (
        admin.execute(
            "SELECT total_count FROM app.autonomy_weekly_usage WHERE tenant_id=%s",
            (cycle.site.tenant_id,),
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize("negative", ["pause", "revoke", "generation", "expiry", "owner_role"])
def test_mid_cycle_authority_changes_stop_ports_and_new_work(
    admin, api, workflow, identity_context, skill_setup, negative
):
    cycle = skill_setup(lifetime=5 if negative == "expiry" else 604800)
    open_skills(workflow, cycle)
    started, handle = admit(workflow, cycle, "strategy_rebuild")
    assert started["state"] == "started"
    if negative == "pause":
        set_site_paused(
            api,
            session_token=identity_context["session_token"],
            site_id=UUID(cycle.site.site_id),
            recovery_generation=identity_context["generation"],
            paused=True,
        )
    elif negative == "revoke":
        from signal_core.standing_authorization import revoke_standing_authorization

        revoke_standing_authorization(
            api,
            session_token=identity_context["session_token"],
            current_recovery_generation=identity_context["generation"],
            site_id=UUID(cycle.site.site_id),
            grant_id=UUID(cycle.site.grant_id),
        )
    elif negative == "owner_role":
        admin.execute(
            "UPDATE app.memberships SET role_key='analyst' WHERE id=%s",
            (identity_context["membership_id"],),
        )
    elif negative == "expiry":
        import time

        time.sleep(5.1)
    with pytest.raises(InsufficientPrivilege), _clean_transaction(workflow):
        workflow.execute(
            "SELECT * FROM control.weekly_skill_context(%s,%s,%s)",
            (
                hash_session_token(handle),
                "stale-generation" if negative == "generation" else "test-generation-1",
                cycle.site.site_id,
            ),
        ).fetchone()
    if negative != "generation":
        assert admit(workflow, cycle, "brain_refresh", cost=1)[0]["reason"] == "AUTHORITY_CHANGED"


@pytest.mark.parametrize("role", ["api", "identity", "scheduler"])
def test_worker_ports_reject_human_and_scheduler_roles(
    request, role, workflow, scopes, skill_setup
):
    cycle = skill_setup()
    open_skills(workflow, cycle)
    connection = request.getfixturevalue(role)
    with pytest.raises(InsufficientPrivilege):
        admit(connection, cycle, "strategy_rebuild")


def test_worker_handle_never_becomes_a_human_session_and_has_no_approval_ports(
    admin, api, workflow, scopes, skill_setup
):
    cycle = skill_setup()
    open_skills(workflow, cycle)
    _, handle = admit(workflow, cycle, "strategy_rebuild")
    digest = hash_session_token(handle)
    for site in (scopes[1].site_id, scopes[2].site_id):
        with pytest.raises(InsufficientPrivilege), _clean_transaction(workflow):
            workflow.execute(
                "SELECT control.weekly_skill_seo_strategy_sources(%s,%s,%s)",
                (digest, "test-generation-1", site),
            )
    assert not admin.execute(
        "SELECT 1 FROM app.sessions WHERE session_token_hash=%s", (digest,)
    ).fetchall()
    for name, signature in (
        ("business_brain_approve", "bytea,text,uuid,uuid,uuid"),
        ("content_writer_accept_brief", "bytea,text,uuid,uuid"),
        ("seo_strategy_decide", "bytea,text,uuid,uuid,uuid,text"),
    ):
        assert not admin.execute(
            "SELECT has_function_privilege('signal_workflow',%s,'EXECUTE')",
            (f"control.{name}({signature})",),
        ).fetchone()[0]
        assert (
            admin.execute(
                "SELECT to_regprocedure(%s)", (f"control.weekly_skill_{name}({signature})",)
            ).fetchone()[0]
            is None
        )


def test_pending_intent_is_unknown_not_retried_and_independent_stages_continue(
    admin, api, workflow, identity_context, skill_setup
):
    cycle = skill_setup()
    open_skills(workflow, cycle)
    assert admit(workflow, cycle, "strategy_rebuild")[0]["state"] == "started"
    engine = WeeklySkills(
        workflow_connection, Recovery(), ports=local_skill_ports(workflow_connection)
    )
    asyncio.run(engine.run(cycle, "research"))
    stages = {s["stage"]: s for s in report(api, cycle, identity_context)["skill_stages"]}
    assert stages["strategy_rebuild"]["detail_code"] == "OUTCOME_UNKNOWN"
    assert stages["brief_proposals"]["detail_code"] == "DEPENDENCY_UNAVAILABLE"
    assert stages["import_bing"]["detail_code"] == "PORT_UNCONFIGURED"
    assert stages["strategy_rebuild"]["units"] == 1
    assert not admin.execute(
        "SELECT 1 FROM app.seo_strategy_snapshots WHERE tenant_id=%s", (cycle.site.tenant_id,)
    ).fetchall()


def test_weekly_strategy_sources_inherit_the_current_learning_projection(
    admin, workflow, skill_setup
):
    cycle = skill_setup()
    open_skills(workflow, cycle)
    admitted, handle = admit(workflow, cycle, "strategy_rebuild")
    assert admitted["state"] == "started"
    with _clean_transaction(workflow):
        sources = workflow.execute(
            "SELECT control.weekly_skill_seo_strategy_sources(%s,%s,%s)",
            (hash_session_token(handle), "test-generation-1", UUID(cycle.site.site_id)),
        ).fetchone()[0]
    assert sources["learning"]["measurements"] == []
    assert sources["learning"]["generations"] == []
    assert sources["learning"]["changes"] == []
    assert sources["learning"]["as_of"]
    assert not admin.execute(
        "SELECT has_function_privilege('signal_workflow',"
        "'control.weekly_skill_seo_strategy_base_sources(bytea,text,uuid)','EXECUTE')"
    ).fetchone()[0]


def test_cap_exhaustion_and_excluded_resources_are_unavailable(admin, api, workflow, skill_setup):
    cycle = skill_setup(cap=1, excluded=("/documents",))
    open_skills(workflow, cycle)
    assert admit(workflow, cycle, "strategy_rebuild")[0]["state"] == "started"
    # An admitted unit consumes the aggregate site cap even if its outcome is unknown.
    again, _ = admit(workflow, cycle, "strategy_rebuild")
    assert again["state"] == "unknown"
    assert (
        admin.execute(
            "SELECT total_count FROM app.autonomy_weekly_usage WHERE tenant_id=%s",
            (cycle.site.tenant_id,),
        ).fetchone()[0]
        == 1
    )


def test_draft_grant_and_model_cost_bound_are_required_before_work(workflow, skill_setup):
    cycle = skill_setup(drafts=False)
    open_skills(workflow, cycle)
    assert admit(workflow, cycle, "brief_proposals")[0]["reason"] == "WORK_TYPE_NOT_GRANTED"
    assert admit(workflow, cycle, "brain_refresh")[0]["reason"] == "COST_BOUND_UNCONFIGURED"


def test_skill_connection_rejects_approval_external_write_and_arbitrary_sql(workflow, skill_setup):
    cycle = skill_setup()
    open_skills(workflow, cycle)
    admitted, handle = admit(workflow, cycle, "strategy_rebuild")
    permit = SkillPermit(
        UUID(cycle.site.tenant_id),
        UUID(cycle.site.site_id),
        UUID(cycle.cycle_id),
        "strategy_rebuild",
        "test-generation-1",
        handle,
        tuple(admitted["plan"]),
        cycle.week_start,
    )
    connection = WeeklySkillConnection(workflow, permit)
    for statement in (
        "SELECT control.business_brain_approve(%s,%s,%s)",
        "SELECT control.content_writer_accept_brief(%s,%s,%s)",
        "SELECT control.seo_strategy_decide(%s,%s,%s)",
        "SELECT control.prepare_github_pr_write(%s,%s,%s)",
        "DELETE FROM app.content_briefs",
        "SELECT * FROM app.sessions",
        "SELECT control.seo_strategy_sources(%s,%s,%s); DELETE FROM app.content_briefs",
    ):
        with pytest.raises(PermissionError):
            connection.execute(statement)
    with pytest.raises(PermissionError):
        connection.execute(
            "SELECT control.seo_strategy_sources(%s,%s,%s)",
            (permit.handle_hash, permit.generation, uuid4()),
        )


def test_completed_results_require_intent_and_evidence_is_closed(workflow, skill_setup):
    from psycopg.errors import CheckViolation

    cycle = skill_setup()
    open_skills(workflow, cycle)
    args = (cycle.site.tenant_id, cycle.site.site_id, cycle.cycle_id, "strategy_rebuild")
    with _clean_transaction(workflow):
        assert (
            workflow.execute(
                "SELECT control.record_weekly_skill(%s,%s,%s,%s,%s,%s,%s)",
                (*args, "completed", "STRATEGY_RECORDED", Jsonb([])),
            ).fetchone()[0]
            == "intent_required"
        )
    admit(workflow, cycle, "strategy_rebuild")
    for refs in ([{"instructions": "untrusted"}], ["<script>untrusted</script>"], ["x" * 513]):
        with pytest.raises(CheckViolation), _clean_transaction(workflow):
            workflow.execute(
                "SELECT control.record_weekly_skill(%s,%s,%s,%s,%s,%s,%s)",
                (*args, "failed", "OUTCOME_UNKNOWN", Jsonb(refs)),
            )
