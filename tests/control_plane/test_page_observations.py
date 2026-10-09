import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from signal_core.audit_findings import read_authenticated_findings
from signal_core.authorization import AuthorizationDenied
from signal_core.commands import accept_authenticated_snapshot
from signal_core.model_reasoning import MetadataDraft, ModelUsage
from signal_core.origin_verification import (
    OriginProofObservation,
    issue_origin_challenge,
    prepare_origin_verification,
    record_origin_verification,
)
from signal_core.outbox_dispatch import claim_outbox_batch
from signal_core.page_observations import (
    ObservedPageMetadata,
    OriginNotVerified,
    PageObservationFailed,
    prepare_authenticated_page_observation,
    read_latest_authenticated_page_observation,
    record_authenticated_page_observation,
)
from signal_core.proposals import (
    ProposalNotReady,
    begin_authenticated_verified_homepage_model_run,
    build_verified_homepage_metadata_task,
    complete_authenticated_verified_homepage_model_proposal,
)
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_contracts import CrawlManifestReference, CrawlTerminalInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started
from signal_core.workflow_terminal import record_crawl_terminal


def make_owner(admin, identity_context):
    admin.execute(
        "UPDATE app.memberships SET role_key = 'owner', authorization_epoch = 2 WHERE id = %s",
        (identity_context["membership_id"],),
    )


def verify_site(admin, identity, scope, context):
    origin = f"https://site-{scope.site_id.hex}.example.invalid"
    admin.execute(
        "UPDATE app.sites SET primary_origin = %s WHERE tenant_id = %s AND id = %s",
        (origin, scope.tenant_id, scope.site_id),
    )
    challenge = issue_origin_challenge(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        requested_site_id=scope.site_id,
        origin=origin,
        idempotency_key=uuid4(),
    )
    request_id = uuid4()
    prepared = prepare_origin_verification(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        requested_site_id=scope.site_id,
        challenge_id=challenge.challenge_id,
        origin=challenge.origin,
        idempotency_key=request_id,
    )
    observation = OriginProofObservation(
        outcome="matched",
        http_status=200,
        media_type="text/plain",
        response_sha256=prepared.proof_sha256,
        final_url=prepared.proof_url,
        resolved_address="93.184.216.34",
        elapsed_ms=12,
    )
    return record_origin_verification(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        requested_site_id=scope.site_id,
        challenge_id=challenge.challenge_id,
        origin=challenge.origin,
        idempotency_key=request_id,
        observation=observation,
    )


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
        worker_key="worker.page-observation",
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
        manifest_sha256="b" * 64,
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


def prepare(identity, scope, context, request_id):
    return prepare_authenticated_page_observation(
        identity,
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
        idempotency_key=request_id,
    )


def observed(html, *, origin, meta_description=None):
    return ObservedPageMetadata(
        fetch_outcome="observed",
        http_status=200,
        media_type="text/html",
        final_url=f"{origin}/",
        resolved_address="93.184.216.34",
        body_sha256=hashlib.sha256(html.encode()).hexdigest(),
        title="Example product",
        heading="Example heading",
        meta_description=meta_description,
        elapsed_ms=21,
    )


def record(identity, scope, context, prepared, page):
    return record_authenticated_page_observation(
        identity,
        session_token=context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=context["generation"],
        prepared=prepared,
        observed=page,
    )


