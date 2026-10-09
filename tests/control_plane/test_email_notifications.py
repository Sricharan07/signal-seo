"""Recipient and outbox safety on real PostgreSQL non-owner runtime roles."""

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx2
import psycopg
import pytest
from signal_core.email_notifications import EmailNotifications
from signal_core.oidc_protocol import VerifiedOidcIdentity, _verified_email_claim
from signal_core.session_issuance import issue_identity_session, issue_tenant_session
from signal_core.session_management import select_session_site
from signal_core.shared_egress import SharedSmtpEgress
from signal_core.smtp_submission import (
    OpenBaoSmtpCredential,
    PinnedSmtpSubmitter,
    SmtpConfiguration,
)
from test_weekly_loop import _cycle, _grant, _open


def _login(
    identity,
    admin,
    scopes,
    context,
    *,
    address="owner@example.invalid",
    age=0,
    issued_offset=0,
    clock_offset=0,
):
    issuer, subject = admin.execute(
        "SELECT oidc_issuer,oidc_subject FROM control.users WHERE id=%s", (context["user_id"],)
    ).fetchone()
    now = datetime.now(UTC)
    proof = VerifiedOidcIdentity(
        issuer,
        subject,
        "signal-dashboard",
        int((now + timedelta(seconds=issued_offset)).timestamp()),
        int((now + timedelta(minutes=5)).timestamp()),
        int((now - timedelta(minutes=age)).timestamp()),
        None,
        "1",
        address,
    )
    global_session = issue_identity_session(
        identity,
        identity=proof,
        current_recovery_generation=context["generation"],
        now=now + timedelta(seconds=clock_offset),
    )
    tenant_session = issue_tenant_session(
        identity,
        identity_session_token=global_session.token,
        requested_tenant_id=scopes[0].tenant_id,
        current_recovery_generation=context["generation"],
    )
    select_session_site(
        identity,
        session_token=tenant_session.token,
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=context["generation"],
        expected_session_version=1,
    )
    return {
        **context,
        "session_token": tenant_session.token,
        "identity_session_id": global_session.id,
    }


def _configure(admin, configuration):
    admin.execute(
        "INSERT INTO control.email_smtp_configuration VALUES(true,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(singleton) DO UPDATE SET host=EXCLUDED.host,port=EXCLUDED.port,"
        "tls_mode=EXCLUDED.tls_mode,"
        "sender=EXCLUDED.sender,dashboard_origin=EXCLUDED.dashboard_origin,daily_cap=EXCLUDED.daily_cap,"
        "configuration_sha256=EXCLUDED.configuration_sha256",
        (
            configuration.host,
            configuration.port,
            configuration.tls_mode,
            configuration.sender,
            configuration.dashboard_origin,
            configuration.daily_cap,
            configuration.sha256,
            "secret://email/smtp/default",
        ),
    )


def _setup(
    admin,
    api,
    identity,
    workflow,
    scopes,
    context,
    *,
    address="owner@example.invalid",
    age=0,
    enabled=True,
    port=None,
    cap=20,
):
    scope = scopes[0]
    cfg = SmtpConfiguration(
        "smtp.example.invalid",
        port or 20000 + uuid4().int % 30000,
        "starttls",
        "signal@example.invalid",
        "https://dashboard.example.invalid",
        cap,
    )
    _configure(admin, cfg)
    context = _login(identity, admin, scopes, context, address=address, age=age)
    grant = _grant(admin, api, scope, context)
    service = EmailNotifications(api, context["generation"])
    state = service.opt_in(
        context["session_token"],
        scope.site_id,
        enabled=enabled,
        address=address or "owner@example.invalid",
    )
    if state == "identity_unverified":
        service.opt_in(context["session_token"], scope.site_id, enabled=False, address=None)
    cycle = _cycle(scope, grant, admin)
    assert _open(workflow, cycle) == "opened"
    assert (
        workflow.execute(
            "SELECT control.close_weekly_cycle(%s,%s,%s,%s,'completed','CYCLE_REPORTED')",
            (scope.tenant_id, scope.site_id, cycle.week_start, cycle.cycle_id),
        ).fetchone()[0]
        == "closed"
    )
    outbox_id = admin.execute(
        "SELECT id FROM app.email_outbox WHERE tenant_id=%s AND site_id=%s "
        "AND event_id=%s AND category='weekly_report'",
        (scope.tenant_id, scope.site_id, cycle.cycle_id),
    ).fetchone()[0]
    return context, service, cfg, outbox_id, cycle, state


