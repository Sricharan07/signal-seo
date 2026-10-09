import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.commands import (
    CommandNotFound,
    accept_authenticated_snapshot,
    read_authenticated_snapshot_command,
    read_latest_authenticated_snapshot_command,
)
from signal_core.database import scoped_transaction


def accept(identity, scopes, context, **overrides):
    arguments = {
        "session_token": context["session_token"],
        "requested_site_id": scopes[0].site_id,
        "current_recovery_generation": context["generation"],
        "idempotency_key": "human-command",
        **overrides,
    }
    return accept_authenticated_snapshot(identity, **arguments)


def read(identity, scopes, context, command_id, **overrides):
    arguments = {
        "session_token": context["session_token"],
        "requested_site_id": scopes[0].site_id,
        "current_recovery_generation": context["generation"],
        "command_id": command_id,
        **overrides,
    }
    return read_authenticated_snapshot_command(identity, **arguments)


def read_latest(identity, scopes, context, **overrides):
    arguments = {
        "session_token": context["session_token"],
        "requested_site_id": scopes[0].site_id,
        "current_recovery_generation": context["generation"],
        **overrides,
    }
    return read_latest_authenticated_snapshot_command(identity, **arguments)


def grant_second_site(admin, scopes, context):
    admin.execute(
        "INSERT INTO app.site_memberships "
        "(tenant_id, site_id, id, user_id, permission_set, authorization_epoch, state) "
        "VALUES (%s, %s, %s, %s, %s, 1, 'active')",
        (
            scopes[1].tenant_id,
            scopes[1].site_id,
            uuid4(),
            context["user_id"],
            '{"permissions":["site.snapshot.request"],"schema_version":1}',
        ),
    )


def test_human_acceptance_commits_attributed_intent_event_and_outbox(
    admin, identity, scopes, identity_context
):
    result = accept(identity, scopes, identity_context)
    assert result.reused is False
    assert result.status == "accepted"
    assert result.accepted_at.tzinfo is not None

    command = admin.execute(
        "SELECT id, actor_user_id, actor_service, principal_key, route_key, "
        "status, octet_length(request_fingerprint) FROM app.commands "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, result.id),
    ).fetchone()
    assert command == (
        result.id,
        identity_context["user_id"],
        None,
        f"user:{identity_context['user_id']}",
        "api.site.snapshot",
        "accepted",
        32,
    )
    assert admin.execute(
        "SELECT command_id, event_number, event_type FROM app.command_events "
        "WHERE tenant_id = %s AND command_id = %s",
        (scopes[0].tenant_id, result.id),
    ).fetchall() == [(result.id, 1, "command.accepted")]
    assert admin.execute(
        "SELECT aggregate_id, event_type, delivered_at FROM app.outbox "
        "WHERE tenant_id = %s AND aggregate_id = %s",
        (scopes[0].tenant_id, result.id),
    ).fetchall() == [(result.id, "command.accepted", None)]


def test_human_retry_reuses_original_without_duplicate_records(
    admin, identity, scopes, identity_context
):
    first = accept(identity, scopes, identity_context, idempotency_key="human-retry")
    second = accept(identity, scopes, identity_context, idempotency_key="human-retry")
    assert second.id == first.id
    assert second.accepted_at == first.accepted_at
    assert second.reused is True
    for table in ("commands", "command_events", "outbox"):
        assert (
            admin.execute(
                f"SELECT count(*) FROM app.{table} WHERE tenant_id = %s",
                (scopes[0].tenant_id,),
            ).fetchone()[0]
            == 1
        )


def test_human_request_for_non_selected_site_is_denied_without_disclosure(
    admin, identity, scopes, identity_context
):
    grant_second_site(admin, scopes, identity_context)
    accept(identity, scopes, identity_context, idempotency_key="cross-site")
    with pytest.raises(AuthorizationDenied) as failure:
        accept(
            identity,
            scopes,
            identity_context,
            requested_site_id=scopes[1].site_id,
            idempotency_key="cross-site",
        )
    assert str(failure.value) == ""


