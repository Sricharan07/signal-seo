import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.commands import (
    accept_authenticated_snapshot,
    accept_snapshot,
    read_authenticated_snapshot_command,
)
from signal_core.outbox_dispatch import (
    claim_outbox_batch,
    mark_outbox_delivered,
)
from signal_core.workflow_admission import (
    WorkflowAdmissionRejected,
    admit_command_event,
)


def accepted_envelope(api, scheduler, scope, key):
    command = accept_snapshot(
        api,
        scope,
        actor_service="workflow-admission-test",
        idempotency_key=key,
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scope.tenant_id,
        worker_key="worker.publisher",
        batch_size=1,
    )[0]
    mark_outbox_delivered(
        scheduler,
        tenant_id=scope.tenant_id,
        outbox_id=envelope.outbox_id,
        worker_key="worker.publisher",
        attempt_count=envelope.attempt_count,
    )
    assert envelope.command_id == command.id
    return command, envelope


def admission_parameters(envelope):
    return (
        envelope.tenant_id,
        envelope.outbox_id,
        envelope.event_id,
        envelope.site_id,
        envelope.command_id,
        envelope.aggregate_kind,
        envelope.event_type,
        envelope.schema_version,
        Jsonb({"schema_version": 1}),
        "workflow.command-start.v1",
    )


