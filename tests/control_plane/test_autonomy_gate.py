"""Real PostgreSQL composition of deterministic policy, Jev evidence, and site caps."""

from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from psycopg import Error
from psycopg.errors import InsufficientPrivilege
from signal_core.autonomy_gate import (
    AutonomyCandidate,
    AutonomyGate,
    AutonomyGateUnavailable,
    deterministic_policy,
)
from signal_core.decision_contracts import (
    ChoiceAnswer,
    DecisionEvaluation,
    Recommendation,
)
from signal_core.decision_records import PostgresDecisionRecorder
from signal_core.jev_decisions import DecisionService, JevUnavailable
from signal_core.recipe_releases import VERIFIED_HOMEPAGE_METADATA_RELEASE_ID
from signal_core.recovery_authority import RecoveryGeneration
from signal_core.standing_authorization import (
    RecipeRange,
    StandingGrantRequest,
    grant_standing_authorization,
    revoke_standing_authorization,
)
from signal_core.weekly_control import read_weekly_report, set_site_paused
from signal_core.weekly_loop import WeeklyCycle, WeeklySite


@pytest.fixture
def anyio_backend():
    return "asyncio"


class GenerationSource:
    def __init__(self, value):
        self.value = value

    async def current_generation(self):
        if isinstance(self.value, Exception):
            raise self.value
        return RecoveryGeneration(self.value, 1)


class RotatingGenerationSource:
    def __init__(self, before, after):
        self.values = iter((before, after))

    async def current_generation(self):
        return RecoveryGeneration(next(self.values), 1)


class JevPrimary:
    def __init__(self, choice="ship", confidence=0.95, after=None, risk="low"):
        self.choice = choice
        self.confidence = confidence
        self.after = after
        self.risk = risk
        self.calls = 0

    async def evaluate(self, request):
        self.calls += 1
        if self.after is not None:
            self.after()
        if self.choice == "unavailable":
            raise JevUnavailable("JEV_RATE_LIMITED", retryable=True)
        return DecisionEvaluation(
            model_reported="jev-1.0.0",
            answers={
                "recommendation": ChoiceAnswer(
                    choice=self.choice,
                    probabilities={"ship": 0.95, "ask_owner": 0.04, "reject": 0.01},
                    confidence=self.confidence,
                ),
                "risk_class": ChoiceAnswer(
                    choice=self.risk,
                    probabilities={"low": 0.8, "moderate": 0.15, "high": 0.05},
                    confidence=0.9,
                ),
            },
            input_tokens=20,
            output_tokens=2,
        )


def _grant(admin, api, scope, identity_context, *, cap=2, spend=100):
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
            weekly_volume_caps={"draft_patch": cap},
            weekly_total_cap=cap,
            weekly_spend_cents=spend,
            excluded_paths=("/private",),
            starts_at=now,
            ends_at=now + timedelta(days=7),
            recovery_window_hours=24,
        ),
    )


def _candidate(scope, grant):
    return AutonomyCandidate(
        scope=scope,
        decision_id=uuid4(),
        operation_id=uuid4(),
        grant_id=grant.id,
        recipe_release_id=VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,
        sealed_revision_sha256=sha256(b"synthetic-sealed-revision").digest(),
        work_type="draft_patch",
        approval_class="A1",
        resource_path="/blog/post",
        cost_cents=25,
        summary="Synthetic metadata draft with no external write.",
    )


def _gate(workflow, scope, identity_context, primary):
    recorder = PostgresDecisionRecorder(workflow, scope)
    return AutonomyGate(
        workflow,
        GenerationSource(identity_context["generation"]),
        DecisionService(primary=primary, recorder=recorder),
    )