def _begin(identity, outbox_id, generation):
    return identity.execute(
        "SELECT control.begin_email_dispatch(%s,%s,%s,100)",
        (outbox_id, generation, hashlib.sha256(b"synthetic-message").digest()),
    ).fetchone()[0]


def _finish(identity, outbox_id, outcome):
    return identity.execute(
        "SELECT control.finish_email_dispatch(%s,%s,%s)",
        (outbox_id, hashlib.sha256(b"synthetic-message").digest(), outcome),
    ).fetchone()[0]


def test_email_identity_opt_in_has_exact_immutable_session_provenance(
    admin, api, identity, workflow, scopes, identity_context
):
    context, service, cfg, identifier, cycle, state = _setup(
        admin, api, identity, workflow, scopes, identity_context
    )
    assert state == "enabled"
    row = admin.execute(
        "SELECT user_id,address_sha256,identity_session_id,recovery_generation FROM "
        "app.email_preferences WHERE tenant_id=%s AND enabled",
        (scopes[0].tenant_id,),
    ).fetchone()
    assert row == (
        context["user_id"],
        hashlib.sha256(b"owner@example.invalid").digest(),
        context["identity_session_id"],
        context["generation"],
    )
    assert service.preference(context["session_token"], scopes[0].site_id)["enabled"]
    assert (
        service.queue_report(
            context["session_token"], scopes[0].site_id, cycle.week_start, context["membership_id"]
        )
        == identifier
    )
    assert _begin(identity, identifier, context["generation"]) == "claimed"
    assert _finish(identity, identifier, "accepted") == "accepted"
    assert _begin(identity, identifier, context["generation"]) == "accepted"
    assert (
        admin.execute(
            "SELECT count(*) FROM app.email_outbox WHERE id=%s", (identifier,)
        ).fetchone()[0]
        == 1
    )
    for table in (
        "app.email_preferences",
        "app.email_send_receipts",
        "control.email_identity_claims",
        "control.email_membership_changes",
    ):
        with pytest.raises(psycopg.Error):
            admin.execute(f"DELETE FROM {table}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute(f"SELECT * FROM {table}")


@pytest.mark.parametrize(
    "address,age,enabled",
    [(None, 0, True), ("owner@example.invalid", 11, True), ("owner@example.invalid", 0, False)],
)
def test_email_unverified_stale_or_opted_out_is_suppressed_and_audited(
    admin, api, identity, workflow, scopes, identity_context, address, age, enabled
):
    context, _, _, identifier, _, state = _setup(
        admin,
        api,
        identity,
        workflow,
        scopes,
        identity_context,
        address=address,
        age=age,
        enabled=enabled,
    )
    assert state == ("disabled" if not enabled else "identity_unverified")
    assert _begin(identity, identifier, context["generation"]) == "suppressed"
    assert admin.execute(
        "SELECT phase,provider_response_class FROM app.email_send_receipts WHERE outbox_id=%s",
        (identifier,),
    ).fetchall() == [("suppression", "recipient_unavailable")]


def test_email_mismatched_address_and_wrong_tenant_cannot_opt_in(
    admin, api, identity, scopes, identity_context
):
    context = _login(identity, admin, scopes, identity_context)
    service = EmailNotifications(api, context["generation"])
    assert (
        service.opt_in(
            context["session_token"],
            scopes[0].site_id,
            enabled=True,
            address="another@example.invalid",
        )
        == "identity_unverified"
    )
    with pytest.raises(PermissionError):
        service.opt_in(
            context["session_token"],
            scopes[2].site_id,
            enabled=True,
            address="owner@example.invalid",
        )
    assert (
        admin.execute(
            "SELECT count(*) FROM app.email_preferences WHERE user_id=%s", (context["user_id"],)
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    "change",
    [
        "different_claim",
        "missing_claim",
        "membership_epoch",
        "role",
        "deprovisioned",
        "site_grant_removed",
        "opt_out",
        "recovery",
    ],
)
def test_email_dispatch_rechecks_address_membership_preferences_and_recovery(
    admin, api, identity, workflow, scopes, identity_context, change
):
    context, service, _, identifier, _, _ = _setup(
        admin, api, identity, workflow, scopes, identity_context
    )
    if change in {"different_claim", "missing_claim"}:
        _login(
            identity,
            admin,
            scopes,
            context,
            address="changed@example.invalid" if change == "different_claim" else None,
        )
    elif change == "membership_epoch":
        admin.execute(
            "UPDATE app.memberships SET authorization_epoch=authorization_epoch+1 WHERE id=%s",
            (context["membership_id"],),
        )
    elif change == "role":
        admin.execute(
            "UPDATE app.memberships SET role_key='editor' WHERE id=%s", (context["membership_id"],)
        )
    elif change == "deprovisioned":
        admin.execute(
            "UPDATE app.memberships SET state='removed' WHERE id=%s", (context["membership_id"],)
        )
    elif change == "site_grant_removed":
        admin.execute(
            "UPDATE app.site_memberships SET state='removed' WHERE id=%s",
            (context["site_membership_id"],),
        )
    elif change == "opt_out":
        assert (
            service.opt_in(context["session_token"], scopes[0].site_id, enabled=False, address=None)
            == "disabled"
        )
    generation = "synthetic-new-generation" if change == "recovery" else context["generation"]
    assert _begin(identity, identifier, generation) == "suppressed"
    assert (
        admin.execute(
            "SELECT count(*) FROM app.email_send_receipts WHERE outbox_id=%s AND phase='dispatch'",
            (identifier,),
        ).fetchone()[0]
        == 0
    )


def test_email_address_or_membership_changed_back_never_revives_verification(
    admin, api, identity, workflow, scopes, identity_context
):
    context, _, _, identifier, _, _ = _setup(
        admin, api, identity, workflow, scopes, identity_context
    )
    _login(identity, admin, scopes, context, address="changed@example.invalid")
    _login(identity, admin, scopes, context)
    admin.execute(
        "UPDATE app.memberships SET state='removed' WHERE id=%s", (context["membership_id"],)
    )
    admin.execute(
        "UPDATE app.memberships SET state='active' WHERE id=%s", (context["membership_id"],)
    )
    assert _begin(identity, identifier, context["generation"]) == "suppressed"


def test_email_cap_is_site_wide_and_bounced_address_is_suppressed(
    admin, api, identity, workflow, scopes, identity_context
):
    context, _, cfg, identifier, _, _ = _setup(
        admin, api, identity, workflow, scopes, identity_context, cap=1
    )
    assert _begin(identity, identifier, context["generation"]) == "claimed"
    assert _finish(identity, identifier, "bounced") == "failed"
    # A committed pause queues an alert, but a bounced recipient must receive nothing.
    admin.execute(
        "INSERT INTO app.site_weekly_control(tenant_id,site_id,paused) VALUES(%s,%s,true)",
        (scopes[0].tenant_id, scopes[0].site_id),
    )
    alerts = admin.execute(
        "SELECT id FROM app.email_outbox WHERE tenant_id=%s AND category='pause'",
        (scopes[0].tenant_id,),
    ).fetchall()
    assert (
        len(alerts) == 1 and _begin(identity, alerts[0][0], context["generation"]) == "suppressed"
    )
    assert (
        admin.execute(
            "SELECT provider_response_class FROM app.email_send_receipts WHERE outbox_id=%s",
            (alerts[0][0],),
        ).fetchone()[0]
        == "recipient_unavailable"
    )


def test_email_daily_cap_counts_definite_transient_attempts(
    admin, api, identity, workflow, scopes, identity_context
):
    context, _, _, identifier, _, _ = _setup(
        admin, api, identity, workflow, scopes, identity_context, cap=1
    )
    assert _begin(identity, identifier, context["generation"]) == "claimed"
    assert _finish(identity, identifier, "transient") == "retry"
    admin.execute("UPDATE app.email_outbox SET next_attempt_at=now() WHERE id=%s", (identifier,))
    assert _begin(identity, identifier, context["generation"]) == "suppressed"
    assert (
        admin.execute(
            "SELECT provider_response_class FROM app.email_send_receipts "
            "WHERE outbox_id=%s AND phase='suppression'",
            (identifier,),
        ).fetchone()[0]
        == "cap_reached"
    )


def test_email_retries_are_bounded_and_unknown_is_not_retryable(
    admin, api, identity, workflow, scopes, identity_context
):
    context, _, cfg, identifier, _, _ = _setup(
        admin, api, identity, workflow, scopes, identity_context
    )
    for attempt in range(3):
        assert _begin(identity, identifier, context["generation"]) == "claimed"
        assert _begin(identity, identifier, context["generation"]) == "dispatching"
        assert _finish(identity, identifier, "transient") == ("retry" if attempt < 2 else "failed")
        admin.execute(
            "UPDATE app.email_outbox SET next_attempt_at=now() WHERE id=%s", (identifier,)
        )
        admin.execute(
            "UPDATE control.origin_buckets SET next_allowed_at=statement_timestamp() "
            "WHERE origin=%s",
            (f"smtp+tls://{cfg.host}:{cfg.port}",),
        )
    assert _begin(identity, identifier, context["generation"]) == "failed"


def test_email_unconfigured_is_unavailable_and_no_provider_authority_is_created(
    admin, api, identity, scopes, identity_context
):
    admin.execute("DELETE FROM control.email_smtp_configuration")
    context = _login(identity, admin, scopes, identity_context)
    view = EmailNotifications(api, context["generation"]).preference(
        context["session_token"], scopes[0].site_id
    )
    assert view["availability"] == "unavailable"
    assert _begin(identity, uuid4(), context["generation"]) == "denied"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute(
            "SELECT control.configure_email_smtp('smtp.example.invalid',587,'plain','signal@example.invalid','https://dashboard.example.invalid',1,%s)",
            (b"x" * 32,),
        )


def test_email_real_protocol_through_shared_gateway_no_duplicate_after_lost_acceptance(
    admin, api, identity, workflow, scopes, identity_context, smtp_server
):
    context, _, cfg, identifier, _, _ = _setup(
        admin, api, identity, workflow, scopes, identity_context, port=smtp_server.port
    )
    smtp_server.mode = "drop"
    reader = OpenBaoSmtpCredential("https://bao.example.invalid", "synthetic-openbao-token")
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(
            200,
            json={
                "data": {
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                    "data": {"username": "synthetic-user", "password": "synthetic-password"},
                }
            },
        )
    )
    gateway = SharedSmtpEgress(
        identity,
        cfg,
        reader,
        PinnedSmtpSubmitter(
            resolver=lambda *_: ("127.0.0.1",), tls_context=smtp_server.tls_context
        ),
    )
    assert (
        asyncio.run(gateway.deliver(identifier, context["generation"], transport=transport))
        == "unknown"
    )
    assert (
        asyncio.run(gateway.deliver(identifier, context["generation"], transport=transport))
        == "unknown"
    )
    assert len(smtp_server.messages) == 1
    assert b"synthetic-password" not in smtp_server.messages[0]
    receipt = admin.execute(
        "SELECT recipient_sha256,message_sha256,provider_response_class "
        "FROM app.email_send_receipts WHERE outbox_id=%s AND phase='completion'",
        (identifier,),
    ).fetchone()
    assert receipt == (
        hashlib.sha256(b"owner@example.invalid").digest(),
        hashlib.sha256(smtp_server.messages[0]).digest(),
        "unknown",
    )


def test_email_stale_configuration_audits_suppression(
    admin, api, identity, workflow, scopes, identity_context
):
    context, _, cfg, identifier, _, _ = _setup(
        admin, api, identity, workflow, scopes, identity_context
    )
    _configure(admin, replace(cfg, daily_cap=1))
    assert _begin(identity, identifier, context["generation"]) == "suppressed"
    assert (
        admin.execute(
            "SELECT provider_response_class FROM app.email_send_receipts WHERE outbox_id=%s",
            (identifier,),
        ).fetchone()[0]
        == "stale_binding"
    )


@pytest.mark.parametrize(
    "change",
    [
        "unverified",
        "opted_out",
        "deprovisioned",
        "different_claim",
        "stale",
        "past_window",
        "future_window",
        "false_claim",
        "missing_claim",
    ],
)
def test_email_ineligible_recipient_never_reaches_smtp(
    admin, api, identity, workflow, scopes, identity_context, smtp_server, change
):
    context, _, cfg, identifier, _, _ = _setup(
        admin,
        api,
        identity,
        workflow,
        scopes,
        identity_context,
        port=smtp_server.port,
        address=None if change == "unverified" else "owner@example.invalid",
        enabled=change != "opted_out",
    )
    if change == "deprovisioned":
        admin.execute(
            "UPDATE app.memberships SET state='removed' WHERE id=%s", (context["membership_id"],)
        )
    if change == "different_claim":
        _login(identity, admin, scopes, context, address="changed@example.invalid")
    if change in {"stale", "past_window", "future_window", "false_claim", "missing_claim"}:
        previous = admin.execute(
            "SELECT claim_epoch,issued_at FROM control.email_claim_heads WHERE user_id=%s",
            (context["user_id"],),
        ).fetchone()
        claims = {"email": "owner@example.invalid"}
        if change != "missing_claim":
            claims["email_verified"] = change != "false_claim"
        offsets = {
            "stale": (-60, 0, 1),
            "past_window": (-720, -720, 12),
            "future_window": (60, 60, 0),
        }
        issued_offset, clock_offset, age = offsets.get(change, (0, 0, 0))
        new_context = _login(
            identity,
            admin,
            scopes,
            context,
            address=_verified_email_claim(claims),
            age=age,
            issued_offset=issued_offset,
            clock_offset=clock_offset,
        )
        assert admin.execute(
            "SELECT count(*) FROM control.identity_sessions WHERE id=%s",
            (new_context["identity_session_id"],),
        ).fetchone() == (1,)
        assert admin.execute(
            "SELECT count(*) FROM control.platform_events WHERE object_id=%s "
            "AND event_type='identity.session.issued'",
            (new_context["identity_session_id"],),
        ).fetchone() == (1,)
        outcome = (
            "outside_window"
            if change.endswith("window")
            else ("stale" if change == "stale" else "unverified")
        )
        assert admin.execute(
            "SELECT address,address_sha256,outcome FROM control.email_identity_claims "
            "WHERE session_id=%s",
            (new_context["identity_session_id"],),
        ).fetchone() == (None, None, outcome)
        digest, epoch, head_time = admin.execute(
            "SELECT address_sha256,claim_epoch,issued_at FROM control.email_claim_heads "
            "WHERE user_id=%s",
            (context["user_id"],),
        ).fetchone()
        assert digest is None and epoch > previous[0] and head_time >= previous[1]
        assert head_time <= datetime.now(UTC) + timedelta(seconds=30)
        assert (
            EmailNotifications(api, context["generation"]).opt_in(
                new_context["session_token"],
                scopes[0].site_id,
                enabled=True,
                address="owner@example.invalid",
            )
            == "identity_unverified"
        )
        with pytest.raises(psycopg.Error):
            admin.execute(
                "UPDATE control.email_identity_claims SET outcome='recorded' WHERE session_id=%s",
                (new_context["identity_session_id"],),
            )
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(
            200,
            json={
                "data": {
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                    "data": {"username": "synthetic-user", "password": "synthetic-password"},
                }
            },
        )
    )
    gateway = SharedSmtpEgress(
        identity,
        cfg,
        OpenBaoSmtpCredential("https://bao.example.invalid", "synthetic-openbao-token"),
        PinnedSmtpSubmitter(
            resolver=lambda *_: ("127.0.0.1",), tls_context=smtp_server.tls_context
        ),
    )
    assert (
        asyncio.run(gateway.deliver(identifier, context["generation"], transport=transport))
        == "suppressed"
    )
    assert not smtp_server.commands and not smtp_server.messages
    assert (
        admin.execute(
            "SELECT count(*) FROM app.email_send_receipts "
            "WHERE outbox_id=%s AND phase='suppression'",
            (identifier,),
        ).fetchone()[0]
        == 1
    )


