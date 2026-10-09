"""Durable alerts and owner projections on real PostgreSQL runtime roles."""

from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.commands import accept_snapshot
from signal_core.email_notifications import EmailNotifications
from signal_core.health_monitoring import CHECKS, evaluate
from signal_core.session_tokens import hash_session_token
from signal_core.smtp_submission import SmtpConfiguration
from test_email_notifications import _configure, _login
from test_gsc_binding import begin, owner_and_verified_origin, session_args, site_origin


def snapshot(**conditions):
    now = datetime.now(UTC)
    return Jsonb(
        [
            asdict(evaluate(key, {"condition": conditions.get(key, "healthy")}, now))
            for key in CHECKS
        ]
    )


def record(connection, scope, checks, generation="test-generation-1"):
    return connection.execute(
        "SELECT control.record_health_checks(%s,%s,%s,%s)",
        (scope.tenant_id, scope.site_id, generation, checks),
    ).fetchone()[0]


def test_facts_missing_providers_are_unknown_and_no_egress_is_created(scheduler, admin, scopes):
    scope = scopes[0]
    before = admin.execute("SELECT count(*) FROM app.egress_operations").fetchone()[0]
    facts = scheduler.execute(
        "SELECT control.health_facts(%s,%s)", (scope.tenant_id, scope.site_id)
    ).fetchone()[0]
    assert facts["outbox_backlog"]["value"] == 0
    assert facts["binding_gsc"]["condition"] == "not_configured"
    assert facts["budget_model"]["condition"] == "not_configured"
    assert admin.execute("SELECT count(*) FROM app.egress_operations").fetchone()[0] == before


def test_alert_dedup_rate_limits_and_delayed_transition(scheduler, admin, scopes):
    scope = scopes[0]
    assert record(scheduler, scope, snapshot())["alerts"] == 0
    assert record(scheduler, scope, snapshot(binding_gsc="revoked"))["alerts"] == 1
    assert record(scheduler, scope, snapshot(binding_gsc="revoked"))["alerts"] == 0
    assert record(scheduler, scope, snapshot(binding_gsc="expired"))["alerts"] == 0
    other = scopes[1]
    record(scheduler, other, snapshot())
    obs = admin.execute(
        "INSERT INTO app.health_observations(tenant_id,site_id,check_key,state,reason,checked_at) "
        "VALUES(%s,%s,'binding_gsc','critical','revoked',"
        "transaction_timestamp()-interval '2 hours') RETURNING id",
        (other.tenant_id, other.site_id),
    ).fetchone()[0]
    admin.execute(
        "INSERT INTO app.health_alerts(tenant_id,site_id,observation_id,created_at) "
        "VALUES(%s,%s,%s,transaction_timestamp()-interval '2 hours')",
        (other.tenant_id, other.site_id, obs),
    )
    assert record(scheduler, other, snapshot(binding_gsc="expired"))["alerts"] == 1


def test_all_bad_checks_are_site_rate_limited(scheduler, scopes):
    assert record(scheduler, scopes[0], snapshot(**{k: "sealed" for k in CHECKS}))["alerts"] == 6
    assert record(scheduler, scopes[0], snapshot(**{k: "expired" for k in CHECKS}))["alerts"] == 0


def test_owner_projection_tenant_role_and_stale_negatives(
    scheduler, api, admin, scopes, identity_context
):
    scope = scopes[0]
    admin.execute(
        "UPDATE app.memberships SET role_key='owner' WHERE tenant_id=%s", (scope.tenant_id,)
    )
    assert record(scheduler, scope, snapshot())["alerts"] == 0
    token = hash_session_token(identity_context["session_token"])
    args = (token, scope.site_id, identity_context["generation"])
    result = api.execute("SELECT control.read_health_checks(%s,%s,%s)", args).fetchone()[0]
    assert len(result["checks"]) == 20
    assert (
        api.execute(
            "SELECT control.read_health_checks(%s,%s,%s)", (token, scopes[2].site_id, args[2])
        ).fetchone()[0]
        is None
    )
    assert (
        api.execute(
            "SELECT control.read_health_checks(%s,%s,%s)",
            (token, scope.site_id, "synthetic-stale-generation"),
        ).fetchone()[0]
        is None
    )
    admin.execute(
        "UPDATE app.memberships SET role_key='viewer' WHERE tenant_id=%s", (scope.tenant_id,)
    )
    assert api.execute("SELECT control.read_health_checks(%s,%s,%s)", args).fetchone()[0] is None


def test_function_only_forced_rls_and_immutable(scheduler, api, identity, admin, scopes):
    record(scheduler, scopes[0], snapshot())
    for connection in (scheduler, api, identity):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM app.health_observations")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        record(api, scopes[0], snapshot())
    assert admin.execute(
        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
        "WHERE oid='app.health_observations'::regclass"
    ).fetchone()[0]
    with pytest.raises(psycopg.Error):
        admin.execute(
            "DELETE FROM app.health_observations WHERE tenant_id=%s", (scopes[0].tenant_id,)
        )


