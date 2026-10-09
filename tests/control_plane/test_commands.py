import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from signal_core.commands import IdempotencyConflict, ScopeUnavailable, accept_snapshot
from signal_core.database import Scope, scoped_transaction


def test_acceptance_commits_command_event_and_outbox_together(api, scopes):
    result = accept_snapshot(api, scopes[0], actor_service="test", idempotency_key="first")
    assert result.reused is False
    with scoped_transaction(api, scopes[0]):
        assert api.execute("SELECT id, status FROM app.commands").fetchall() == [
            (result.id, "accepted")
        ]
        assert api.execute(
            "SELECT command_id, event_number FROM app.command_events"
        ).fetchall() == [(result.id, 1)]
        assert api.execute("SELECT aggregate_id, delivered_at FROM app.outbox").fetchall() == [
            (result.id, None)
        ]


def test_retry_returns_original_command_without_duplicate_events(api, scopes):
    first = accept_snapshot(api, scopes[0], actor_service="test", idempotency_key="retry")
    second = accept_snapshot(api, scopes[0], actor_service="test", idempotency_key="retry")
    assert second.id == first.id and second.reused is True
    with scoped_transaction(api, scopes[0]):
        for table in ["commands", "command_events", "outbox"]:
            assert api.execute(f"SELECT count(*) FROM app.{table}").fetchone()[0] == 1


def test_same_key_for_different_site_conflicts_without_disclosing_scope(api, scopes):
    accept_snapshot(api, scopes[0], actor_service="test", idempotency_key="same")
    with pytest.raises(IdempotencyConflict) as failure:
        accept_snapshot(api, scopes[1], actor_service="test", idempotency_key="same")
    assert str(failure.value) == ""
    # Principals and tenants have separate idempotency namespaces.
    assert not accept_snapshot(api, scopes[1], actor_service="other", idempotency_key="same").reused
    assert not accept_snapshot(api, scopes[2], actor_service="test", idempotency_key="same").reused


def test_concurrent_duplicate_acceptance_has_one_durable_result(scopes):
    barrier = Barrier(4, timeout=10)

    def accept():
        with psycopg.connect(os.environ["SIGNAL_TEST_API_DSN"], autocommit=True) as connection:
            barrier.wait()
            return accept_snapshot(
                connection, scopes[0], actor_service="test", idempotency_key="concurrent"
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: accept(), range(4)))
    assert len({result.id for result in results}) == 1
    assert sum(not result.reused for result in results) == 1


def test_outbox_failure_rolls_back_all_acceptance_records(admin, api, scopes):
    # A database trigger injects the fault after the command and event INSERTs.
    admin.execute(
        "CREATE FUNCTION app.inject_outbox_failure() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'injected_outbox_failure'; END $$"
    )
    admin.execute(
        "CREATE TRIGGER inject_failure BEFORE INSERT ON app.outbox "
        "FOR EACH ROW EXECUTE FUNCTION app.inject_outbox_failure()"
    )
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="injected_outbox_failure"):
            accept_snapshot(api, scopes[0], actor_service="test", idempotency_key="rollback")
        for table in ["commands", "command_events", "outbox"]:
            assert (
                admin.execute(
                    f"SELECT count(*) FROM app.{table} WHERE tenant_id = %s", (scopes[0].tenant_id,)
                ).fetchone()[0]
                == 0
            )
    finally:
        admin.execute("DROP TRIGGER inject_failure ON app.outbox")
        admin.execute("DROP FUNCTION app.inject_outbox_failure()")
    assert not accept_snapshot(
        api, scopes[0], actor_service="test", idempotency_key="rollback"
    ).reused


@pytest.mark.parametrize("key", ["", "x" * 129, "a\x00b", "a b", "../escape", None])
def test_invalid_idempotency_keys_fail_before_database_access(api, scopes, key):
    with pytest.raises(ValueError):
        accept_snapshot(api, scopes[0], actor_service="test", idempotency_key=key)


@pytest.mark.parametrize("actor", ["", "admin:anything", "x" * 65, None])
def test_invalid_service_identity_fails_before_database_access(api, scopes, actor):
    with pytest.raises(ValueError):
        accept_snapshot(api, scopes[0], actor_service=actor, idempotency_key="valid")


def test_missing_scope_and_suspended_tenant_are_rejected(admin, api, scopes):
    with pytest.raises(ScopeUnavailable):
        accept_snapshot(
            api, Scope(uuid4(), uuid4()), actor_service="test", idempotency_key="missing"
        )
    admin.execute(
        "UPDATE app.tenants SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    with pytest.raises(ScopeUnavailable):
        accept_snapshot(api, scopes[0], actor_service="test", idempotency_key="suspended")


def test_reconnection_after_lost_acknowledgement_reuses_committed_intent(api, scopes):
    original = accept_snapshot(api, scopes[0], actor_service="test", idempotency_key="disconnect")
    api.close()
    with psycopg.connect(os.environ["SIGNAL_TEST_API_DSN"], autocommit=True) as new_connection:
        recovered = accept_snapshot(
            new_connection, scopes[0], actor_service="test", idempotency_key="disconnect"
        )
    assert recovered.id == original.id and recovered.reused
