import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from signal_core.commands import (
    accept_authenticated_snapshot,
    accept_snapshot,
    read_authenticated_snapshot_command,
)
from signal_core.outbox_dispatch import claim_outbox_batch
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_start import (
    WorkflowStartReceipt,
    WorkflowStartRecordConflict,
    WorkflowStartRecordRejected,
    record_workflow_started,
)


def admitted_command(api, scheduler, workflow, scope, key):
    command = accept_snapshot(
        api,
        scope,
        actor_service="workflow-start-test",
        idempotency_key=key,
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scope.tenant_id,
        worker_key="worker.workflow-start",
        batch_size=1,
    )[0]
    return command, admit_command_event(workflow, envelope)


def receipt(admission, run_id=None):
    return WorkflowStartReceipt(
        workflow_id=admission.workflow_id,
        first_run_id=run_id or str(uuid4()),
        evidence_kind="start_acknowledged",
    )


def test_start_recording_atomically_advances_projection_command_and_event(
    admin, api, scheduler, workflow, scopes
):
    command, admission = admitted_command(api, scheduler, workflow, scopes[0], "record-start-once")
    evidence = receipt(admission)

    started = record_workflow_started(workflow, admission, evidence)

    assert started.command_id == command.id
    assert started.workflow_id == admission.workflow_id
    assert started.first_run_id == evidence.first_run_id
    assert started.start_evidence == "start_acknowledged"
    assert started.state == "running"
    assert started.projected_at.tzinfo is not None
    assert started.duplicate is False
    assert admin.execute(
        "SELECT first_run_id, state_projection, projected_event_sequence, projected_at "
        "FROM app.workflow_refs WHERE tenant_id = %s AND command_id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == (evidence.first_run_id, "running", 3, started.projected_at)
    assert admin.execute(
        "SELECT status, row_version, result_reference FROM app.commands "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == ("processing", 3, None)
    assert admin.execute(
        "SELECT id, event_number, event_type, facts, created_at "
        "FROM app.command_events WHERE tenant_id = %s AND command_id = %s "
        "ORDER BY event_number",
        (scopes[0].tenant_id, command.id),
    ).fetchall()[-1] == (
        started.progress_event_id,
        3,
        "command.workflow_started",
        {
            "first_run_id": evidence.first_run_id,
            "schema_version": 1,
            "start_evidence": "start_acknowledged",
            "workflow_id": admission.workflow_id,
        },
        started.projected_at,
    )


def test_admission_redelivery_returns_original_receipt_after_workflow_progress(
    admin, api, scheduler, workflow, scopes
):
    command = accept_snapshot(
        api,
        scopes[0],
        actor_service="workflow-start-test",
        idempotency_key="admission-after-start",
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.workflow-start",
        batch_size=1,
    )[0]
    admission = admit_command_event(workflow, envelope)
    record_workflow_started(workflow, admission, receipt(admission))

    duplicate = admit_command_event(workflow, envelope)

    assert duplicate == replace(admission, duplicate=True)
    assert admin.execute(
        "SELECT state_projection FROM app.workflow_refs WHERE tenant_id = %s AND command_id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == ("running",)


def test_same_start_evidence_is_idempotent_after_authority_is_suspended(
    admin, api, scheduler, workflow, scopes
):
    _, admission = admitted_command(api, scheduler, workflow, scopes[0], "record-start-retry")
    evidence = receipt(admission)
    first = record_workflow_started(workflow, admission, evidence)
    admin.execute(
        "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    admin.execute(
        "UPDATE app.tenants SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    admin.execute(
        "UPDATE app.sites SET state = 'archived' WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    )

    duplicate = record_workflow_started(workflow, admission, evidence)

    assert duplicate.progress_event_id == first.progress_event_id
    assert duplicate.projected_at == first.projected_at
    assert duplicate.duplicate is True
    assert admin.execute(
        "SELECT count(*) FROM app.command_events WHERE tenant_id = %s AND command_id = %s",
        (scopes[0].tenant_id, admission.command_id),
    ).fetchone() == (3,)


def test_positive_start_can_be_recorded_after_scope_suspension(
    admin, api, scheduler, workflow, scopes
):
    _, admission = admitted_command(api, scheduler, workflow, scopes[0], "record-after-suspension")
    admin.execute(
        "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    admin.execute(
        "UPDATE app.tenants SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    admin.execute(
        "UPDATE app.sites SET state = 'archived' WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    )

    started = record_workflow_started(workflow, admission, receipt(admission))

    assert started.state == "running"
    assert started.duplicate is False


def test_concurrent_same_run_recording_creates_one_progress_event(api, scheduler, scopes):
    with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as workflow:
        _, admission = admitted_command(
            api, scheduler, workflow, scopes[0], "concurrent-start-record"
        )
    evidence = receipt(admission)
    barrier = Barrier(2, timeout=10)

    def record():
        with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as connection:
            barrier.wait()
            return record_workflow_started(connection, admission, evidence)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: record(), range(2)))

    assert sorted(result.duplicate for result in results) == [False, True]
    assert len({result.progress_event_id for result in results}) == 1
    assert len({result.projected_at for result in results}) == 1


def test_different_run_cannot_replace_first_execution_identity(
    admin, api, scheduler, workflow, scopes
):
    _, admission = admitted_command(api, scheduler, workflow, scopes[0], "run-conflict")
    first = record_workflow_started(workflow, admission, receipt(admission))

    with pytest.raises(WorkflowStartRecordConflict):
        record_workflow_started(workflow, admission, receipt(admission))

    assert admin.execute(
        "SELECT first_run_id, projected_at FROM app.workflow_refs "
        "WHERE tenant_id = %s AND command_id = %s",
        (scopes[0].tenant_id, admission.command_id),
    ).fetchone() == (first.first_run_id, first.projected_at)


def test_mismatched_workflow_identity_or_scope_is_unavailable(api, scheduler, workflow, scopes):
    _, admission = admitted_command(api, scheduler, workflow, scopes[0], "start-mismatch")
    evidence = receipt(admission)
    wrong_site = type(admission)(**{**admission.__dict__, "site_id": scopes[1].site_id})

    with pytest.raises(WorkflowStartRecordRejected):
        record_workflow_started(workflow, wrong_site, evidence)


@pytest.mark.parametrize("index", range(7))
def test_database_start_contract_rejects_null_fields(api, scheduler, workflow, scopes, index):
    _, admission = admitted_command(
        api, scheduler, workflow, scopes[0], f"null-start-field-{index}"
    )
    parameters = [
        admission.tenant_id,
        admission.site_id,
        admission.command_id,
        admission.workflow_id,
        str(uuid4()),
        "start_acknowledged",
        uuid4(),
    ]
    parameters[index] = None

    with pytest.raises(psycopg.errors.InvalidParameterValue):
        workflow.execute(
            "SELECT outcome FROM control.record_workflow_started(" + ", ".join(["%s"] * 7) + ")",
            parameters,
        )


def test_start_recording_rejects_non_autocommit_connection(api, scheduler, scopes):
    with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as workflow:
        _, admission = admitted_command(api, scheduler, workflow, scopes[0], "non-autocommit-start")
    with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"]) as connection:
        with pytest.raises(ValueError, match="autocommit"):
            record_workflow_started(connection, admission, receipt(admission))


def test_workflow_role_has_only_exact_start_record_function(admin, workflow):
    signature = "control.record_workflow_started(uuid,uuid,uuid,text,text,text,uuid)"
    assert admin.execute(
        "SELECT has_function_privilege('signal_workflow', %s, 'EXECUTE')",
        (signature,),
    ).fetchone() == (True,)
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
    for table in ("app.commands", "app.command_events", "app.workflow_refs"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            workflow.execute(f"SELECT count(*) FROM {table}")


def test_workflow_and_command_progress_reject_unreviewed_mutation(
    admin, api, scheduler, workflow, scopes
):
    _, admission = admitted_command(api, scheduler, workflow, scopes[0], "guard-start")
    started = record_workflow_started(workflow, admission, receipt(admission))
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.workflow_refs SET first_run_id = %s "
            "WHERE tenant_id = %s AND command_id = %s",
            (str(uuid4()), scopes[0].tenant_id, admission.command_id),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.commands SET status = 'workflow_admitted' WHERE tenant_id = %s AND id = %s",
            (scopes[0].tenant_id, admission.command_id),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "DELETE FROM app.workflow_refs WHERE tenant_id = %s AND command_id = %s",
            (scopes[0].tenant_id, admission.command_id),
        )
    assert started.first_run_id


def test_event_failure_rolls_back_workflow_and_command_progress(
    admin, api, scheduler, workflow, scopes
):
    _, admission = admitted_command(api, scheduler, workflow, scopes[0], "atomic-start")
    admin.execute(
        "CREATE TRIGGER fail_workflow_started_event BEFORE INSERT ON app.command_events "
        "FOR EACH ROW WHEN (NEW.event_number = 3) EXECUTE FUNCTION app.reject_mutation()"
    )
    try:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            record_workflow_started(workflow, admission, receipt(admission))
    finally:
        admin.execute("DROP TRIGGER fail_workflow_started_event ON app.command_events")
    assert admin.execute(
        "SELECT first_run_id, state_projection, projected_event_sequence "
        "FROM app.workflow_refs WHERE tenant_id = %s AND command_id = %s",
        (scopes[0].tenant_id, admission.command_id),
    ).fetchone() == (None, "admitted", 2)
    assert admin.execute(
        "SELECT status, row_version FROM app.commands WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, admission.command_id),
    ).fetchone() == ("workflow_admitted", 2)
    assert admin.execute(
        "SELECT count(*) FROM app.command_events WHERE tenant_id = %s AND command_id = %s",
        (scopes[0].tenant_id, admission.command_id),
    ).fetchone() == (2,)


def test_human_status_exposes_complete_running_projection(
    identity, identity_context, scheduler, workflow, scopes
):
    accepted = accept_authenticated_snapshot(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
        idempotency_key="human-running-progress",
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.human-start",
        batch_size=1,
    )[0]
    admission = admit_command_event(workflow, envelope)
    started = record_workflow_started(workflow, admission, receipt(admission))

    status = read_authenticated_snapshot_command(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
        command_id=accepted.id,
    )

    assert status.status == "processing"
    assert status.workflow_id == admission.workflow_id
    assert status.workflow_type == "CrawlSite"
    assert status.first_run_id == started.first_run_id
    assert status.workflow_state == "running"
    assert status.projected_at == started.projected_at
