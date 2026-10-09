import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest
from signal_core.audit_findings import (
    LOCAL_FIXTURE_HTML,
    LOCAL_FIXTURE_SOURCE,
    AuditNotReady,
    has_nonempty_meta_description,
    read_authenticated_findings,
    record_authenticated_local_fixture_finding,
)
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.commands import accept_authenticated_snapshot
from signal_core.outbox_dispatch import claim_outbox_batch
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_contracts import CrawlManifestReference, CrawlTerminalInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started
from signal_core.workflow_terminal import record_crawl_terminal


def completed_audit(identity, scheduler, workflow, scope, context, key):
    command = accept_authenticated_snapshot(
        identity,
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
        idempotency_key=key,
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scope.tenant_id,
        worker_key="worker.audit-findings",
        batch_size=1,
    )[0]
    admission = admit_command_event(workflow, envelope)
    started = record_workflow_started(
        workflow,
        admission,
        WorkflowStartReceipt(admission.workflow_id, str(uuid4()), "start_acknowledged"),
    )
    manifest = CrawlManifestReference(
        schema_version=1,
        manifest_id=str(uuid4()),
        manifest_sha256="a" * 64,
        coverage="complete",
        discovered_count=1,
        terminal_count=1,
        scope_version=1,
        crawl_policy_version=1,
    )
    record_crawl_terminal(
        workflow,
        CrawlTerminalInput(
            schema_version=1,
            tenant_id=str(scope.tenant_id),
            site_id=str(scope.site_id),
            command_id=str(command.id),
            workflow_id=admission.workflow_id,
            first_run_id=started.first_run_id,
            state="succeeded",
            result_reference=manifest,
            reason=None,
        ),
    )
    return command, manifest


def record(identity, scope, context):
    return record_authenticated_local_fixture_finding(
        identity,
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
    )


def test_metadata_detector_is_deterministic_and_bounded():
    assert has_nonempty_meta_description(LOCAL_FIXTURE_HTML) is False
    assert (
        has_nonempty_meta_description(
            '<html><head><meta NAME="description" content="Useful summary"></head></html>'
        )
        is True
    )
    assert (
        has_nonempty_meta_description(
            '<html><head><meta name="description" content="   "></head></html>'
        )
        is False
    )
    for invalid in (None, "", "x\x00y", "x" * (64 * 1024 + 1)):
        with pytest.raises(ValueError, match="bounded"):
            has_nonempty_meta_description(invalid)


def test_fixture_analysis_requires_a_completed_current_user_audit(
    identity, scopes, identity_context
):
    with pytest.raises(AuditNotReady):
        record(identity, scopes[0], identity_context)
    accept_authenticated_snapshot(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
        idempotency_key="unfinished-audit",
    )
    with pytest.raises(AuditNotReady):
        record(identity, scopes[0], identity_context)


def test_fixture_finding_is_durable_inspectable_and_idempotent(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    command, manifest = completed_audit(
        identity, scheduler, workflow, scopes[0], identity_context, "finding-first"
    )

    first = record(identity, scopes[0], identity_context)
    duplicate = record(identity, scopes[0], identity_context)
    visible = read_authenticated_findings(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
    )

    assert first.command_id == command.id
    assert first.manifest_id == UUID(manifest.manifest_id)
    assert first.source_identifier == LOCAL_FIXTURE_SOURCE
    assert first.content_sha256 == sha256(LOCAL_FIXTURE_HTML.encode()).hexdigest()
    assert first.reused is False
    assert duplicate.id == first.id
    assert duplicate.evidence_id == first.evidence_id
    assert duplicate.evidence_observed_at == first.evidence_observed_at
    assert duplicate.reused is True
    assert visible == (replace(duplicate, reused=False),)
    assert admin.execute(
        "SELECT count(*) FROM app.evidence_records WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (1,)
    assert admin.execute(
        "SELECT count(*) FROM app.findings WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (1,)
    assert admin.execute(
        "SELECT count(*) FROM app.finding_evidence WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (1,)


def test_new_audit_appends_evidence_and_advances_existing_finding(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    completed_audit(identity, scheduler, workflow, scopes[0], identity_context, "finding-old")
    first = record(identity, scopes[0], identity_context)
    newest_command, newest_manifest = completed_audit(
        identity, scheduler, workflow, scopes[0], identity_context, "finding-new"
    )
    newest = record(identity, scopes[0], identity_context)

    assert newest.id == first.id
    assert newest.evidence_id != first.evidence_id
    assert newest.command_id == newest_command.id
    assert newest.manifest_id == UUID(newest_manifest.manifest_id)
    assert newest.first_seen_at == first.first_seen_at
    assert newest.last_seen_at >= first.last_seen_at
    assert admin.execute(
        "SELECT count(*) FROM app.evidence_records WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (2,)
    assert admin.execute(
        "SELECT count(*) FROM app.finding_evidence WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (2,)


def test_finding_operations_recheck_session_and_selected_site_authority(
    identity, scheduler, workflow, scopes, identity_context
):
    completed_audit(identity, scheduler, workflow, scopes[0], identity_context, "finding-auth")
    with pytest.raises(AuthorizationDenied):
        record_authenticated_local_fixture_finding(
            identity,
            session_token=identity_context["session_token"],
            requested_site_id=scopes[1].site_id,
            current_recovery_generation=identity_context["generation"],
        )
    with pytest.raises(InvalidSession):
        record_authenticated_local_fixture_finding(
            identity,
            session_token=identity_context["session_token"],
            requested_site_id=scopes[0].site_id,
            current_recovery_generation="stale-generation",
        )


def test_concurrent_fixture_retries_converge_on_one_evidence_and_finding(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    completed_audit(identity, scheduler, workflow, scopes[0], identity_context, "finding-race")
    barrier = Barrier(4, timeout=10)

    def issue():
        with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as connection:
            barrier.wait()
            return record(connection, scopes[0], identity_context)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: issue(), range(4)))
    assert len({result.id for result in results}) == 1
    assert len({result.evidence_id for result in results}) == 1
    assert sum(not result.reused for result in results) == 1
    for table in ("evidence_records", "findings", "finding_evidence"):
        assert admin.execute(
            f"SELECT count(*) FROM app.{table} WHERE tenant_id = %s AND site_id = %s",
            (scopes[0].tenant_id, scopes[0].site_id),
        ).fetchone() == (1,)


def test_fixture_evidence_conflict_and_direct_mutation_fail_closed(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    completed_audit(identity, scheduler, workflow, scopes[0], identity_context, "finding-conflict")
    finding = record(identity, scopes[0], identity_context)
    row = identity.execute(
        "SELECT outcome FROM control.record_authenticated_local_fixture_finding("
        "%s, %s, %s, %s, %s, %s)",
        (
            sha256(identity_context["session_token"].encode("ascii")).digest(),
            scopes[0].site_id,
            identity_context["generation"],
            uuid4(),
            uuid4(),
            b"b" * 32,
        ),
    ).fetchone()
    assert row == ("evidence_conflict",)

    for table in ("evidence_records", "findings", "finding_evidence"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            identity.execute(f"SELECT count(*) FROM app.{table}")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.evidence_records SET observed_at = now() WHERE tenant_id = %s AND id = %s",
            (scopes[0].tenant_id, finding.evidence_id),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.findings SET title = 'Changed' WHERE tenant_id = %s AND id = %s",
            (scopes[0].tenant_id, finding.id),
        )
