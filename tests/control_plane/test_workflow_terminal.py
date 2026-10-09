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
from signal_core.workflow_contracts import CrawlManifestReference, CrawlTerminalInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started
from signal_core.workflow_terminal import (
    WorkflowTerminalConflict,
    WorkflowTerminalUnavailable,
    record_crawl_terminal,
)


def running_command(api, scheduler, workflow, scope, key):
    command = accept_snapshot(
        api,
        scope,
        actor_service="workflow-terminal-test",
        idempotency_key=key,
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scope.tenant_id,
        worker_key="worker.workflow-terminal",
        batch_size=1,
    )[0]
    admission = admit_command_event(workflow, envelope)
    started = record_workflow_started(
        workflow,
        admission,
        WorkflowStartReceipt(admission.workflow_id, str(uuid4()), "start_acknowledged"),
    )
    return command, admission, started


def manifest(**overrides):
    values = {
        "schema_version": 1,
        "manifest_id": str(uuid4()),
        "manifest_sha256": "a" * 64,
        "coverage": "complete",
        "discovered_count": 7,
        "terminal_count": 7,
        "scope_version": 1,
        "crawl_policy_version": 1,
        **overrides,
    }
    return CrawlManifestReference(**values)


def terminal(scope, admission, started, *, state="succeeded", result=None, reason=None):
    if state == "succeeded" and result is None:
        result = manifest()
    return CrawlTerminalInput(
        schema_version=1,
        tenant_id=str(scope.tenant_id),
        site_id=str(scope.site_id),
        command_id=str(admission.command_id),
        workflow_id=admission.workflow_id,
        first_run_id=started.first_run_id,
        state=state,
        result_reference=result,
        reason=reason,
    )