@pytest.mark.anyio
async def test_weekly_handoff_records_only_current_gate_ship_and_pause_blocks_late_handoff(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)
    day = admin.execute("SELECT (transaction_timestamp() AT TIME ZONE 'UTC')::date").fetchone()[0]
    cycle = WeeklyCycle.for_window(
        WeeklySite(str(scope.tenant_id), str(scope.site_id), str(grant.id)), day
    )
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
                identity_context["generation"],
            ),
        ).fetchone()[0]
        == "opened"
    )
    first = _candidate(scope, grant)
    second = _candidate(scope, grant)
    owner_review = replace(_candidate(scope, grant), sensitive_subject=True)
    gate = _gate(workflow, scope, identity_context, JevPrimary())
    assert (await gate.evaluate(first)).outcome is Recommendation.SHIP
    args = (scope.tenant_id, scope.site_id, cycle.week_start, cycle.cycle_id)
    assert (
        workflow.execute(
            "SELECT control.record_weekly_handoff(%s,%s,%s,%s,%s,%s)",
            (*args, first.decision_id, identity_context["generation"]),
        ).fetchone()[0]
        == "recorded"
    )
    assert (
        workflow.execute(
            "SELECT control.record_weekly_handoff(%s,%s,%s,%s,%s,%s)",
            (*args, first.decision_id, identity_context["generation"]),
        ).fetchone()[0]
        == "recorded"
    )
    assert (await gate.evaluate(second)).outcome is Recommendation.SHIP
    assert (await gate.evaluate(owner_review)).outcome is Recommendation.ASK_OWNER
    assert (
        workflow.execute(
            "SELECT control.record_weekly_stage(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                *args,
                grant.id,
                "gate",
                "completed",
                [
                    f"gate:{first.decision_id}",
                    f"gate:{second.decision_id}",
                    f"gate:{owner_review.decision_id}",
                ],
                "GATE_RECORDED",
            ),
        ).fetchone()[0]
        == "recorded"
    )
    set_site_paused(
        api,
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        recovery_generation=identity_context["generation"],
        paused=True,
    )
    assert (
        workflow.execute(
            "SELECT control.record_weekly_handoff(%s,%s,%s,%s,%s,%s)",
            (*args, second.decision_id, identity_context["generation"]),
        ).fetchone()[0]
        == "authority_changed"
    )
    assert (
        admin.execute(
            "SELECT count(*) FROM app.weekly_handoffs WHERE tenant_id=%s AND site_id=%s",
            (scope.tenant_id, scope.site_id),
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
    assert {item["decision_id"] for item in report["gate_decisions"]} == {
        str(first.decision_id),
        str(second.decision_id),
        str(owner_review.decision_id),
    }
    assert report["waiting_for_owner"] == [str(owner_review.decision_id)]


@pytest.mark.anyio
async def test_jev_ship_requires_current_grant_and_atomically_reserves_budget(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)
    candidate = _candidate(scope, grant)
    primary = JevPrimary()
    gate = _gate(workflow, scope, identity_context, primary)
    result = await gate.evaluate(candidate)
    assert result.outcome is Recommendation.SHIP
    assert result.reserved is True
    assert result.fallback is False
    assert primary.calls == 1
    assert admin.execute(
        "SELECT outcome,reason,model_decision_id,reserved_operation_id,provider "
        "FROM app.autonomy_gate_records WHERE id=%s",
        (candidate.decision_id,),
    ).fetchone() == (
        "ship",
        "JEV_SHIP_AND_BUDGET_RESERVED",
        candidate.decision_id,
        candidate.operation_id,
        "typesafe",
    )
    assert admin.execute(
        "SELECT total_count,spend_cents FROM app.autonomy_weekly_usage "
        "WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == (1, 25)
    duplicate = await gate.evaluate(candidate)
    assert duplicate.outcome is Recommendation.ASK_OWNER
    assert duplicate.reason == "HISTORICAL_GATE_ONLY"
    assert duplicate.reserved is False
    with pytest.raises(Error):
        admin.execute(
            "UPDATE app.autonomy_gate_records SET outcome='ship' WHERE id=%s",
            (candidate.decision_id,),
        )


@pytest.mark.anyio
async def test_low_confidence_and_jev_fallback_never_reserve(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)
    low = await _gate(workflow, scope, identity_context, JevPrimary(confidence=0.7)).evaluate(
        _candidate(scope, grant)
    )
    assert low.outcome is Recommendation.ASK_OWNER
    assert low.reserved is False
    fallback = await _gate(workflow, scope, identity_context, JevPrimary("unavailable")).evaluate(
        _candidate(scope, grant)
    )
    assert fallback.outcome is Recommendation.ASK_OWNER
    assert fallback.fallback is True
    assert admin.execute(
        "SELECT count(*) FROM app.autonomy_reservations WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == (0,)


@pytest.mark.anyio
async def test_high_jev_risk_reduces_ship_to_owner_review(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)
    result = await _gate(workflow, scope, identity_context, JevPrimary(risk="high")).evaluate(
        _candidate(scope, grant)
    )
    assert result.outcome is Recommendation.ASK_OWNER
    assert result.reason == "MODEL_RISK_REVIEW_REQUIRED"
    assert result.reserved is False
    assert admin.execute(
        "SELECT count(*) FROM app.autonomy_reservations WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == (0,)


@pytest.mark.anyio
async def test_deterministic_restrictions_run_before_jev_and_are_immutable(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)
    primary = JevPrimary()
    gate = _gate(workflow, scope, identity_context, primary)
    sensitive = _candidate(scope, grant)
    sensitive = replace(sensitive, sensitive_subject=True)
    result = await gate.evaluate(sensitive)
    assert result.outcome is Recommendation.ASK_OWNER
    assert result.reason == "SENSITIVE_SUBJECT"
    assert primary.calls == 0
    assert admin.execute(
        "SELECT model_decision_id,policy_outcome FROM app.autonomy_gate_records WHERE id=%s",
        (sensitive.decision_id,),
    ).fetchone() == (None, "ask_owner")
    forbidden = replace(_candidate(scope, grant), approval_class="A4")
    assert deterministic_policy(forbidden) == ("reject", "APPROVAL_CLASS_MISMATCH")
    assert (await gate.evaluate(forbidden)).outcome is Recommendation.REJECT
    assert primary.calls == 0
    with pytest.raises(ValueError):
        replace(_candidate(scope, grant), work_type="destructive")


@pytest.mark.anyio
async def test_grant_revocation_between_preflight_and_jev_blocks_ship(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)

    def revoke():
        revoke_standing_authorization(
            api,
            session_token=identity_context["session_token"],
            current_recovery_generation=identity_context["generation"],
            site_id=scope.site_id,
            grant_id=grant.id,
        )

    result = await _gate(workflow, scope, identity_context, JevPrimary(after=revoke)).evaluate(
        _candidate(scope, grant)
    )
    assert result.outcome is Recommendation.ASK_OWNER
    assert result.reason == "AUTHORITY_CHANGED"
    assert admin.execute(
        "SELECT count(*) FROM app.autonomy_reservations WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == (0,)


@pytest.mark.anyio
async def test_preflight_cap_and_recovery_failure_do_not_call_jev(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context, cap=1, spend=25)
    primary = JevPrimary()
    gate = _gate(workflow, scope, identity_context, primary)
    assert (await gate.evaluate(_candidate(scope, grant))).outcome is Recommendation.SHIP
    capped = await gate.evaluate(_candidate(scope, grant))
    assert capped.outcome is Recommendation.ASK_OWNER
    assert capped.reason == "WEEKLY_CAP_REACHED"
    assert primary.calls == 1
    unavailable = AutonomyGate(workflow, GenerationSource(RuntimeError()), gate.decision_service)
    with pytest.raises(AutonomyGateUnavailable):
        await unavailable.evaluate(_candidate(scope, grant))
    assert primary.calls == 1


@pytest.mark.anyio
async def test_budget_consumed_after_preflight_cannot_be_split_into_second_ship(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context, cap=1, spend=25)
    candidate = _candidate(scope, grant)

    def consume_budget():
        assert workflow.execute(
            "SELECT control.reserve_standing_budget(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                grant.id,
                identity_context["generation"],
                VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,
                "draft_patch",
                "/blog/other",
                uuid4(),
                sha256(b"synthetic-other-revision").digest(),
                25,
            ),
        ).fetchone() == ("reserved",)

    result = await _gate(
        workflow, scope, identity_context, JevPrimary(after=consume_budget)
    ).evaluate(candidate)
    assert result.outcome is Recommendation.ASK_OWNER
    assert result.reason == "BUDGET_OR_AUTHORITY_CHANGED"
    assert result.reserved is False
    assert admin.execute(
        "SELECT total_count,spend_cents FROM app.autonomy_weekly_usage "
        "WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == (1, 25)


@pytest.mark.anyio
async def test_external_generation_rotation_after_jev_never_ships(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)
    gate = AutonomyGate(
        workflow,
        RotatingGenerationSource(identity_context["generation"], "synthetic-new-generation"),
        DecisionService(primary=JevPrimary(), recorder=PostgresDecisionRecorder(workflow, scope)),
    )
    result = await gate.evaluate(_candidate(scope, grant))
    assert result.outcome is Recommendation.ASK_OWNER
    assert result.reason == "AUTHORITY_CHANGED"
    assert admin.execute(
        "SELECT count(*) FROM app.autonomy_reservations WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == (0,)


@pytest.mark.anyio
async def test_wrong_scope_and_bad_path_are_deterministic_denials(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)
    primary = JevPrimary()
    gate = _gate(workflow, scope, identity_context, primary)
    denied = await gate.evaluate(replace(_candidate(scope, grant), resource_path="/private/post"))
    assert denied.outcome is Recommendation.ASK_OWNER
    assert denied.reason == "GRANT_UNAVAILABLE"
    assert primary.calls == 0
    with pytest.raises(ValueError):
        replace(_candidate(scope, grant), resource_path="/private%2Fpost")


def test_gate_table_is_forced_rls_and_unrecorded_model_cannot_ship(
    admin, api, workflow, scopes, identity_context
):
    scope = scopes[0]
    grant = _grant(admin, api, scope, identity_context)
    candidate = _candidate(scope, grant)
    assert workflow.execute(
        "SELECT * FROM control.finalize_autonomy_gate(" + ",".join(["%s"] * 14) + ")",
        (
            scope.tenant_id,
            scope.site_id,
            candidate.decision_id,
            candidate.operation_id,
            grant.id,
            identity_context["generation"],
            candidate.recipe_release_id,
            candidate.sealed_revision_sha256,
            sha256(b"synthetic-missing-model-input").digest(),
            candidate.work_type,
            candidate.resource_path,
            candidate.cost_cents,
            "eligible",
            "DETERMINISTIC_SCOPE_PASSED",
        ),
    ).fetchone() == ("reject", "MODEL_EVIDENCE_UNAVAILABLE", False)
    assert admin.execute(
        "SELECT count(*) FROM app.autonomy_gate_records WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == (0,)
    with pytest.raises(InsufficientPrivilege):
        workflow.execute("SELECT * FROM app.autonomy_gate_records")
    assert admin.execute(
        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
        "WHERE oid='app.autonomy_gate_records'::regclass"
    ).fetchone() == (True, True)