@pytest.mark.parametrize("verified", [True, False])
def test_revoked_binding_alert_email_only_verified_owner(
    scheduler, api, identity, admin, scopes, identity_context, verified
):
    scope = scopes[0]
    admin.execute(
        "UPDATE app.memberships SET role_key='owner' WHERE tenant_id=%s", (scope.tenant_id,)
    )
    admin.execute("UPDATE app.sites SET state='active' WHERE tenant_id=%s", (scope.tenant_id,))
    cfg = SmtpConfiguration(
        "smtp.example.invalid",
        2525,
        "starttls",
        "signal@example.invalid",
        "https://dashboard.example.invalid",
        20,
    )
    _configure(admin, cfg)
    context = _login(
        identity,
        admin,
        scopes,
        identity_context,
        address="owner@example.invalid" if verified else None,
    )
    preference = EmailNotifications(api, context["generation"])
    assert preference.opt_in(
        context["session_token"], scope.site_id, enabled=True, address="owner@example.invalid"
    ) == ("enabled" if verified else "identity_unverified")
    assert (
        record(scheduler, scope, snapshot(binding_gsc="revoked"), context["generation"])["alerts"]
        == 1
    )
    count = admin.execute(
        "SELECT count(*) FROM app.email_outbox WHERE tenant_id=%s AND site_id=%s "
        "AND category='health_alert'",
        (scope.tenant_id, scope.site_id),
    ).fetchone()[0]
    assert count == int(verified)
    assert (
        record(scheduler, scope, snapshot(binding_gsc="revoked"), context["generation"])["alerts"]
        == 0
    )


def test_bad_snapshot_rolls_back(scheduler, admin, scopes):
    with pytest.raises(psycopg.Error):
        record(scheduler, scopes[0], Jsonb([]))
    incoherent = snapshot().obj
    incoherent[0]["reason"] = "probe_unavailable"
    with pytest.raises(psycopg.errors.CheckViolation):
        record(scheduler, scopes[0], Jsonb(incoherent))
    assert (
        admin.execute(
            "SELECT count(*) FROM app.health_observations WHERE tenant_id=%s",
            (scopes[0].tenant_id,),
        ).fetchone()[0]
        == 0
    )


def test_future_outbox_work_has_zero_age(scheduler, api, scopes):
    scope = scopes[0]
    accept_snapshot(api, scope, actor_service="synthetic-test", idempotency_key="future-health")
    item = scheduler.execute(
        "SELECT outbox_id,attempt_count FROM control.claim_outbox_batch(%s,%s,1,60)",
        (scope.tenant_id, "synthetic-health"),
    ).fetchone()
    scheduler.execute(
        "SELECT * FROM control.reschedule_outbox(%s,%s,%s,%s,3600)",
        (scope.tenant_id, item[0], "synthetic-health", item[1]),
    )
    facts = scheduler.execute(
        "SELECT control.health_facts(%s,%s)", (scope.tenant_id, scope.site_id)
    ).fetchone()[0]
    assert facts["outbox_age"]["value"] == 0


def test_actual_revoked_gsc_binding_raises_committed_alert(
    scheduler, admin, identity, scopes, identity_context
):
    scope = scopes[0]
    owner_and_verified_origin(admin, identity, identity_context, scope.site_id)
    attempt_id, binding_id = uuid4(), uuid4()
    state = ("synthetic-health-" + uuid4().hex).encode()
    assert begin(identity, identity_context, scope.site_id, attempt_id, state) == "created"
    args = (*session_args(identity_context, scope.site_id), attempt_id)
    assert identity.execute(
        "SELECT outcome FROM control.consume_gsc_oauth_attempt(%s,%s,%s,%s,%s,%s)",
        (*args, sha256(state).digest(), "https://signal.example/oauth/gsc/callback"),
    ).fetchone() == ("consumed",)
    property_name = site_origin(scope.site_id) + "/"
    assert identity.execute(
        "SELECT control.stage_gsc_oauth_attempt(%s,%s,%s,%s,%s,%s)",
        (
            *args,
            f"secret://gsc/{attempt_id}",
            Jsonb(
                [{"resource_name": property_name, "property_type": "url_prefix", "eligible": True}]
            ),
        ),
    ).fetchone() == ("staged",)
    assert identity.execute(
        "SELECT control.confirm_gsc_binding(%s,%s,%s,%s,%s,%s,%s)",
        (*args, binding_id, uuid4(), property_name),
    ).fetchone() == ("bound",)
    assert identity.execute(
        "SELECT outcome FROM control.revoke_gsc_binding(%s,%s,%s,%s,%s)",
        (*session_args(identity_context, scope.site_id), binding_id, uuid4()),
    ).fetchone() == ("revoked",)
    facts = scheduler.execute(
        "SELECT control.health_facts(%s,%s)", (scope.tenant_id, scope.site_id)
    ).fetchone()[0]
    assert facts["binding_gsc"]["condition"] == "revoked"
    checks = snapshot(binding_gsc=facts["binding_gsc"]["condition"])
    assert record(scheduler, scope, checks)["alerts"] == 1


def test_stale_health_snapshot_read_is_unknown(api, admin, scopes, identity_context):
    scope = scopes[0]
    admin.execute(
        "UPDATE app.memberships SET role_key='owner' WHERE tenant_id=%s", (scope.tenant_id,)
    )
    admin.execute(
        "INSERT INTO app.health_observations(tenant_id,site_id,check_key,state,reason,checked_at) "
        "VALUES(%s,%s,'temporal','ok','reachable',transaction_timestamp()-interval '6 minutes')",
        (scope.tenant_id, scope.site_id),
    )
    result = api.execute(
        "SELECT control.read_health_checks(%s,%s,%s)",
        (
            hash_session_token(identity_context["session_token"]),
            scope.site_id,
            identity_context["generation"],
        ),
    ).fetchone()[0]
    assert result["checks"][0]["state"] == "unknown"
    assert result["checks"][0]["reason"] == "stale_evidence"