def test_concurrent_human_retries_have_one_durable_result(scopes, identity_context):
    barrier = Barrier(4, timeout=10)

    def issue():
        with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as connection:
            barrier.wait()
            return accept(
                connection,
                scopes,
                identity_context,
                idempotency_key="human-concurrent",
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: issue(), range(4)))
    assert len({result.id for result in results}) == 1
    assert sum(not result.reused for result in results) == 1


@pytest.mark.parametrize(
    ("table", "column", "value", "error"),
    [
        ("app.sessions", "revoked_at", "now()", InvalidSession),
        ("control.identity_sessions", "revoked_at", "now()", InvalidSession),
        ("control.users", "disabled_at", "now()", InvalidSession),
        ("app.memberships", "state", "'suspended'", AuthorizationDenied),
        ("app.site_memberships", "state", "'removed'", AuthorizationDenied),
        ("app.sites", "state", "'archived'", AuthorizationDenied),
        ("app.tenants", "lifecycle", "'suspended'", AuthorizationDenied),
    ],
)
def test_human_acceptance_rechecks_live_authority_atomically(
    admin, identity, scopes, identity_context, table, column, value, error
):
    identifiers = {
        "app.sessions": ("id", identity_context["tenant_session_id"]),
        "control.identity_sessions": ("id", identity_context["identity_session_id"]),
        "control.users": ("id", identity_context["user_id"]),
        "app.memberships": ("id", identity_context["membership_id"]),
        "app.site_memberships": ("id", identity_context["site_membership_id"]),
        "app.sites": ("id", scopes[0].site_id),
        "app.tenants": ("tenant_id", scopes[0].tenant_id),
    }
    key, identifier = identifiers[table]
    admin.execute(f"UPDATE {table} SET {column} = {value} WHERE {key} = %s", (identifier,))
    with pytest.raises(error):
        accept(identity, scopes, identity_context, idempotency_key=f"state-{table}")
    assert (
        admin.execute(
            "SELECT count(*) FROM app.commands WHERE tenant_id = %s",
            (scopes[0].tenant_id,),
        ).fetchone()[0]
        == 0
    )


def test_human_acceptance_rejects_stale_external_recovery_generation(
    identity, scopes, identity_context
):
    with pytest.raises(InvalidSession):
        accept(
            identity,
            scopes,
            identity_context,
            current_recovery_generation="new-generation",
        )


@pytest.mark.parametrize("token", [None, "", "short", "x" * 42, "contains+symbol"])
def test_human_acceptance_rejects_malformed_session_before_database(
    identity, scopes, identity_context, token
):
    identity.close()
    with pytest.raises(InvalidSession):
        accept(identity, scopes, identity_context, session_token=token)


@pytest.mark.parametrize("key", [None, "", "x" * 129, "a b", "../escape", "a\x00b"])
def test_human_acceptance_rejects_bad_idempotency_key_before_database(
    identity, scopes, identity_context, key
):
    identity.close()
    with pytest.raises(ValueError, match="Idempotency"):
        accept(identity, scopes, identity_context, idempotency_key=key)


def test_human_command_status_rechecks_authority_and_actor(identity, scopes, identity_context):
    accepted = accept(identity, scopes, identity_context, idempotency_key="read-status")
    status = read(identity, scopes, identity_context, accepted.id)
    assert status.id == accepted.id
    assert status.actor_user_id == identity_context["user_id"]
    assert status.kind == "site.snapshot"
    assert status.status == "accepted"
    assert status.accepted_at == accepted.accepted_at

    with pytest.raises(CommandNotFound):
        read(identity, scopes, identity_context, uuid4())
    with pytest.raises(AuthorizationDenied):
        read(
            identity,
            scopes,
            identity_context,
            accepted.id,
            requested_site_id=scopes[1].site_id,
        )


def test_latest_human_command_returns_newest_current_user_projection(
    identity, scopes, identity_context
):
    first = accept(identity, scopes, identity_context, idempotency_key="latest-first")
    second = accept(identity, scopes, identity_context, idempotency_key="latest-second")

    latest = read_latest(identity, scopes, identity_context)

    assert latest.id == second.id
    assert latest.id != first.id
    assert latest.status == "accepted"
    assert latest.actor_user_id == identity_context["user_id"]


