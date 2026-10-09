import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from signal_core.commands import accept_snapshot
from signal_core.outbox_dispatch import (
    OutboxLeaseLost,
    claim_outbox_batch,
    list_active_dispatch_tenants,
    mark_outbox_delivered,
    reschedule_outbox,
)


def test_active_tenant_directory_is_bounded_ordered_and_paginated(admin, scheduler, scopes):
    tenant_ids = list_active_dispatch_tenants(scheduler, batch_size=1000)
    assert tenant_ids == tuple(sorted(tenant_ids))
    assert scopes[0].tenant_id in tenant_ids
    assert scopes[2].tenant_id in tenant_ids

    cursor = tenant_ids[len(tenant_ids) // 2]
    page = list_active_dispatch_tenants(
        scheduler,
        after_tenant_id=cursor,
        batch_size=1000,
    )
    assert all(tenant_id > cursor for tenant_id in page)

    admin.execute(
        "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[2].tenant_id,),
    )
    assert scopes[2].tenant_id not in list_active_dispatch_tenants(scheduler, batch_size=1000)


def test_claim_returns_exact_immutable_event_and_commits_short_lease(admin, api, scheduler, scopes):
    command = accept_snapshot(
        api,
        scopes[0],
        actor_service="dispatcher-test",
        idempotency_key="claim-one",
    )
    event_id, outbox_id = admin.execute(
        "SELECT event_id, id FROM app.outbox WHERE tenant_id = %s AND aggregate_id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone()

    claimed = claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.one",
        batch_size=1,
        lease_seconds=30,
    )

    assert len(claimed) == 1
    envelope = claimed[0]
    assert envelope.tenant_id == scopes[0].tenant_id
    assert envelope.site_id == scopes[0].site_id
    assert envelope.outbox_id == outbox_id
    assert envelope.event_id == event_id
    assert envelope.command_id == command.id
    assert envelope.aggregate_kind == "command"
    assert envelope.event_type == "command.accepted"
    assert envelope.schema_version == 1
    assert envelope.payload == b'{"schema_version":1}'
    assert envelope.attempt_count == 1
    assert envelope.lease_until > datetime.now(UTC)
    assert (
        claim_outbox_batch(
            scheduler,
            tenant_id=scopes[0].tenant_id,
            worker_key="worker.two",
        )
        == ()
    )
    assert admin.execute(
        "SELECT lease_owner, attempt_count, delivered_at FROM app.outbox "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, outbox_id),
    ).fetchone() == ("worker.one", 1, None)


def test_concurrent_claimers_receive_disjoint_bounded_batches(api, scopes):
    command_ids = {
        accept_snapshot(
            api,
            scopes[0],
            actor_service="concurrent-dispatch",
            idempotency_key=f"claim-{index}",
        ).id
        for index in range(12)
    }
    barrier = Barrier(2, timeout=10)

    def claim(worker_key: str):
        with psycopg.connect(
            os.environ["SIGNAL_TEST_SCHEDULER_DSN"], autocommit=True
        ) as connection:
            barrier.wait()
            return claim_outbox_batch(
                connection,
                tenant_id=scopes[0].tenant_id,
                worker_key=worker_key,
                batch_size=6,
                lease_seconds=30,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        batches = list(executor.map(claim, ("worker.alpha", "worker.beta")))

    assert [len(batch) for batch in batches] == [6, 6]
    assert {item.command_id for batch in batches for item in batch} == command_ids
    assert len({item.outbox_id for batch in batches for item in batch}) == 12


def test_delivery_acknowledgement_is_fenced_and_terminal(admin, api, scheduler, scopes):
    command = accept_snapshot(
        api,
        scopes[0],
        actor_service="delivery-test",
        idempotency_key="deliver-one",
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.delivery",
        batch_size=1,
    )[0]
    delivered_at = mark_outbox_delivered(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        outbox_id=envelope.outbox_id,
        worker_key="worker.delivery",
        attempt_count=envelope.attempt_count,
    )
    assert delivered_at.tzinfo is not None
    with pytest.raises(OutboxLeaseLost):
        mark_outbox_delivered(
            scheduler,
            tenant_id=scopes[0].tenant_id,
            outbox_id=envelope.outbox_id,
            worker_key="worker.delivery",
            attempt_count=envelope.attempt_count,
        )
    assert (
        claim_outbox_batch(
            scheduler,
            tenant_id=scopes[0].tenant_id,
            worker_key="worker.other",
        )
        == ()
    )
    assert admin.execute(
        "SELECT aggregate_id, lease_owner, lease_until, delivered_at, attempt_count "
        "FROM app.outbox WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, envelope.outbox_id),
    ).fetchone() == (command.id, None, None, delivered_at, 1)


def test_expired_lease_redelivers_and_rejects_stale_ack(api, scheduler, scopes):
    command = accept_snapshot(
        api,
        scopes[0],
        actor_service="redelivery-test",
        idempotency_key="redeliver-one",
    )
    first = claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.first",
        batch_size=1,
        lease_seconds=1,
    )[0]
    time.sleep(1.1)
    second = claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.second",
        batch_size=1,
        lease_seconds=30,
    )[0]
    assert second.outbox_id == first.outbox_id
    assert second.command_id == command.id
    assert second.attempt_count == 2
    with pytest.raises(OutboxLeaseLost):
        mark_outbox_delivered(
            scheduler,
            tenant_id=scopes[0].tenant_id,
            outbox_id=first.outbox_id,
            worker_key="worker.first",
            attempt_count=first.attempt_count,
        )
    mark_outbox_delivered(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        outbox_id=second.outbox_id,
        worker_key="worker.second",
        attempt_count=second.attempt_count,
    )


