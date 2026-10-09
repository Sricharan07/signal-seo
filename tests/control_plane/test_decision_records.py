from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.database import scoped_transaction
from signal_core.decision_contracts import (
    ChoiceQuestion,
    DecisionRecommendation,
    DecisionRequest,
    Recommendation,
)
from signal_core.decision_records import (
    DecisionRecordConflict,
    PostgresDecisionRecorder,
)


def request(*, ceiling: Recommendation = Recommendation.SHIP) -> DecisionRequest:
    return DecisionRequest(
        decision_id=uuid4(),
        purpose="autonomy.triage",
        state={"finding_id": str(uuid4()), "risk": "low"},
        questions={
            "recommendation": ChoiceQuestion(
                "What is the safest next step?",
                {
                    "ship": "Recommend continuation.",
                    "ask_owner": "Require owner review.",
                    "reject": "Stop the work.",
                },
            )
        },
        threshold=0.8,
        policy_ceiling=ceiling,
    )


def result(
    decision_request: DecisionRequest,
    *,
    recommendation: Recommendation = Recommendation.SHIP,
    confidence: float = 0.91,
) -> DecisionRecommendation:
    probabilities = {"ship": 0.9, "ask_owner": 0.05, "reject": 0.05}
    return DecisionRecommendation(
        decision_id=decision_request.decision_id,
        recommendation=recommendation,
        policy_ceiling=decision_request.policy_ceiling,
        provider="typesafe",
        model_requested="jev-latest",
        model_reported="jev-1.13.0",
        answers={
            "recommendation": {
                "type": "choice",
                "choice": recommendation.value,
                "probabilities": probabilities,
                "confidence": confidence,
            }
        },
        probabilities=probabilities,
        confidence=confidence,
        threshold=decision_request.threshold,
        fallback=False,
        fallback_reason=None,
    )


def test_decision_record_is_immutable_exact_and_idempotent(admin, workflow, scopes) -> None:
    decision_request = request()
    recommendation = result(decision_request)
    recorder = PostgresDecisionRecorder(workflow, scopes[0])

    first = recorder.record(decision_request, recommendation)
    duplicate = recorder.record(decision_request, recommendation)

    assert first.decision_id == decision_request.decision_id
    assert first.duplicate is False
    assert duplicate.decision_id == first.decision_id
    assert duplicate.created_at == first.created_at
    assert duplicate.duplicate is True
    stored = admin.execute(
        "SELECT purpose, provider, model_requested, model_reported, "
        "encode(input_sha256, 'hex'), encode(question_schema_sha256, 'hex'), "
        "answer, probabilities, confidence, threshold, policy_ceiling, outcome, "
        "fallback, fallback_reason FROM app.decision_records "
        "WHERE tenant_id = %s AND site_id = %s AND id = %s",
        (scopes[0].tenant_id, scopes[0].site_id, decision_request.decision_id),
    ).fetchone()
    assert stored[:4] == ("autonomy.triage", "typesafe", "jev-latest", "jev-1.13.0")
    assert stored[4] == decision_request.input_sha256.hex()
    assert stored[5] == decision_request.question_schema_sha256.hex()
    assert stored[6]["recommendation"]["choice"] == "ship"
    assert stored[7] == dict(recommendation.probabilities)
    stored_tail = tuple(
        float(value) if index in {0, 1} else value for index, value in enumerate(stored[8:])
    )
    assert stored_tail == (
        recommendation.confidence,
        recommendation.threshold,
        "ship",
        "ship",
        False,
        None,
    )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.decision_records SET outcome = 'reject' "
            "WHERE tenant_id = %s AND site_id = %s AND id = %s",
            (scopes[0].tenant_id, scopes[0].site_id, decision_request.decision_id),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "DELETE FROM app.decision_records WHERE tenant_id = %s AND site_id = %s AND id = %s",
            (scopes[0].tenant_id, scopes[0].site_id, decision_request.decision_id),
        )


def test_decision_id_conflict_cannot_rebind_immutable_facts(workflow, scopes) -> None:
    decision_request = request()
    recommendation = result(decision_request)
    recorder = PostgresDecisionRecorder(workflow, scopes[0])
    recorder.record(decision_request, recommendation)

    with pytest.raises(DecisionRecordConflict):
        recorder.record(
            decision_request,
            result(decision_request, confidence=0.92),
        )