def test_success_terminal_projection_is_atomic_and_idempotent(
    admin, api, scheduler, workflow, scopes
):
    command, admission, started = running_command(
        api, scheduler, workflow, scopes[0], "terminal-success"
    )
    expected = terminal(scopes[0], admission, started, result=manifest(coverage="partial"))

    first = record_crawl_terminal(workflow, expected)
    duplicate = record_crawl_terminal(workflow, expected)

    assert first.workflow_state == first.command_status == "succeeded"
    assert first.result_reference == expected.result_reference
    assert first.reason is None
    assert first.duplicate is False
    assert duplicate == replace(first, duplicate=True)
    assert admin.execute(
        "SELECT status, row_version, result_reference FROM app.commands "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == (
        "succeeded",
        4,
        {
            "schema_version": 1,
            "kind": "crawl_manifest",
            "manifest_id": expected.result_reference.manifest_id,
            "manifest_sha256": "a" * 64,
            "coverage": "partial",
            "discovered_count": 7,
            "terminal_count": 7,
            "scope_version": 1,
            "crawl_policy_version": 1,
        },
    )
    assert admin.execute(
        "SELECT state_projection, projected_event_sequence FROM app.workflow_refs "
        "WHERE tenant_id = %s AND command_id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == ("succeeded", 4)
    assert admin.execute(
        "SELECT event_type, facts FROM app.command_events "
        "WHERE tenant_id = %s AND command_id = %s AND event_number = 4",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == (
        "command.workflow_succeeded",
        {
            "schema_version": 1,
            "workflow_id": admission.workflow_id,
            "first_run_id": started.first_run_id,
            "result_reference": {
                "schema_version": 1,
                "kind": "crawl_manifest",
                "manifest_id": expected.result_reference.manifest_id,
                "manifest_sha256": "a" * 64,
                "coverage": "partial",
                "discovered_count": 7,
                "terminal_count": 7,
                "scope_version": 1,
                "crawl_policy_version": 1,
            },
        },
    )


def test_start_receipt_remains_stable_after_terminal_projection(
    admin, api, scheduler, workflow, scopes
):
    command, admission, started = running_command(
        api, scheduler, workflow, scopes[0], "terminal-start-redelivery"
    )
    record_crawl_terminal(workflow, terminal(scopes[0], admission, started))

    duplicate = record_workflow_started(
        workflow,
        admission,
        WorkflowStartReceipt(admission.workflow_id, started.first_run_id, "already_started"),
    )

    assert duplicate == replace(started, duplicate=True)
    assert admin.execute(
        "SELECT status, row_version FROM app.commands WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == ("succeeded", 4)
    assert admin.execute(
        "SELECT count(*) FROM app.command_events WHERE tenant_id = %s AND command_id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == (4,)


def test_running_workflow_can_finish_after_scope_suspension(
    admin, api, scheduler, workflow, scopes
):
    command, admission, started = running_command(
        api, scheduler, workflow, scopes[0], "terminal-after-suspension"
    )
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

    projected = record_crawl_terminal(
        workflow,
        terminal(
            scopes[0],
            admission,
            started,
            state="cancelled",
            result=None,
            reason="crawl_cancelled",
        ),
    )

    assert projected.command_status == projected.workflow_state == "cancelled"
    assert admin.execute(
        "SELECT status FROM app.commands WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == ("cancelled",)


@pytest.mark.parametrize(
    ("state", "reason", "event_type"),
    [
        ("failed", "crawl_activity_failed", "command.workflow_failed"),
        ("cancelled", "crawl_cancelled", "command.workflow_cancelled"),
    ],
)
def test_non_success_terminal_projection_has_closed_reason_and_no_result(
    admin, api, scheduler, workflow, scopes, state, reason, event_type
):
    command, admission, started = running_command(
        api, scheduler, workflow, scopes[0], f"terminal-{state}"
    )

    projected = record_crawl_terminal(
        workflow,
        terminal(scopes[0], admission, started, state=state, result=None, reason=reason),
    )

    assert projected.workflow_state == projected.command_status == state
    assert projected.result_reference is None
    assert projected.reason == reason
    assert admin.execute(
        "SELECT status, row_version, result_reference FROM app.commands "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == (state, 4, None)
    assert admin.execute(
        "SELECT event_type, facts->>'reason' FROM app.command_events "
        "WHERE tenant_id = %s AND command_id = %s AND event_number = 4",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == (event_type, reason)


def test_terminal_projection_rejects_unstarted_or_wrong_run(api, scheduler, workflow, scopes):
    command = accept_snapshot(
        api,
        scopes[0],
        actor_service="workflow-terminal-test",
        idempotency_key="terminal-before-start",
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.workflow-terminal",
        batch_size=1,
    )[0]
    admission = admit_command_event(workflow, envelope)
    fake_started = type("Started", (), {"first_run_id": str(uuid4())})()
    with pytest.raises(WorkflowTerminalUnavailable):
        record_crawl_terminal(workflow, terminal(scopes[0], admission, fake_started))

    started = record_workflow_started(
        workflow,
        admission,
        WorkflowStartReceipt(admission.workflow_id, str(uuid4()), "start_acknowledged"),
    )
    with pytest.raises(WorkflowTerminalUnavailable):
        record_crawl_terminal(
            workflow,
            replace(terminal(scopes[0], admission, started), first_run_id=str(uuid4())),
        )
    assert command.id == admission.command_id


def test_database_terminal_contract_rejects_null_or_unclosed_fields(
    api, scheduler, workflow, scopes
):
    _, admission, started = running_command(
        api, scheduler, workflow, scopes[0], "terminal-database-contract"
    )
    expected = terminal(scopes[0], admission, started)
    result = expected.result_reference
    assert result is not None
    parameters = [
        scopes[0].tenant_id,
        scopes[0].site_id,
        admission.command_id,
        admission.workflow_id,
        started.first_run_id,
        "succeeded",
        result.manifest_id,
        result.manifest_sha256,
        result.coverage,
        result.discovered_count,
        result.terminal_count,
        result.scope_version,
        result.crawl_policy_version,
        None,
        uuid4(),
    ]
    placeholders = ", ".join(["%s"] * len(parameters))
    for index, invalid in [(5, None), (9, None), (13, "provider-secret")]:
        attempt = list(parameters)
        attempt[index] = invalid
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            workflow.execute(
                f"SELECT outcome FROM control.record_crawl_workflow_terminal({placeholders})",
                attempt,
            )


def test_success_command_cannot_exist_without_result_reference(admin, scopes):
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "INSERT INTO app.commands ("
            "tenant_id, id, site_id, actor_service, kind, schema_version, principal_key, "
            "route_key, scope_kind, idempotency_key, request_fingerprint, payload, "
            "status, row_version, result_reference) VALUES ("
            "%s, %s, %s, 'terminal-shape-test', 'site.snapshot', 1, "
            "'service:terminal-shape-test', 'internal.site.snapshot', 'site', %s, %s, "
            "'{\"schema_version\":1}'::jsonb, 'succeeded', 4, NULL)",
            (scopes[0].tenant_id, uuid4(), scopes[0].site_id, uuid4().hex, b"0" * 32),
        )


def test_different_terminal_evidence_cannot_replace_committed_result(
    admin, api, scheduler, workflow, scopes
):
    command, admission, started = running_command(
        api, scheduler, workflow, scopes[0], "terminal-conflict"
    )
    first = terminal(scopes[0], admission, started)
    record_crawl_terminal(workflow, first)

    with pytest.raises(WorkflowTerminalConflict):
        record_crawl_terminal(
            workflow,
            replace(first, result_reference=manifest(manifest_sha256="b" * 64)),
        )
    with pytest.raises(WorkflowTerminalConflict):
        record_crawl_terminal(
            workflow,
            replace(
                first,
                state="failed",
                result_reference=None,
                reason="crawl_activity_failed",
            ),
        )
    assert admin.execute(
        "SELECT status FROM app.commands WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == ("succeeded",)


def test_concurrent_terminal_retry_creates_one_event(api, scheduler, scopes):
    with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as workflow:
        _, admission, started = running_command(
            api, scheduler, workflow, scopes[0], "terminal-concurrent"
        )
    expected = terminal(scopes[0], admission, started)
    barrier = Barrier(2, timeout=10)

    def record():
        with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"], autocommit=True) as connection:
            barrier.wait()
            return record_crawl_terminal(connection, expected)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: record(), range(2)))

    assert sorted(item.duplicate for item in results) == [False, True]
    assert len({item.progress_event_id for item in results}) == 1


def test_terminal_role_is_function_only(admin, workflow):
    signature = (
        "control.record_crawl_workflow_terminal(uuid,uuid,uuid,text,text,text,uuid,text,"
        "text,integer,integer,integer,integer,text,uuid)"
    )
    assert admin.execute(
        "SELECT has_function_privilege('signal_workflow', %s, 'EXECUTE')",
        (signature,),
    ).fetchone() == (True,)
    for role in ("public", "signal_identity", "signal_bootstrap", "signal_api", "signal_scheduler"):
        assert admin.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
            (role, signature),
        ).fetchone() == (False,)
    for table in ("app.commands", "app.command_events", "app.workflow_refs"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            workflow.execute(f"SELECT count(*) FROM {table}")


def test_terminal_records_and_projection_are_immutable(admin, api, scheduler, workflow, scopes):
    command, admission, started = running_command(
        api, scheduler, workflow, scopes[0], "terminal-guards"
    )
    record_crawl_terminal(workflow, terminal(scopes[0], admission, started))

    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.commands SET result_reference = NULL WHERE tenant_id = %s AND id = %s",
            (scopes[0].tenant_id, command.id),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.workflow_refs SET state_projection = 'failed' "
            "WHERE tenant_id = %s AND command_id = %s",
            (scopes[0].tenant_id, command.id),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "DELETE FROM app.command_events WHERE tenant_id = %s AND command_id = %s",
            (scopes[0].tenant_id, command.id),
        )


def test_terminal_event_failure_rolls_back_both_projections(
    admin, api, scheduler, workflow, scopes
):
    command, admission, started = running_command(
        api, scheduler, workflow, scopes[0], "terminal-atomic"
    )
    admin.execute(
        "CREATE TRIGGER fail_workflow_terminal_event BEFORE INSERT ON app.command_events "
        "FOR EACH ROW WHEN (NEW.event_number = 4) EXECUTE FUNCTION app.reject_mutation()"
    )
    try:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            record_crawl_terminal(workflow, terminal(scopes[0], admission, started))
    finally:
        admin.execute("DROP TRIGGER fail_workflow_terminal_event ON app.command_events")

    assert admin.execute(
        "SELECT status, row_version, result_reference FROM app.commands "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == ("processing", 3, None)
    assert admin.execute(
        "SELECT state_projection, projected_event_sequence FROM app.workflow_refs "
        "WHERE tenant_id = %s AND command_id = %s",
        (scopes[0].tenant_id, command.id),
    ).fetchone() == ("running", 3)


def test_terminal_recording_rejects_non_autocommit_connection(api, scheduler, workflow, scopes):
    _, admission, started = running_command(
        api, scheduler, workflow, scopes[0], "terminal-non-autocommit"
    )
    with psycopg.connect(os.environ["SIGNAL_TEST_WORKFLOW_DSN"]) as connection:
        with pytest.raises(ValueError, match="autocommit"):
            record_crawl_terminal(connection, terminal(scopes[0], admission, started))


def test_human_status_exposes_terminal_manifest(
    identity, identity_context, scheduler, workflow, scopes
):
    accepted = accept_authenticated_snapshot(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
        idempotency_key="human-terminal-progress",
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.human-terminal",
        batch_size=1,
    )[0]
    admission = admit_command_event(workflow, envelope)
    started = record_workflow_started(
        workflow,
        admission,
        WorkflowStartReceipt(admission.workflow_id, str(uuid4()), "start_acknowledged"),
    )
    expected = terminal(scopes[0], admission, started)
    record_crawl_terminal(workflow, expected)

    status = read_authenticated_snapshot_command(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
        command_id=accepted.id,
    )

    assert status.status == status.workflow_state == "succeeded"
    assert status.result_reference == expected.result_reference
    assert status.terminal_reason is None


@pytest.mark.parametrize(
    ("state", "reason"),
    [("failed", "crawl_activity_failed"), ("cancelled", "crawl_cancelled")],
)
def test_human_status_exposes_closed_terminal_reason(
    identity,
    identity_context,
    scheduler,
    workflow,
    scopes,
    state,
    reason,
):
    accepted = accept_authenticated_snapshot(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
        idempotency_key=f"human-terminal-{state}",
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scopes[0].tenant_id,
        worker_key="worker.human-terminal-reason",
        batch_size=1,
    )[0]
    admission = admit_command_event(workflow, envelope)
    started = record_workflow_started(
        workflow,
        admission,
        WorkflowStartReceipt(admission.workflow_id, str(uuid4()), "start_acknowledged"),
    )
    record_crawl_terminal(
        workflow,
        terminal(
            scopes[0],
            admission,
            started,
            state=state,
            result=None,
            reason=reason,
        ),
    )

    status = read_authenticated_snapshot_command(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
        command_id=accepted.id,
    )

    assert status.status == status.workflow_state == state
    assert status.result_reference is None
    assert status.terminal_reason == reason