def test_failed_publish_is_rescheduled_without_losing_identity(admin, api, scheduler, scopes):
    command = accept_snapshot(
        api,
        scopes[0],
        actor_service="retry-test",
        idempotency_key="retry-later",
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.retry",
        batch_size=1,
    )[0]
    next_available = reschedule_outbox(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        outbox_id=envelope.outbox_id,
        worker_key="worker.retry",
        attempt_count=envelope.attempt_count,
        delay_seconds=60,
    )
    assert next_available > datetime.now(UTC)
    assert (
        claim_outbox_batch(
            scheduler,
            tenant_id=scopes[0].tenant_id,
            worker_key="worker.early",
        )
        == ()
    )
    with pytest.raises(OutboxLeaseLost):
        reschedule_outbox(
            scheduler,
            tenant_id=scopes[0].tenant_id,
            outbox_id=envelope.outbox_id,
            worker_key="worker.retry",
            attempt_count=envelope.attempt_count,
            delay_seconds=60,
        )
    assert admin.execute(
        "SELECT aggregate_id, available_at, lease_owner, attempt_count, delivered_at "
        "FROM app.outbox WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, envelope.outbox_id),
    ).fetchone() == (command.id, next_available, None, 1, None)


def test_claim_requires_both_directory_and_tenant_to_be_active(admin, api, scheduler, scopes):
    command = accept_snapshot(
        api,
        scopes[0],
        actor_service="lifecycle-test",
        idempotency_key="inactive-tenant",
    )
    admin.execute(
        "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    assert (
        claim_outbox_batch(
            scheduler,
            tenant_id=scopes[0].tenant_id,
            worker_key="worker.lifecycle",
        )
        == ()
    )
    admin.execute(
        "UPDATE control.tenant_directory SET lifecycle = 'active' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    admin.execute(
        "UPDATE app.tenants SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    assert (
        claim_outbox_batch(
            scheduler,
            tenant_id=scopes[0].tenant_id,
            worker_key="worker.lifecycle",
        )
        == ()
    )
    assert admin.execute(
        "SELECT attempt_count FROM app.outbox WHERE tenant_id = %s AND aggregate_id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == (0,)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
        "UPDATE app.tenants SET lifecycle = 'suspended' WHERE tenant_id = %s",
    ],
)
def test_claim_lifecycle_locks_block_concurrent_suspension(admin, scopes, statement):
    with psycopg.connect(os.environ["SIGNAL_TEST_SCHEDULER_DSN"]) as claimant:
        claimant.execute(
            "SELECT * FROM control.claim_outbox_batch(%s, 'worker.lock', 1, 30)",
            (scopes[0].tenant_id,),
        ).fetchall()
        admin.execute("SET lock_timeout = '200ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            admin.execute(statement, (scopes[0].tenant_id,))


def test_ack_remains_available_after_tenant_suspension(admin, api, scheduler, scopes):
    accept_snapshot(
        api,
        scopes[0],
        actor_service="suspension-ack",
        idempotency_key="publish-before-suspend",
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.suspension",
        batch_size=1,
    )[0]
    admin.execute(
        "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    admin.execute(
        "UPDATE app.tenants SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    assert (
        mark_outbox_delivered(
            scheduler,
            tenant_id=scopes[0].tenant_id,
            outbox_id=envelope.outbox_id,
            worker_key="worker.suspension",
            attempt_count=envelope.attempt_count,
        ).tzinfo
        is not None
    )


def test_scheduler_has_only_directory_and_exact_dispatch_functions(admin, scheduler, scopes):
    assert scheduler.execute("SELECT count(*) FROM control.tenant_directory").fetchone()[0] > 0
    for statement in [
        "SELECT count(*) FROM app.outbox",
        "UPDATE app.outbox SET attempt_count = attempt_count + 1",
        "DELETE FROM app.outbox",
        "INSERT INTO app.outbox DEFAULT VALUES",
    ]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            scheduler.execute(statement)

    functions = [
        "control.claim_outbox_batch(uuid,text,integer,integer)",
        "control.mark_outbox_delivered(uuid,uuid,text,integer)",
        "control.reschedule_outbox(uuid,uuid,text,integer,integer)",
    ]
    for function in functions:
        assert admin.execute(
            "SELECT has_function_privilege('signal_scheduler', %s, 'EXECUTE')",
            (function,),
        ).fetchone()[0]
        for role in ["public", "signal_api", "signal_identity", "signal_bootstrap"]:
            assert not admin.execute(
                "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                (role, function),
            ).fetchone()[0]


def test_api_outbox_insert_is_column_limited_and_bookkeeping_is_guarded(admin):
    insertable = {
        "tenant_id",
        "site_id",
        "id",
        "event_id",
        "aggregate_kind",
        "aggregate_id",
        "event_type",
        "schema_version",
        "payload",
    }
    columns = [
        "tenant_id",
        "site_id",
        "id",
        "event_id",
        "aggregate_kind",
        "aggregate_id",
        "event_type",
        "schema_version",
        "payload",
        "available_at",
        "lease_owner",
        "lease_until",
        "delivered_at",
        "attempt_count",
    ]
    assert not admin.execute(
        "SELECT has_table_privilege('signal_api', 'app.outbox', 'INSERT')"
    ).fetchone()[0]
    for column in columns:
        assert admin.execute(
            "SELECT has_column_privilege('signal_api', 'app.outbox', %s, 'INSERT')",
            (column,),
        ).fetchone()[0] == (column in insertable)


def test_outbox_identity_payload_and_invalid_delivery_transitions_remain_immutable(
    admin, api, scopes
):
    command = accept_snapshot(
        api,
        scopes[0],
        actor_service="immutability-test",
        idempotency_key="immutable-outbox",
    )
    where = (scopes[0].tenant_id, command.id)
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="immutable_outbox"):
        admin.execute(
            "UPDATE app.outbox SET payload = '{\"schema_version\":2}' "
            "WHERE tenant_id = %s AND aggregate_id = %s",
            where,
        )
    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState,
        match="invalid_outbox_delivery_transition",
    ):
        admin.execute(
            "UPDATE app.outbox SET attempt_count = attempt_count + 1 "
            "WHERE tenant_id = %s AND aggregate_id = %s",
            where,
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="immutable_outbox"):
        admin.execute(
            "DELETE FROM app.outbox WHERE tenant_id = %s AND aggregate_id = %s",
            where,
        )