def test_function_rejects_scope_mismatch_and_widening(workflow, scopes) -> None:
    decision_request = request(ceiling=Recommendation.ASK_OWNER)
    recommendation = result(decision_request, recommendation=Recommendation.ASK_OWNER)
    recorder = PostgresDecisionRecorder(workflow, scopes[0])
    recorder.record(decision_request, recommendation)

    base = (
        uuid4(),
        "autonomy.triage",
        "typesafe",
        "jev-latest",
        "jev-1.13.0",
        b"a" * 32,
        b"b" * 32,
        Jsonb(
            {
                "recommendation": {
                    "type": "choice",
                    "choice": "ask_owner",
                    "probabilities": {
                        "ship": 0.0,
                        "ask_owner": 1.0,
                        "reject": 0.0,
                    },
                    "confidence": 1.0,
                }
            }
        ),
        Jsonb({"ship": 0.0, "ask_owner": 1.0, "reject": 0.0}),
        Decimal("1.0"),
        Decimal("0.8"),
        "ask_owner",
        "ask_owner",
        False,
        None,
    )
    statement = (
        "SELECT * FROM control.record_decision_recommendation(" + ", ".join(["%s"] * 17) + ")"
    )
    with scoped_transaction(workflow, scopes[0]):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            workflow.execute(
                statement,
                (scopes[1].tenant_id, scopes[1].site_id, *base),
            )
    widening = list(base)
    widening[12] = "ship"
    with scoped_transaction(workflow, scopes[0]):
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            workflow.execute(
                statement,
                (scopes[0].tenant_id, scopes[0].site_id, *widening),
            )


def test_function_rejects_internally_inconsistent_answer(workflow, scopes) -> None:
    statement = (
        "SELECT * FROM control.record_decision_recommendation(" + ", ".join(["%s"] * 17) + ")"
    )
    inconsistent = (
        uuid4(),
        "autonomy.triage",
        "typesafe",
        "jev-latest",
        "jev-1.13.0",
        b"a" * 32,
        b"b" * 32,
        Jsonb(
            {
                "recommendation": {
                    "type": "choice",
                    "choice": "ask_owner",
                    "probabilities": {
                        "ship": 0.0,
                        "ask_owner": 1.0,
                        "reject": 0.0,
                    },
                    "confidence": 0.5,
                }
            }
        ),
        Jsonb({"ship": 0.0, "ask_owner": 1.0, "reject": 0.0}),
        Decimal("1.0"),
        Decimal("0.8"),
        "ask_owner",
        "ask_owner",
        False,
        None,
    )

    with scoped_transaction(workflow, scopes[0]):
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            workflow.execute(
                statement,
                (scopes[0].tenant_id, scopes[0].site_id, *inconsistent),
            )


def test_decision_table_is_forced_rls_and_function_only(admin, workflow, scopes) -> None:
    row = admin.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'app.decision_records'::regclass"
    ).fetchone()
    assert row == (True, True)
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
        assert not admin.execute(
            "SELECT has_table_privilege('signal_workflow', 'app.decision_records', %s)",
            (privilege,),
        ).fetchone()[0]
    assert admin.execute(
        "SELECT has_function_privilege('signal_workflow', "
        "'control.record_decision_recommendation(uuid,uuid,uuid,text,text,text,text,"
        "bytea,bytea,jsonb,jsonb,numeric,numeric,text,text,boolean,text)', 'EXECUTE')"
    ).fetchone() == (True,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        workflow.execute("SELECT count(*) FROM app.decision_records")


def test_fallback_record_is_explicit_and_cannot_claim_ship(admin, workflow, scopes) -> None:
    decision_request = request()
    recommendation = DecisionRecommendation(
        decision_id=decision_request.decision_id,
        recommendation=Recommendation.ASK_OWNER,
        policy_ceiling=decision_request.policy_ceiling,
        provider="deterministic_fallback",
        model_requested="deterministic-v1",
        model_reported="deterministic-v1",
        answers={
            "recommendation": {
                "type": "fallback",
                "recommendation": "ask_owner",
                "reason_code": "OWNER_REVIEW_REQUIRED",
            }
        },
        probabilities={},
        confidence=0,
        threshold=decision_request.threshold,
        fallback=True,
        fallback_reason="JEV_UNCONFIGURED",
    )

    PostgresDecisionRecorder(workflow, scopes[0]).record(decision_request, recommendation)

    assert admin.execute(
        "SELECT provider, outcome, fallback, fallback_reason "
        "FROM app.decision_records WHERE tenant_id = %s AND site_id = %s AND id = %s",
        (scopes[0].tenant_id, scopes[0].site_id, decision_request.decision_id),
    ).fetchone() == (
        "deterministic_fallback",
        "ask_owner",
        True,
        "JEV_UNCONFIGURED",
    )
