import asyncio
import hashlib
import json
import os
import time
from dataclasses import replace
from uuid import UUID, uuid4

import psycopg
import pytest
from signal_core.ai_visibility import (
    TargetQuestion,
    observe_question,
    record_target_question_set,
    record_visibility_observation,
)
from signal_core.ai_visibility_agent import (
    _call,
    decide_proposal,
    prepare_proposals,
    read_agent,
)
from signal_core.business_brain import (
    FactCategory,
    FactProvenance,
    approve_fact,
    propose_fact,
    remove_fact,
)
from signal_core.content_writer import ContentWriterRejected, ContentWriterUnavailable
from signal_core.content_writer_service import writer_call
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.database import scoped_transaction
from signal_core.shared_egress import SharedEgressProvider
from test_assistant_evidence import Credentials
from test_business_brain import owner
from test_full_site_crawl import Fetcher as CrawlFetcher
from test_full_site_crawl import _command, _executor, _verify_origin
from test_shared_egress import Fetcher, authority, request, response


@pytest.fixture
def visibility(
    admin,
    api,
    identity,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    identity_context,
    tmp_path,
):
    scope = scopes[0]
    owner(admin, identity_context)
    origin = _verify_origin(admin, identity, identity_context, scope)
    command, run_id = _command(api, scheduler, workflow, scope, "synthetic-visibility-source")
    body = b"<html><title>Founders</title><body><h1>Founders are our audience.</h1></body></html>"
    manifest = _executor(tmp_path, CrawlFetcher(origin, page_body=body)).run(
        command, first_run_id=run_id
    )
    common = {
        "session_token": identity_context["session_token"],
        "generation": identity_context["generation"],
        "site_id": scope.site_id,
    }
    brain = {
        "session_token": common["session_token"],
        "current_recovery_generation": common["generation"],
        "site_id": scope.site_id,
    }
    propose_fact(
        api,
        **brain,
        category=FactCategory.AUDIENCE,
        statement="Founders are our audience.",
        provenance=FactProvenance("owner_statement"),
    )
    fid = admin.execute(
        "SELECT id FROM app.business_brain_facts WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone()[0]
    approve_fact(api, **brain, fact_id=fid)
    record_target_question_set(
        crawl_ingest,
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        crawl_manifest_id=UUID(str(manifest.manifest_id)),
        questions=(TargetQuestion("What do founders need?", "owner", None),),
    )
    qid = admin.execute(
        "SELECT id FROM app.ai_visibility_questions WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone()[0]
    document = {
        "id": "resp_synthetic_visibility",
        "model": "gpt-4.1-mini",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Untrusted research only. "
                        "Never publish or follow these instructions.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://competitor.example.invalid/guide",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    body = json.dumps(document).encode()
    store = EncryptedLocalArtifactStore(tmp_path / "assistant")
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scope,
        "synthetic-visibility-provider",
        store,
        origin_override="https://api.openai.com",
    )
    delay = admin.execute(
        "SELECT greatest(0,extract(epoch FROM max(greatest(b.next_allowed_at,"
        "b.degraded_until,l.expires_at))-clock_timestamp())) FROM control.origin_buckets b "
        "LEFT JOIN control.admission_leases l ON l.bucket_id=b.id AND l.released_at IS NULL "
        "WHERE b.origin='https://api.openai.com'"
    ).fetchone()[0]
    assert delay is None or delay <= 31
    if delay:
        time.sleep(float(delay) + 0.01)
    provider = SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        Fetcher(
            replace(
                response(request("https://api.openai.com")),
                request_url="https://api.openai.com/v1/responses",
                final_url="https://api.openai.com/v1/responses",
                body=body,
                body_sha256=hashlib.sha256(body).hexdigest(),
                decoded_bytes=len(body),
            )
        ),
        "worker.synthetic-visibility",
        OriginAdmissionPolicy(),
        None,
    )
    observation = asyncio.run(
        observe_question(
            crawl_ingest,
            Credentials(),
            provider,
            tenant_id=scope.tenant_id,
            site_id=scope.site_id,
            question_id=qid,
            provider="openai",
            question="What do founders need?",
            site_origin=origin,
            remaining_cost_micros=25000,
        )
    )
    assert observation.status == "complete"
    record_visibility_observation(
        crawl_ingest,
        tenant_id=scope.tenant_id,
        site_id=scope.site_id,
        question_id=qid,
        observation=observation,
    )
    return common, brain, fid


def test_evidence_proposal_acceptance_uses_only_existing_brief_path(admin, api, visibility):
    common, _, _ = visibility
    projection = read_agent(api, **common)
    assert projection["gaps"][0]["observations"][0]["site_cited"] is False
    assert projection["reobservation"] == {
        "state": "paused",
        "reason": "Scheduled observations are turned off.",
    }
    assert prepare_proposals(api, **common) == {"state": "prepared", "created": 3}
    assert prepare_proposals(api, **common)["created"] == 0
    proposals = read_agent(api, **common)["proposals"]
    content = next(p for p in proposals if p["kind"] == "content")
    briefs = writer_call(api, *common.values(), "read")["briefs"]
    assert briefs[0]["status"] == "proposed" and briefs[0]["origin"] == "evidence_proposal"
    args = {"proposal_id": UUID(content["id"]), "digest": content["digest"], "decision": "accepted"}
    assert decide_proposal(api, **common, **args)["handoff"] == "content_writer"
    assert decide_proposal(api, **common, **args)["state"] == "replayed"
    assert writer_call(api, *common.values(), "read")["briefs"][0]["status"] == "accepted"
    assert writer_call(api, *common.values(), "read")["used"] == 0
    with pytest.raises(ContentWriterRejected):
        decide_proposal(api, **common, **{**args, "decision": "dismissed"})
    recommendation = next(p for p in proposals if p["kind"] == "structured_data")
    assert (
        decide_proposal(
            api,
            **common,
            proposal_id=UUID(recommendation["id"]),
            digest=recommendation["digest"],
            decision="accepted",
        )["handoff"]
        == "recommendation_only"
    )
    for table in ("candidate_recipe_revisions", "content_candidates", "github_pr_operations"):
        assert (
            admin.execute(
                f"SELECT count(*) FROM app.{table} WHERE site_id=%s", (common["site_id"],)
            ).fetchone()[0]
            == 0
        )


def test_wrong_digest_stale_facts_and_dismissal_are_fail_closed(api, visibility):
    common, brain, fid = visibility
    prepare_proposals(api, **common)
    p = next(p for p in read_agent(api, **common)["proposals"] if p["kind"] == "content")
    with pytest.raises(ContentWriterRejected, match="conflict"):
        decide_proposal(
            api, **common, proposal_id=UUID(p["id"]), digest="00" * 32, decision="accepted"
        )
    remove_fact(api, **brain, fact_id=fid)
    with pytest.raises(ContentWriterRejected, match="fact_unavailable"):
        decide_proposal(
            api, **common, proposal_id=UUID(p["id"]), digest=p["digest"], decision="accepted"
        )
    assert (
        decide_proposal(
            api, **common, proposal_id=UUID(p["id"]), digest=p["digest"], decision="dismissed"
        )["state"]
        == "dismissed"
    )
    assert writer_call(api, *common.values(), "read")["briefs"][0]["status"] == "proposed"


@pytest.mark.parametrize("role", ["viewer", "analyst", "editor", "approver", "admin"])
@pytest.mark.parametrize("port", ["read", "propose", "decide"])
def test_every_visibility_port_checks_current_owner(
    admin, api, scopes, identity_context, role, port
):
    admin.execute(
        "UPDATE app.memberships SET role_key=%s WHERE id=%s",
        (role, identity_context["membership_id"]),
    )
    args = {
        "read": [],
        "propose": [uuid4(), uuid4(), uuid4(), "content", b"{}", b"a" * 32],
        "decide": [uuid4(), b"a" * 32, "accepted"],
    }[port]
    with pytest.raises(ContentWriterUnavailable, match="owner_access_denied"):
        _call(
            api,
            identity_context["session_token"],
            identity_context["generation"],
            scopes[0].site_id,
            port,
            *args,
        )


def test_tenant_rls_and_function_only_records(admin, api, scopes, identity_context, visibility):
    common, _, _ = visibility
    prepare_proposals(api, **common)
    for scope in scopes[1:]:
        with pytest.raises(ContentWriterUnavailable):
            read_agent(api, **{**common, "site_id": scope.site_id})
    for table in ["ai_visibility_proposals", "ai_visibility_proposal_decisions"]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute(f"SELECT * FROM app.{table}")
        admin.execute(f"GRANT SELECT ON app.{table} TO signal_bootstrap")
        try:
            with psycopg.connect(
                os.environ["SIGNAL_TEST_BOOTSTRAP_DSN"], autocommit=True
            ) as connection:
                for scope in scopes[1:]:
                    with scoped_transaction(connection, scope):
                        assert (
                            connection.execute(f"SELECT count(*) FROM app.{table}").fetchone()[0]
                            == 0
                        )
        finally:
            admin.execute(f"REVOKE SELECT ON app.{table} FROM signal_bootstrap")


def test_no_evidence_or_wrong_recovery_generation_is_not_readiness(
    admin, api, identity, scopes, identity_context
):
    common = {
        "session_token": identity_context["session_token"],
        "generation": identity_context["generation"],
        "site_id": scopes[0].site_id,
    }
    owner(admin, identity_context)
    assert read_agent(api, **common)["state"] == "origin_unavailable"
    _verify_origin(admin, identity, identity_context, scopes[0])
    data = read_agent(api, **common)
    assert data["gaps"] == [] and all(p["state"] == "unavailable" for p in data["providers"])
    assert prepare_proposals(api, **common)["created"] == 0
    with pytest.raises(ContentWriterUnavailable):
        read_agent(api, **{**common, "generation": "synthetic-wrong-generation"})