def test_dispatch_scope_is_transaction_local_and_connection_reuse_is_clean(scheduler, api, scopes):
    accept_snapshot(
        api,
        scopes[0],
        actor_service="scope-test",
        idempotency_key="dispatch-scope",
    )
    claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.scope",
        batch_size=1,
    )
    assert scheduler.execute(
        "SELECT NULLIF(current_setting('signal.tenant_id', true), ''), "
        "NULLIF(current_setting('signal.site_id', true), '')"
    ).fetchone() == (None, None)
    assert list_active_dispatch_tenants(scheduler, batch_size=1000)


def test_dispatch_inputs_fail_before_database_access():
    with pytest.raises(ValueError):
        list_active_dispatch_tenants(None, after_tenant_id="not-a-uuid")
    with pytest.raises(ValueError):
        list_active_dispatch_tenants(None, batch_size=True)
    with pytest.raises(ValueError):
        claim_outbox_batch(
            None,
            tenant_id="not-a-uuid",
            worker_key="worker.valid",
        )
    with pytest.raises(ValueError):
        claim_outbox_batch(
            None,
            tenant_id=uuid4(),
            worker_key="Worker Invalid",
        )
    with pytest.raises(ValueError):
        claim_outbox_batch(
            None,
            tenant_id=uuid4(),
            worker_key="worker.valid",
            batch_size=101,
        )
    with pytest.raises(ValueError):
        claim_outbox_batch(
            None,
            tenant_id=uuid4(),
            worker_key="worker.valid",
            lease_seconds=301,
        )
    with pytest.raises(ValueError):
        mark_outbox_delivered(
            None,
            tenant_id=uuid4(),
            outbox_id="not-a-uuid",
            worker_key="worker.valid",
            attempt_count=1,
        )
    with pytest.raises(ValueError):
        reschedule_outbox(
            None,
            tenant_id=uuid4(),
            outbox_id=uuid4(),
            worker_key="worker.valid",
            attempt_count=1,
            delay_seconds=3601,
        )


def test_dispatch_rejects_non_autocommit_connections():
    with psycopg.connect(os.environ["SIGNAL_TEST_SCHEDULER_DSN"]) as connection:
        with pytest.raises(ValueError, match="idle autocommit"):
            list_active_dispatch_tenants(connection)