def test_email_crashed_dispatch_is_unknown_not_requeued(
    admin, api, identity, workflow, scopes, identity_context
):
    context, _, _, identifier, _, _ = _setup(
        admin, api, identity, workflow, scopes, identity_context
    )
    admin.execute(
        "INSERT INTO app.email_send_receipts(tenant_id,site_id,outbox_id,attempt_number,phase,"
        "recipient_sha256,message_sha256,provider_response_class,created_at) "
        "VALUES(%s,%s,%s,1,'dispatch',%s,%s,'dispatching',now()-interval '1 minute')",
        (
            scopes[0].tenant_id,
            scopes[0].site_id,
            identifier,
            hashlib.sha256(b"owner@example.invalid").digest(),
            hashlib.sha256(b"synthetic-message").digest(),
        ),
    )
    admin.execute(
        "UPDATE app.email_outbox SET state='dispatching',attempt_count=1 WHERE id=%s", (identifier,)
    )
    admin.execute(
        "UPDATE control.email_outbox_routes SET ready_at=now()-interval '1 minute' WHERE id=%s",
        (identifier,),
    )
    assert identifier not in [
        row[0] for row in identity.execute("SELECT control.email_due_outbox(100)")
    ]
    assert _begin(identity, identifier, context["generation"]) == "unknown"
    assert (
        admin.execute(
            "SELECT provider_response_class FROM app.email_send_receipts "
            "WHERE outbox_id=%s AND phase='completion'",
            (identifier,),
        ).fetchone()[0]
        == "unknown"
    )


