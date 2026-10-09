import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from signal_core.audit_findings import record_authenticated_local_fixture_finding
from signal_core.commands import accept_authenticated_snapshot
from signal_core.model_reasoning import MetadataDraft, ModelUsage
from signal_core.outbox_dispatch import claim_outbox_batch
from signal_core.proposals import (
    ApprovalDecisionConflict,
    ApprovalPermissionDenied,
    ApprovalRevisionConflict,
    ModelOutcomeUnknown,
    ProposalNotReady,
    begin_authenticated_fixture_model_run,
    build_fixture_metadata_task,
    build_local_fixture_manifest,
    canonicalize_proposal_manifest,
    complete_authenticated_fixture_model_proposal,
    decide_authenticated_local_fixture_approval,
    fail_authenticated_fixture_model_run,
    prepare_authenticated_local_fixture_proposal,
    read_authenticated_local_fixture_proposals,
)
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_contracts import CrawlManifestReference, CrawlTerminalInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started
from signal_core.workflow_terminal import record_crawl_terminal


def _complete_fixture_finding(identity, scheduler, workflow, scope, context):
    command = accept_authenticated_snapshot(
        identity,
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
        idempotency_key=f"proposal-{uuid4()}",
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scope.tenant_id,
        worker_key=f"worker.proposal-{uuid4()}",
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
    return record_authenticated_local_fixture_finding(
        identity,
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
    )


def _make_owner(admin, scope, context):
    admin.execute(
        "UPDATE app.memberships SET role_key = 'owner' WHERE tenant_id = %s AND id = %s",
        (scope.tenant_id, context["membership_id"]),
    )


def _prepare(identity, scope, context):
    return prepare_authenticated_local_fixture_proposal(
        identity,
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
    )


def _draft() -> MetadataDraft:
    description = (
        "Explore Signal's synthetic product fixture with clear, durable SEO evidence "
        "prepared for supervised review."
    )
    rationale = "The draft describes only the supplied synthetic page facts."
    output = {"meta_description": description, "rationale": rationale}
    canonical = json.dumps(
        output, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return MetadataDraft(
        meta_description=description,
        rationale=rationale,
        provider_response_id="resp_fixture_model_1",
        model_reported="gpt-6-luna-2026-09-01",
        usage=ModelUsage(120, 40, 160, 0),
        output_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def test_local_manifest_uses_stable_rfc8785_revision_identity(
    identity, scheduler, workflow, scopes, identity_context
):
    finding = _complete_fixture_finding(identity, scheduler, workflow, scopes[0], identity_context)
    manifest = build_local_fixture_manifest(site_id=scopes[0].site_id, finding=finding)

    first = canonicalize_proposal_manifest(manifest)
    second = canonicalize_proposal_manifest(dict(reversed(tuple(manifest.items()))))

    assert first == second
    assert first.startswith(b'{"assessment":')
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    assert manifest["authority"]["external_write"] is False
    assert manifest["assessment"]["customer_origin_read"] is False


def test_prepare_requires_owner_and_current_evidence(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    with pytest.raises(ApprovalPermissionDenied):
        _prepare(identity, scopes[0], identity_context)

    _make_owner(admin, scopes[0], identity_context)
    with pytest.raises(ProposalNotReady):
        _prepare(identity, scopes[0], identity_context)

    _complete_fixture_finding(identity, scheduler, workflow, scopes[0], identity_context)
    prepared = _prepare(identity, scopes[0], identity_context)

    assert prepared.approval_status == "pending"
    assert prepared.revision_number == 1
    assert prepared.approval_expires_at > prepared.approval_requested_at
    assert prepared.manifest["authority"]["external_write"] is False


def test_prepare_is_idempotent_and_read_returns_the_exact_revision(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    _make_owner(admin, scopes[0], identity_context)
    finding = _complete_fixture_finding(identity, scheduler, workflow, scopes[0], identity_context)

    first = _prepare(identity, scopes[0], identity_context)
    replay = _prepare(identity, scopes[0], identity_context)
    visible = read_authenticated_local_fixture_proposals(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
    )

    assert replay.proposal_id == first.proposal_id
    assert replay.revision_id == first.revision_id
    assert replay.approval_request_id == first.approval_request_id
    assert replay.revision_sha256 == first.revision_sha256
    assert first.reused is False
    assert replay.reused is True
    assert len(visible) == 1
    assert visible[0].revision_sha256 == first.revision_sha256
    assert visible[0].manifest["finding"]["id"] == str(finding.id)
    assert (
        hashlib.sha256(canonicalize_proposal_manifest(visible[0].manifest)).hexdigest()
        == first.revision_sha256
    )

    for table in (
        "proposals",
        "proposal_revisions",
        "approval_requests",
    ):
        assert admin.execute(
            f"SELECT count(*) FROM app.{table} WHERE tenant_id = %s AND site_id = %s",
            (scopes[0].tenant_id, scopes[0].site_id),
        ).fetchone() == (1,)


def test_decision_is_exact_immutable_and_never_dispatches_external_work(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    _make_owner(admin, scopes[0], identity_context)
    _complete_fixture_finding(identity, scheduler, workflow, scopes[0], identity_context)
    proposal = _prepare(identity, scopes[0], identity_context)
    decision_id = uuid4()

    with pytest.raises(ApprovalRevisionConflict):
        decide_authenticated_local_fixture_approval(
            identity,
            session_token=identity_context["session_token"],
            requested_site_id=scopes[0].site_id,
            current_recovery_generation=identity_context["generation"],
            approval_request_id=proposal.approval_request_id,
            expected_revision_sha256="0" * 64,
            decision="approved",
            decision_id=decision_id,
        )

    decided = decide_authenticated_local_fixture_approval(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
        approval_request_id=proposal.approval_request_id,
        expected_revision_sha256=proposal.revision_sha256,
        decision="approved",
        decision_id=decision_id,
    )
    replay = decide_authenticated_local_fixture_approval(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
        approval_request_id=proposal.approval_request_id,
        expected_revision_sha256=proposal.revision_sha256,
        decision="approved",
        decision_id=decision_id,
    )

    assert decided.decision == "approved"
    assert decided.approval_status == "approved"
    assert decided.decision_id == decision_id
    assert replay.decision_id == decision_id
    assert replay.reused is True
    assert admin.execute(
        "SELECT count(*) FROM app.approval_decisions WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (1,)
    assert admin.execute(
        "SELECT count(*) FROM app.outbox WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (1,)

    with pytest.raises(ApprovalDecisionConflict):
        decide_authenticated_local_fixture_approval(
            identity,
            session_token=identity_context["session_token"],
            requested_site_id=scopes[0].site_id,
            current_recovery_generation=identity_context["generation"],
            approval_request_id=proposal.approval_request_id,
            expected_revision_sha256=proposal.revision_sha256,
            decision="rejected",
            decision_id=uuid4(),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        identity.execute("DELETE FROM app.approval_decisions")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="immutable"):
        admin.execute(
            "UPDATE app.approval_decisions SET decision = 'rejected' "
            "WHERE tenant_id = %s AND site_id = %s",
            (scopes[0].tenant_id, scopes[0].site_id),
        )


def test_concurrent_prepare_retries_converge_on_one_revision(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    _make_owner(admin, scopes[0], identity_context)
    _complete_fixture_finding(identity, scheduler, workflow, scopes[0], identity_context)
    barrier = Barrier(4, timeout=10)

    def issue():
        with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as connection:
            barrier.wait()
            return _prepare(connection, scopes[0], identity_context)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: issue(), range(4)))

    assert len({result.proposal_id for result in results}) == 1
    assert len({result.revision_id for result in results}) == 1
    assert len({result.approval_request_id for result in results}) == 1
    assert sum(not result.reused for result in results) == 1
    assert admin.execute(
        "SELECT count(*) FROM app.proposal_revisions WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (1,)


def test_model_run_is_durable_before_call_and_seals_exact_supervised_revision(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    scope = scopes[0]
    _make_owner(admin, scope, identity_context)
    finding = _complete_fixture_finding(identity, scheduler, workflow, scope, identity_context)
    task = build_fixture_metadata_task(site_id=scope.site_id, finding=finding)

    run = begin_authenticated_fixture_model_run(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        finding=finding,
        task=task,
    )

    assert run.outcome == "started"
    assert run.status == "requested"
    assert admin.execute(
        "SELECT status, model_requested FROM app.model_calls "
        "WHERE tenant_id = %s AND site_id = %s AND id = %s",
        (scope.tenant_id, scope.site_id, run.model_call_id),
    ).fetchone() == ("requested", "gpt-6-luna")
    proposal = complete_authenticated_fixture_model_proposal(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        run=run,
        draft=_draft(),
    )

    assert proposal.approval_status == "pending"
    assert proposal.manifest["proposal_kind"] == "model_fixture_metadata_draft"
    assert proposal.manifest["target"]["after"] == _draft().meta_description
    assert proposal.manifest["model"]["model_requested"] == "gpt-6-luna"
    assert proposal.manifest["model"]["provider_response_id"] == "resp_fixture_model_1"
    assert proposal.manifest["model"]["usage"]["total_tokens"] == 160
    assert proposal.manifest["model"]["store"] is False
    assert proposal.manifest["authority"]["external_write"] is False
    assert admin.execute(
        "SELECT run.status, call.status, call.total_tokens "
        "FROM app.agent_runs AS run JOIN app.model_calls AS call "
        "ON call.tenant_id = run.tenant_id AND call.site_id = run.site_id "
        "AND call.agent_run_id = run.id WHERE run.tenant_id = %s "
        "AND run.site_id = %s AND run.id = %s",
        (scope.tenant_id, scope.site_id, run.agent_run_id),
    ).fetchone() == ("completed", "completed", 160)
    replay = begin_authenticated_fixture_model_run(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        finding=finding,
        task=task,
    )
    assert replay.outcome == "completed"
    assert replay.model_call_id == run.model_call_id


def test_operator_metadata_model_is_bound_before_dispatch_and_cannot_be_swapped(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    from signal_core.proposals import ModelInputConflict

    scope = scopes[0]
    _make_owner(admin, scope, identity_context)
    finding = _complete_fixture_finding(identity, scheduler, workflow, scope, identity_context)
    task = build_fixture_metadata_task(site_id=scope.site_id, finding=finding)
    kwargs = dict(
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        finding=finding,
        task=task,
    )
    run = begin_authenticated_fixture_model_run(
        identity, **kwargs, model_requested="synthetic-operator-model"
    )
    assert admin.execute(
        "SELECT model_requested FROM app.model_calls WHERE id=%s", (run.model_call_id,)
    ).fetchone() == ("synthetic-operator-model",)
    with pytest.raises(ModelInputConflict):
        begin_authenticated_fixture_model_run(identity, **kwargs, model_requested="gpt-6-luna")
    complete_kwargs = dict(
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        run=run,
    )
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="model_release_mismatch"):
        complete_authenticated_fixture_model_proposal(identity, **complete_kwargs, draft=_draft())
    draft = replace(
        _draft(),
        model_requested="synthetic-operator-model",
        model_reported="synthetic-operator-model-1",
    )
    proposal = complete_authenticated_fixture_model_proposal(
        identity, **complete_kwargs, draft=draft
    )
    assert proposal.manifest["model"]["model_requested"] == "synthetic-operator-model"
    assert proposal.approval_status == "pending"
    assert proposal.manifest["authority"]["external_write"] is False


def test_model_failure_can_retry_but_unknown_outcome_blocks_blind_retry(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    scope = scopes[0]
    _make_owner(admin, scope, identity_context)
    finding = _complete_fixture_finding(identity, scheduler, workflow, scope, identity_context)
    task = build_fixture_metadata_task(site_id=scope.site_id, finding=finding)
    first = begin_authenticated_fixture_model_run(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        finding=finding,
        task=task,
    )
    fail_authenticated_fixture_model_run(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        run=first,
        error_code="MODEL_RATE_LIMITED",
        outcome_unknown=False,
    )
    second = begin_authenticated_fixture_model_run(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        finding=finding,
        task=task,
    )
    assert second.attempt_number == 2
    fail_authenticated_fixture_model_run(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        run=second,
        error_code="MODEL_TRANSPORT_UNAVAILABLE",
        outcome_unknown=True,
    )
    with pytest.raises(ModelOutcomeUnknown):
        begin_authenticated_fixture_model_run(
            identity,
            session_token=identity_context["session_token"],
            requested_site_id=scope.site_id,
            current_recovery_generation=identity_context["generation"],
            finding=finding,
            task=task,
        )


def test_model_output_identity_and_direct_mutation_fail_closed(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    scope = scopes[0]
    _make_owner(admin, scope, identity_context)
    finding = _complete_fixture_finding(identity, scheduler, workflow, scope, identity_context)
    task = build_fixture_metadata_task(site_id=scope.site_id, finding=finding)
    run = begin_authenticated_fixture_model_run(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        finding=finding,
        task=task,
    )
    with pytest.raises(ValueError, match="output identity"):
        complete_authenticated_fixture_model_proposal(
            identity,
            session_token=identity_context["session_token"],
            requested_site_id=scope.site_id,
            current_recovery_generation=identity_context["generation"],
            run=run,
            draft=replace(_draft(), output_sha256="0" * 64),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        identity.execute("SELECT * FROM app.model_calls")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="prohibited"):
        admin.execute(
            "DELETE FROM app.agent_runs WHERE tenant_id = %s AND site_id = %s AND id = %s",
            (scope.tenant_id, scope.site_id, run.agent_run_id),
        )
