import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.model_budget import ModelBudgetUnavailable, PostgresModelBudget
from signal_core.model_reasoning import BrainModelResult, ModelUsage
from signal_core.model_roles import RoleModel
from test_business_brain import owner


def budget_for(connection, context, scope):
    return PostgresModelBudget(
        connection, context["session_token"], context["generation"], scope.site_id
    )


def reserve(budget, *, amount=100, operation_id=None):
    config = RoleModel()
    operation_id = operation_id or uuid4()
    outcome = budget.call(
        "reserve",
        operation_id,
        hashlib.sha256(b"synthetic-model-request").digest(),
        "claim_check",
        Jsonb({**asdict(config), "release": config.release}),
        amount,
    )
    return operation_id, outcome


def test_monthly_reservation_dispatch_settlement_warning_and_exhaustion(
    admin, api, scopes, identity_context
):
    owner(admin, identity_context)
    budget = budget_for(api, identity_context, scopes[0])
    assert budget.call("read")["cap_micros"] == 25000000
    assert budget.call("set_cap", 100) == "updated"
    call_id, outcome = reserve(budget, amount=80)
    assert outcome == "reserved"
    assert budget.call("read")["warning"] is True
    assert reserve(budget, amount=21)[1] == "exhausted"
    assert reserve(budget, amount=80, operation_id=call_id)[1] == "replayed"
    assert reserve(budget, amount=79, operation_id=call_id)[1] == "conflict"
    body = b"synthetic-model-request"
    budget.dispatch(call_id, body)
    with pytest.raises(ModelBudgetUnavailable, match="DISPATCH_DENIED"):
        budget.dispatch(call_id, body)
    result = BrainModelResult(
        {}, "resp_synthetic_budget", "gpt-6-luna", ModelUsage(100, 40, 140, 0)
    )
    budget.finish(call_id, model=RoleModel(), result=result, response_sha256="a" * 64)
    budget.finish(call_id, model=RoleModel(), result=result, response_sha256="a" * 64)
    assert budget.call("read")["used_micros"] == RoleModel().cost(result.usage)
    assert reserve(budget, amount=100)[1] == "exhausted"
    assert budget.call("set_cap", 0) == "updated"
    assert budget.call("read")["state"] == "unavailable"


def test_unknown_outcome_holds_and_new_dispatch_is_impossible_after_cap_reduction(
    admin, api, scopes, identity_context
):
    owner(admin, identity_context)
    budget = budget_for(api, identity_context, scopes[0])
    call_id, outcome = reserve(budget)
    assert outcome == "reserved"
    budget.dispatch(call_id, b"synthetic-model-request")
    assert budget.call("read")["used_micros"] == 100
    assert reserve(budget, operation_id=call_id)[1] == "replayed"
    other, _ = reserve(budget)
    assert budget.call("set_cap", 150) == "updated"
    with pytest.raises(ModelBudgetUnavailable, match="DISPATCH_DENIED"):
        budget.dispatch(other, b"synthetic-model-request")
    assert budget.call("read")["used_micros"] == 200


@pytest.mark.parametrize("race", range(5))
def test_concurrent_site_reservations_cannot_split_the_cap(
    admin, api, scopes, identity_context, race
):
    from threading import Barrier

    barrier = Barrier(2)
    owner(admin, identity_context)
    assert budget_for(api, identity_context, scopes[0]).call("set_cap", 100) == "updated"

    def attempt(_):
        with psycopg.connect(os.environ["SIGNAL_TEST_API_DSN"], autocommit=True) as connection:
            barrier.wait(timeout=10)
            return reserve(budget_for(connection, identity_context, scopes[0]))[1]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["exhausted", "reserved"]
    assert budget_for(api, identity_context, scopes[0]).call("read")["used_micros"] == 100