def test_admission_atomically_records_inbox_workflow_progress_and_event(
    admin, api, scheduler, workflow, scopes
):
    command, envelope = accepted_envelope(api, scheduler, scopes[0], "admit-once")
    admission = admit_command_event(workflow, envelope)

    assert admission.source_event_id == envelope.event_id
    assert admission.command_id == command.id
    assert admission.site_id == scopes[0].site_id
    assert admission.workflow_id == (f"signal:CrawlSite:{scopes[0].tenant_id}:{command.id}")
    assert admission.workflow_type == "CrawlSite"
    assert admission.state == "admitted"
    assert admission.processed_at.tzinfo is not None
    assert admission.duplicate is False
    assert admin.execute(
        "SELECT consumer_key, event_id, command_id, processed_at "
        "FROM app.consumer_inbox WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    ).fetchone() == (
        "workflow.command-start.v1",
        envelope.event_id,
        command.id,
        admission.processed_at,
    )
    assert admin.execute(
        "SELECT workflow_id, first_run_id, workflow_type, state_projection, "
        "projected_event_sequence, projected_at FROM app.workflow_refs "
        "WHERE tenant_id = %s AND command_id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == (
        admission.workflow_id,
        None,
        "CrawlSite",
        "admitted",
        2,
        admission.processed_at,
    )
    assert admin.execute(
        "SELECT status, row_version, result_reference FROM app.commands "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == ("workflow_admitted", 2, None)
    assert admin.execute(
        "SELECT id, event_number, event_type, facts, created_at "
        "FROM app.command_events WHERE tenant_id = %s AND command_id = %s "
        "ORDER BY event_number",
        (scopes[0].tenant_id, command.id),
    ).fetchall() == [
        (
            envelope.event_id,
            1,
            "command.accepted",
            {"schema_version": 1},
            envelope.available_at,
        ),
        (
            admission.progress_event_id,
            2,
            "command.workflow_admitted",
            {
                "consumer_key": "workflow.command-start.v1",
                "schema_version": 1,
                "workflow_id": admission.workflow_id,
            },
            admission.processed_at,
        ),
    ]


def test_duplicate_delivery_returns_original_receipt_even_after_suspension(
    admin, api, scheduler, workflow, scopes
):
    _, envelope = accepted_envelope(api, scheduler, scopes[0], "duplicate-receipt")
    first = admit_command_event(workflow, envelope)
    admin.execute(
        "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )

    duplicate = admit_command_event(workflow, envelope)

    assert duplicate == replace(first, duplicate=True)
    for table in ("consumer_inbox", "workflow_refs"):
        assert (
            admin.execute(
                f"SELECT count(*) FROM app.{table} WHERE tenant_id = %s",
                (scopes[0].tenant_id,),
            ).fetchone()[0]
            == 1
        )
    assert (
        admin.execute(
            "SELECT count(*) FROM app.command_events WHERE tenant_id = %s AND command_id = %s",
            (scopes[0].tenant_id, envelope.command_id),
        ).fetchone()[0]
        == 2
    )


def test_concurrent_duplicate_delivery_creates_one_logical_workflow(api, scheduler, scopes):
    _, envelope = accepted_envelope(api, scheduler, scopes[0], "concurrent-admission")
    barrier = Barrier(2, timeout=10)

    def consume():
        with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as connection:
            barrier.wait()
            return admit_command_event(connection, envelope)

    with ThreadPoolExecutor(max_workers=2) as executor:
        admissions = list(executor.map(lambda _: consume(), range(2)))

    assert sorted(item.duplicate for item in admissions) == [False, True]
    assert len({item.workflow_id for item in admissions}) == 1
    assert len({item.progress_event_id for item in admissions}) == 1
    assert len({item.processed_at for item in admissions}) == 1


@pytest.mark.parametrize(
    ("statement", "arguments"),
    [
        (
            "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
            lambda scope: (scope.tenant_id,),
        ),
        (
            "UPDATE app.tenants SET lifecycle = 'suspended' WHERE tenant_id = %s",
            lambda scope: (scope.tenant_id,),
        ),
        (
            "UPDATE app.sites SET state = 'archived' WHERE tenant_id = %s AND id = %s",
            lambda scope: (scope.tenant_id, scope.site_id),
        ),
    ],
)
def test_new_admission_requires_live_tenant_and_site(
    admin, api, scheduler, workflow, scopes, statement, arguments
):
    _, envelope = accepted_envelope(api, scheduler, scopes[0], f"inactive-{uuid4()}")
    admin.execute(statement, arguments(scopes[0]))

    with pytest.raises(WorkflowAdmissionRejected):
        admit_command_event(workflow, envelope)

    assert admin.execute(
        "SELECT status FROM app.commands WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, envelope.command_id),
    ).fetchone() == ("accepted",)
    assert (
        admin.execute(
            "SELECT count(*) FROM app.consumer_inbox WHERE tenant_id = %s",
            (scopes[0].tenant_id,),
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    ("statement", "arguments"),
    [
        (
            "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
            lambda scope: (scope.tenant_id,),
        ),
        (
            "UPDATE app.tenants SET lifecycle = 'suspended' WHERE tenant_id = %s",
            lambda scope: (scope.tenant_id,),
        ),
        (
            "UPDATE app.sites SET state = 'archived' WHERE tenant_id = %s AND id = %s",
            lambda scope: (scope.tenant_id, scope.site_id),
        ),
    ],
)
def test_admission_locks_current_lifecycle_until_its_transaction_finishes(
    api, scheduler, scopes, statement, arguments
):
    _, envelope = accepted_envelope(api, scheduler, scopes[0], f"lifecycle-lock-{uuid4()}")
    with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"]) as consumer:
        outcome = consumer.execute(
            "SELECT outcome FROM control.admit_command_event(" + ", ".join(["%s"] * 10) + ")",
            admission_parameters(envelope),
        ).fetchone()
        assert outcome == ("admitted",)
        with psycopg.connect(os.environ["SIGNAL_TEST_ADMIN_DSN"], autocommit=True) as operator:
            operator.execute("SET lock_timeout = '200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                operator.execute(statement, arguments(scopes[0]))
        consumer.rollback()


def test_structurally_valid_but_mismatched_envelope_is_rejected_without_side_effects(
    admin, api, scheduler, workflow, scopes
):
    _, envelope = accepted_envelope(api, scheduler, scopes[0], "tampered-envelope")
    with pytest.raises(WorkflowAdmissionRejected):
        admit_command_event(workflow, replace(envelope, outbox_id=uuid4()))
    with pytest.raises(ValueError, match="consumer"):
        admit_command_event(workflow, envelope, consumer_key="workflow.other.v1")
    assert admin.execute(
        "SELECT status FROM app.commands WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, envelope.command_id),
    ).fetchone() == ("accepted",)
    assert (
        admin.execute(
            "SELECT count(*) FROM app.consumer_inbox WHERE tenant_id = %s",
            (scopes[0].tenant_id,),
        ).fetchone()[0]
        == 0
    )


def test_database_admission_contract_rejects_null_message_fields(
    admin, api, scheduler, workflow, scopes
):
    _, envelope = accepted_envelope(api, scheduler, scopes[0], "null-contract-field")
    for index in range(5, 10):
        parameters = list(admission_parameters(envelope))
        parameters[index] = None
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            workflow.execute(
                "SELECT outcome FROM control.admit_command_event(" + ", ".join(["%s"] * 10) + ")",
                parameters,
            )
    assert admin.execute(
        "SELECT status FROM app.commands WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, envelope.command_id),
    ).fetchone() == ("accepted",)


def test_malformed_or_unsupported_input_fails_before_database_access():
    with pytest.raises(ValueError, match="typed outbox"):
        admit_command_event(object(), object())


def test_admission_rejects_non_autocommit_connections(api, scheduler, scopes):
    _, envelope = accepted_envelope(api, scheduler, scopes[0], "non-autocommit")
    with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"]) as connection:
        with pytest.raises(ValueError, match="autocommit"):
            admit_command_event(connection, envelope)


def test_workflow_role_has_exact_admission_function_without_direct_table_access(admin, workflow):
    signature = "control.admit_command_event(uuid,uuid,uuid,uuid,uuid,text,text,integer,jsonb,text)"
    assert admin.execute(
        "SELECT has_function_privilege('signal_workflow', %s, 'EXECUTE')",
        (signature,),
    ).fetchone() == (True,)
    assert admin.execute(
        "SELECT has_schema_privilege('signal_workflow', 'control', 'USAGE'), "
        "has_schema_privilege('signal_workflow', 'app', 'USAGE')"
    ).fetchone() == (True, False)
    for role in (
        "public",
        "signal_identity",
        "signal_bootstrap",
        "signal_api",
        "signal_scheduler",
    ):
        assert admin.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
            (role, signature),
        ).fetchone() == (False,)
    for table in ("app.consumer_inbox", "app.workflow_refs"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            workflow.execute(f"SELECT count(*) FROM {table}")


def test_admission_records_and_command_intent_are_guarded(admin, api, scheduler, workflow, scopes):
    _, envelope = accepted_envelope(api, scheduler, scopes[0], "guarded-admission")
    admit_command_event(workflow, envelope)
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.commands SET status = 'accepted' WHERE tenant_id = %s AND id = %s",
            (scopes[0].tenant_id, envelope.command_id),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.commands SET payload = '{\"schema_version\":2}' "
            "WHERE tenant_id = %s AND id = %s",
            (scopes[0].tenant_id, envelope.command_id),
        )
    for table in ("consumer_inbox", "workflow_refs"):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(
                f"DELETE FROM app.{table} WHERE tenant_id = %s",
                (scopes[0].tenant_id,),
            )


def test_admission_failure_rolls_back_every_progress_record(
    admin, api, scheduler, workflow, scopes
):
    _, envelope = accepted_envelope(api, scheduler, scopes[0], "atomic-admission")
    admin.execute(
        "CREATE TRIGGER fail_workflow_admission BEFORE INSERT ON app.workflow_refs "
        "FOR EACH ROW EXECUTE FUNCTION app.reject_mutation()"
    )
    try:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admit_command_event(workflow, envelope)
    finally:
        admin.execute("DROP TRIGGER fail_workflow_admission ON app.workflow_refs")
    assert admin.execute(
        "SELECT status, row_version FROM app.commands WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, envelope.command_id),
    ).fetchone() == ("accepted", 1)
    assert (
        admin.execute(
            "SELECT count(*) FROM app.consumer_inbox WHERE tenant_id = %s",
            (scopes[0].tenant_id,),
        ).fetchone()[0]
        == 0
    )
    assert (
        admin.execute(
            "SELECT count(*) FROM app.command_events WHERE tenant_id = %s AND command_id = %s",
            (scopes[0].tenant_id, envelope.command_id),
        ).fetchone()[0]
        == 1
    )


def test_human_retry_receipt_stays_accepted_while_status_exposes_progress(
    identity, identity_context, scheduler, workflow, scopes
):
    arguments = {
        "session_token": identity_context["session_token"],
        "requested_site_id": scopes[0].site_id,
        "current_recovery_generation": identity_context["generation"],
        "idempotency_key": "human-progress",
    }
    accepted = accept_authenticated_snapshot(identity, **arguments)
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.human",
        batch_size=1,
    )[0]
    admission = admit_command_event(workflow, envelope)

    retried = accept_authenticated_snapshot(identity, **arguments)
    status = read_authenticated_snapshot_command(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
        command_id=accepted.id,
    )
    assert retried.id == accepted.id
    assert retried.reused is True
    assert retried.status == "accepted"
    assert status.status == "workflow_admitted"
    assert status.workflow_id == admission.workflow_id
    assert status.workflow_type == "CrawlSite"
    assert status.workflow_state == "admitted"
    assert status.projected_at == admission.processed_at