def test_email_role_boundary_and_projection_are_shared_with_dashboard(
    admin, api, identity, workflow, scopes, identity_context
):
    context, _, _, identifier, cycle, _ = _setup(
        admin, api, identity, workflow, scopes, identity_context
    )
    report = admin.execute(
        "SELECT control.weekly_report_projection(%s,%s,%s)",
        (scopes[0].tenant_id, scopes[0].site_id, cycle.week_start),
    ).fetchone()[0]
    from signal_core.session_tokens import hash_session_token

    legacy = api.execute(
        "SELECT control.read_weekly_cycle_report(%s,%s,%s,%s)",
        (
            hash_session_token(context["session_token"]),
            scopes[0].site_id,
            context["generation"],
            cycle.week_start,
        ),
    ).fetchone()[0]
    assert {k: v for k, v in report.items() if k != "delivery"} == legacy
    assert (
        admin.execute(
            "SELECT projection FROM app.email_outbox WHERE id=%s", (identifier,)
        ).fetchone()[0]
        == report
    )
    for connection in (api, workflow):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _begin(connection, identifier, context["generation"])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "SELECT control.weekly_report_projection(%s,%s,%s)",
                (scopes[0].tenant_id, scopes[0].site_id, cycle.week_start),
            )


def test_email_queue_failure_is_immutable_and_cannot_block_authority_reduction(
    admin, api, identity, workflow, scopes, identity_context
):
    _setup(admin, api, identity, workflow, scopes, identity_context)
    for category, projection in (("pause", {"oversized": "x" * 140000}), ("invalid", {})):
        admin.execute(
            "SELECT control.queue_email_event(%s,%s,%s,%s,%s)",
            (
                scopes[0].tenant_id,
                scopes[0].site_id,
                uuid4(),
                category,
                psycopg.types.json.Jsonb(projection),
            ),
        )
    assert admin.execute(
        "SELECT error_class FROM control.email_queue_failures "
        "WHERE tenant_id=%s ORDER BY error_class",
        (scopes[0].tenant_id,),
    ).fetchall() == [("bounded_projection_rejected",), ("queue_constraint_rejected",)]
    with pytest.raises(psycopg.Error):
        admin.execute(
            "DELETE FROM control.email_queue_failures WHERE tenant_id=%s", (scopes[0].tenant_id,)
        )