def model_draft():
    output = {
        "meta_description": (
            "Explore the example product through its verified homepage and understand the "
            "main topic described by the observed title and heading."
        ),
        "rationale": "The draft uses only the verified homepage title and heading.",
    }
    canonical = json.dumps(
        output, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return MetadataDraft(
        meta_description=output["meta_description"],
        rationale=output["rationale"],
        provider_response_id="resp_verified_homepage_1",
        model_reported="gpt-6-luna-2026-09-20",
        usage=ModelUsage(120, 35, 155, 0),
        output_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def test_page_observation_requires_current_verified_origin_and_completed_audit(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    make_owner(admin, identity_context)
    completed_audit(identity, scheduler, workflow, scopes[0], identity_context, "page-not-verified")
    with pytest.raises(OriginNotVerified):
        prepare(identity, scopes[0], identity_context, uuid4())

    verify_site(admin, identity, scopes[0], identity_context)
    prepared = prepare(identity, scopes[0], identity_context, uuid4())
    assert prepared.origin == f"https://site-{scopes[0].site_id.hex}.example.invalid"


def test_verified_homepage_observation_persists_evidence_and_deterministic_finding(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    make_owner(admin, identity_context)
    verified = verify_site(admin, identity, scopes[0], identity_context)
    command, manifest = completed_audit(
        identity, scheduler, workflow, scopes[0], identity_context, "page-finding"
    )
    request_id = uuid4()
    prepared = prepare(identity, scopes[0], identity_context, request_id)
    replay = prepare(identity, scopes[0], identity_context, request_id)
    page = observed(
        "<html><head><title>Example product</title></head></html>",
        origin=prepared.origin,
    )

    result = record(identity, scopes[0], identity_context, prepared, page)
    duplicate = record(identity, scopes[0], identity_context, prepared, page)
    latest = read_latest_authenticated_page_observation(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=identity_context["generation"],
    )

    verification_id = admin.execute(
        "SELECT id FROM app.site_origin_verifications "
        "WHERE tenant_id = %s AND site_id = %s AND challenge_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id, verified.challenge_id),
    ).fetchone()[0]
    assert prepared.verification_id == verification_id
    assert prepared.command_id == command.id
    assert prepared.manifest_id.hex == manifest.manifest_id.replace("-", "")
    assert replay == replace(prepared, replayed=True)
    assert result.finding_id is not None
    assert result.meta_description is None
    assert duplicate == replace(result, reused=True)
    assert latest == replace(result, reused=False)
    facts = admin.execute(
        "SELECT source_kind, rights_status, quality, facts FROM app.evidence_records "
        "WHERE tenant_id = %s AND site_id = %s AND id = %s",
        (scopes[0].tenant_id, scopes[0].site_id, result.evidence_id),
    ).fetchone()
    assert facts[0:2] == ("verified_origin", "verified_owner")
    assert facts[2]["customer_origin_read"] is True
    assert facts[3]["final_url"] == f"{prepared.origin}/"
    assert facts[3]["meta_description"] is None


def test_present_meta_description_records_observation_without_inventing_finding(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    make_owner(admin, identity_context)
    verify_site(admin, identity, scopes[0], identity_context)
    completed_audit(identity, scheduler, workflow, scopes[0], identity_context, "page-clean")
    prepared = prepare(identity, scopes[0], identity_context, uuid4())
    result = record(
        identity,
        scopes[0],
        identity_context,
        prepared,
        observed(
            "<html></html>",
            origin=prepared.origin,
            meta_description="A useful description.",
        ),
    )

    assert result.finding_id is None
    assert result.meta_description == "A useful description."
    assert admin.execute(
        "SELECT count(*) FROM app.findings WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (0,)


def test_known_fetch_failure_is_durable_without_evidence_or_finding(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    make_owner(admin, identity_context)
    verify_site(admin, identity, scopes[0], identity_context)
    completed_audit(identity, scheduler, workflow, scopes[0], identity_context, "page-failure")
    prepared = prepare(identity, scopes[0], identity_context, uuid4())
    failure = ObservedPageMetadata(
        fetch_outcome="transport_unavailable",
        http_status=None,
        media_type=None,
        final_url=None,
        resolved_address=None,
        body_sha256=None,
        title=None,
        heading=None,
        meta_description=None,
        elapsed_ms=0,
    )

    with pytest.raises(PageObservationFailed) as captured:
        record(identity, scopes[0], identity_context, prepared, failure)
    assert captured.value.outcome == "transport_unavailable"
    assert admin.execute(
        "SELECT fetch_outcome FROM app.page_observation_results "
        "WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchall() == [("transport_unavailable",)]
    assert admin.execute(
        "SELECT count(*) FROM app.evidence_records WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (0,)


def test_observation_rechecks_current_authority_and_tables_are_function_only(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    make_owner(admin, identity_context)
    verify_site(admin, identity, scopes[0], identity_context)
    completed_audit(identity, scheduler, workflow, scopes[0], identity_context, "page-authority")
    prepared = prepare(identity, scopes[0], identity_context, uuid4())
    page = observed("<html></html>", origin=prepared.origin)
    record(identity, scopes[0], identity_context, prepared, page)
    admin.execute(
        "UPDATE app.sites SET ownership_status = 'reverification_required' "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    )
    with pytest.raises(AuthorizationDenied):
        record(identity, scopes[0], identity_context, prepared, page)

    for table in ("page_observation_intents", "page_observation_results"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            identity.execute(f"SELECT count(*) FROM app.{table}")
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(
                f"DELETE FROM app.{table} WHERE tenant_id = %s AND site_id = %s",
                (scopes[0].tenant_id, scopes[0].site_id),
            )


def test_verified_homepage_finding_seals_one_model_proposal(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    scope = scopes[0]
    make_owner(admin, identity_context)
    verify_site(admin, identity, scope, identity_context)
    completed_audit(identity, scheduler, workflow, scope, identity_context, "verified-proposal")
    prepared = prepare(identity, scope, identity_context, uuid4())
    observation = record(
        identity,
        scope,
        identity_context,
        prepared,
        observed("<html></html>", origin=prepared.origin),
    )
    finding = next(
        item
        for item in read_authenticated_findings(
            identity,
            session_token=identity_context["session_token"],
            requested_site_id=scope.site_id,
            current_recovery_generation=identity_context["generation"],
        )
        if item.id == observation.finding_id
    )
    task = build_verified_homepage_metadata_task(
        site_id=scope.site_id, finding=finding, observation=observation
    )
    run = begin_authenticated_verified_homepage_model_run(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        finding=finding,
        task=task,
    )
    proposal = complete_authenticated_verified_homepage_model_proposal(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        run=run,
        draft=model_draft(),
    )
    replay = begin_authenticated_verified_homepage_model_run(
        identity,
        session_token=identity_context["session_token"],
        requested_site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
        finding=finding,
        task=task,
    )

    assert run.outcome == "started"
    assert replay.outcome == "completed"
    assert replay.model_call_id == run.model_call_id
    assert proposal.manifest["proposal_kind"] == "model_verified_homepage_metadata_draft"
    assert proposal.manifest["target"]["resource_locator"] == observation.final_url
    assert proposal.manifest["assessment"]["customer_origin_read"] is True
    assert proposal.manifest["authority"] == {
        "approval_class": "A1",
        "requested": "accept_verified_homepage_metadata_draft",
        "external_write": False,
    }
    assert proposal.approval_status == "pending"
    assert admin.execute(
        "SELECT release_key, status FROM app.agent_runs "
        "WHERE tenant_id = %s AND site_id = %s AND id = %s",
        (scope.tenant_id, scope.site_id, run.agent_run_id),
    ).fetchone() == ("verified-gpt-6-luna-metadata-v2", "completed")


def test_verified_homepage_proposal_requires_observed_page_semantics(
    admin, identity, scheduler, workflow, scopes, identity_context
):
    scope = scopes[0]
    make_owner(admin, identity_context)
    verify_site(admin, identity, scope, identity_context)
    completed_audit(identity, scheduler, workflow, scope, identity_context, "weak-evidence")
    prepared = prepare(identity, scope, identity_context, uuid4())
    observation = record(
        identity,
        scope,
        identity_context,
        prepared,
        replace(
            observed("<html></html>", origin=prepared.origin),
            title=None,
            heading=None,
        ),
    )
    finding = next(
        item
        for item in read_authenticated_findings(
            identity,
            session_token=identity_context["session_token"],
            requested_site_id=scope.site_id,
            current_recovery_generation=identity_context["generation"],
        )
        if item.id == observation.finding_id
    )

    with pytest.raises(ProposalNotReady):
        build_verified_homepage_metadata_task(
            site_id=scope.site_id, finding=finding, observation=observation
        )