@pytest.mark.parametrize("role", ["viewer", "analyst", "editor", "approver", "admin"])
def test_nonowner_cannot_reserve_read_or_set_model_budget(
    admin, api, scopes, identity_context, role
):
    admin.execute(
        "UPDATE app.memberships SET role_key=%s WHERE id=%s",
        (role, identity_context["membership_id"]),
    )
    budget = budget_for(api, identity_context, scopes[0])
    for name, args in (("read", ()), ("set_cap", (100,))):
        with pytest.raises(ModelBudgetUnavailable, match="ACCESS_DENIED"):
            budget.call(name, *args)
    with pytest.raises(ModelBudgetUnavailable, match="ACCESS_DENIED"):
        reserve(budget)


def test_tenant_role_negative_and_forced_rls_function_only_storage(
    admin, api, scopes, identity_context, workflow
):
    owner(admin, identity_context)
    budget = budget_for(api, identity_context, scopes[0])
    for scope in scopes[1:]:
        with pytest.raises(ModelBudgetUnavailable, match="ACCESS_DENIED"):
            replace(budget, site_id=scope.site_id).call("read")
    for table in (
        "model_budget_caps",
        "model_budget_calls",
        "model_budget_dispatches",
        "model_budget_receipts",
    ):
        assert admin.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
            ("app." + table,),
        ).fetchone() == (True, True)
        for role in ("signal_api", "signal_identity", "signal_workflow", "signal_bootstrap"):
            assert not admin.execute(
                "SELECT has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE,SELECT')",
                (role, "app." + table),
            ).fetchone()[0]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        workflow.execute(
            "SELECT control.model_budget_read(%s,%s,%s)",
            (b"a" * 32, "synthetic-generation", scopes[0].site_id),
        )


def test_dispatch_rechecks_current_owner_and_exact_digest(admin, api, scopes, identity_context):
    owner(admin, identity_context)
    budget = budget_for(api, identity_context, scopes[0])
    call_id, _ = reserve(budget)
    with pytest.raises(ModelBudgetUnavailable, match="DISPATCH_DENIED"):
        budget.dispatch(call_id, b"different-synthetic-request")
    admin.execute(
        "UPDATE app.memberships SET role_key='analyst' WHERE id=%s",
        (identity_context["membership_id"],),
    )
    with pytest.raises(ModelBudgetUnavailable, match="ACCESS_DENIED"):
        budget.dispatch(call_id, b"synthetic-model-request")


def test_prior_month_holds_cannot_authorize_egress_or_charge_this_month(
    admin, api, scopes, identity_context
):
    owner(admin, identity_context)
    budget = budget_for(api, identity_context, scopes[0])
    call_id = uuid4()
    admin.execute(
        "INSERT INTO app.model_budget_calls(tenant_id,site_id,id,generation,request_sha256,"
        "role,model_release,reserved_micros,month) "
        "VALUES(%s,%s,%s,%s,%s,'claim_check','{}',100,"
        "date_trunc('month',now() AT TIME ZONE 'UTC')::date-interval '1 month')",
        (
            scopes[0].tenant_id,
            scopes[0].site_id,
            call_id,
            identity_context["generation"],
            hashlib.sha256(b"synthetic-model-request").digest(),
        ),
    )
    assert budget.call("read")["used_micros"] == 0
    with pytest.raises(ModelBudgetUnavailable, match="DISPATCH_DENIED"):
        budget.dispatch(call_id, b"synthetic-model-request")


def test_model_hold_and_receipt_are_immutable_and_overspend_is_not_discarded(
    admin, api, scopes, identity_context
):
    owner(admin, identity_context)
    budget = budget_for(api, identity_context, scopes[0])
    budget.call("set_cap", 100)
    call_id, _ = reserve(budget, amount=100)
    budget.dispatch(call_id, b"synthetic-model-request")
    assert budget.call("finish", call_id, 200, b"a" * 32, Jsonb({"synthetic": True})) == "completed"
    assert (
        budget.call("read")["used_micros"] == 200 and budget.call("read")["state"] == "unavailable"
    )
    for table, key in (
        ("model_budget_calls", "id"),
        ("model_budget_dispatches", "call_id"),
        ("model_budget_receipts", "call_id"),
    ):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(f"DELETE FROM app.{table} WHERE {key}=%s", (call_id,))