def test_latest_human_command_fails_closed_for_empty_or_wrong_site(
    identity, scopes, identity_context
):
    with pytest.raises(CommandNotFound):
        read_latest(identity, scopes, identity_context)

    accept(identity, scopes, identity_context, idempotency_key="latest-authority")
    with pytest.raises(AuthorizationDenied):
        read_latest(
            identity,
            scopes,
            identity_context,
            requested_site_id=scopes[1].site_id,
        )


def test_human_acceptance_rolls_back_when_outbox_insert_fails(
    admin, identity, scopes, identity_context
):
    admin.execute(
        "CREATE FUNCTION app.inject_human_outbox_failure() RETURNS trigger LANGUAGE plpgsql "
        "AS $$ BEGIN RAISE EXCEPTION 'injected_human_outbox_failure'; END $$"
    )
    admin.execute(
        "CREATE TRIGGER inject_human_failure BEFORE INSERT ON app.outbox "
        "FOR EACH ROW EXECUTE FUNCTION app.inject_human_outbox_failure()"
    )
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="injected_human_outbox_failure"):
            accept(identity, scopes, identity_context, idempotency_key="human-rollback")
        for table in ("commands", "command_events", "outbox"):
            assert (
                admin.execute(
                    f"SELECT count(*) FROM app.{table} WHERE tenant_id = %s",
                    (scopes[0].tenant_id,),
                ).fetchone()[0]
                == 0
            )
    finally:
        admin.execute("DROP TRIGGER inject_human_failure ON app.outbox")
        admin.execute("DROP FUNCTION app.inject_human_outbox_failure()")


def test_identity_role_has_only_narrow_command_functions(identity, scopes, identity_context):
    assert identity.execute(
        "SELECT has_function_privilege(current_user, "
        "'control.accept_authenticated_snapshot(bytea,uuid,text,text,uuid,uuid,uuid)', "
        "'EXECUTE'), has_function_privilege(current_user, "
        "'control.read_authenticated_snapshot_command(bytea,uuid,text,uuid)', 'EXECUTE'), "
        "has_function_privilege(current_user, "
        "'control.read_latest_authenticated_snapshot_command(bytea,uuid,text)', 'EXECUTE')"
    ).fetchone() == (True, True, True)
    with scoped_transaction(identity, scopes[0]):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), identity.transaction():
            identity.execute(
                "INSERT INTO app.commands "
                "(tenant_id, id, site_id, actor_user_id, kind, schema_version, principal_key, "
                "route_key, scope_kind, idempotency_key, request_fingerprint, payload) "
                "VALUES (%s, %s, %s, %s, 'site.snapshot', 1, %s, 'api.site.snapshot', "
                "'site', 'forged', %s, %s)",
                (
                    scopes[0].tenant_id,
                    uuid4(),
                    scopes[0].site_id,
                    identity_context["user_id"],
                    f"user:{identity_context['user_id']}",
                    b"x" * 32,
                    '{"schema_version":1}',
                ),
            )


def test_command_actor_constraints_reject_ambiguous_or_unbound_humans(
    admin, scopes, identity_context
):
    base = (
        scopes[0].tenant_id,
        uuid4(),
        scopes[0].site_id,
        identity_context["user_id"],
        f"user:{identity_context['user_id']}",
        b"x" * 32,
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "INSERT INTO app.commands "
            "(tenant_id, id, site_id, actor_service, actor_user_id, kind, schema_version, "
            "principal_key, route_key, scope_kind, idempotency_key, request_fingerprint, payload) "
            "VALUES (%s, %s, %s, 'forged', %s, 'site.snapshot', 1, %s, "
            "'api.site.snapshot', 'site', 'ambiguous', %s, '{\"schema_version\":1}')",
            base,
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        unknown_user_id = uuid4()
        admin.execute(
            "INSERT INTO app.commands "
            "(tenant_id, id, site_id, actor_user_id, kind, schema_version, principal_key, "
            "route_key, scope_kind, idempotency_key, request_fingerprint, payload) "
            "VALUES (%s, %s, %s, %s, 'site.snapshot', 1, %s, "
            "'api.site.snapshot', 'site', 'unbound', %s, '{\"schema_version\":1}')",
            (
                scopes[0].tenant_id,
                uuid4(),
                scopes[0].site_id,
                unknown_user_id,
                f"user:{unknown_user_id}",
                b"x" * 32,
            ),
        )
