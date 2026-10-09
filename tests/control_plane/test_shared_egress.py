import hashlib
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import httpx2
import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.commands import accept_snapshot
from signal_core.crawl_admission import (
    OriginAdmissionPolicy,
    OriginPermitGrant,
    acquire_origin_permit,
)
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.crawl_frontier import CrawlRunLimits, claim_crawl_frontier, open_crawl_run
from signal_core.crawl_http import (
    CrawlFetchRejected,
    CrawlFetchUnavailable,
    EgressHttpRequest,
    EgressHttpResult,
    RobotsFetchResult,
)
from signal_core.crawl_robots import persist_robots_snapshot
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.egress_profiles import EgressProfile, GitHubRepositoryWriteScope
from signal_core.github_app import GitHubRepositoryTarget
from signal_core.github_pr_patch import GitHubPrPatch
from signal_core.github_pr_provider import GitHubWriteEgressTransport
from signal_core.outbox_dispatch import claim_outbox_batch, mark_outbox_delivered
from signal_core.shared_egress import (
    SharedEgressConflict,
    SharedEgressDeferred,
    SharedEgressPending,
    SharedEgressProvider,
    SharedEgressReceipt,
    SharedEgressRequest,
    execute_shared_egress,
)
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_contracts import CrawlSiteWorkflowInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started

NETWORK_PROFILE = "ab" * 32


def authority(
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scope,
    key,
    store,
    origin=None,
    *,
    origin_override=None,
    github_profile=False,
    verification_profile=False,
    seed_url=None,
    verification_path="",
):
    origin = origin_override or origin or f"https://{key}.egress.example"
    command = accept_snapshot(api, scope, actor_service="egress-test", idempotency_key=key)
    envelopes = claim_outbox_batch(
        scheduler,
        tenant_id=scope.tenant_id,
        worker_key="worker.shared-egress",
        batch_size=100 if github_profile or verification_profile else 1,
    )
    envelope = next(item for item in envelopes if item.command_id == command.id)
    admitted = admit_command_event(workflow, envelope)
    started = record_workflow_started(
        workflow,
        admitted,
        WorkflowStartReceipt(admitted.workflow_id, str(uuid4()), "start_acknowledged"),
    )
    if github_profile or verification_profile:
        mark_outbox_delivered(
            scheduler,
            tenant_id=scope.tenant_id,
            outbox_id=envelope.outbox_id,
            worker_key="worker.shared-egress",
            attempt_count=envelope.attempt_count,
        )
    crawl_input = CrawlSiteWorkflowInput(
        schema_version=1,
        tenant_id=str(scope.tenant_id),
        site_id=str(scope.site_id),
        command_id=str(command.id),
        source_event_id=str(admitted.source_event_id),
        scope_version=1,
        crawl_policy_version=1,
    )
    policy = CrawlScopePolicy(
        schema_version=1,
        allowed_origins=(origin,),
        user_agent="SignalBot/1.0 (+https://signal.example/bot)",
        request_timeout_seconds=2,
        total_timeout_seconds=5,
        max_redirects=0 if github_profile or verification_profile else 5,
        max_body_bytes=128 * 1024
        if verification_profile
        else 256 * 1024
        if github_profile
        else 5 * 1024 * 1024,
    )
    run = open_crawl_run(
        crawl_admission,
        crawl_input,
        first_run_id=started.first_run_id,
        policy=policy,
        limits=CrawlRunLimits(),
        seed_url=seed_url or f"{origin}/{verification_path}",
    )
    observed_at = datetime.now(UTC)
    snapshot = persist_robots_snapshot(
        crawl_ingest,
        store,
        run,
        policy,
        RobotsFetchResult(
            schema_version=1,
            origin=origin,
            robots_url=f"{origin}/robots.txt",
            final_url=f"{origin}/robots.txt",
            outcome="not_found",
            http_status=404,
            media_type=None,
            response_headers=(),
            redirect_chain=(),
            resolved_address="8.8.8.8",
            body=b"",
            body_sha256=None,
            decoded_bytes=0,
            elapsed_ms=5,
        ),
        snapshot_id=uuid4(),
        fetched_at=observed_at,
        network_profile_sha256=NETWORK_PROFILE,
        artifact_key=None,
        retain_until=None,
        recorded_at=observed_at,
    )
    return run, policy, snapshot


def request(origin: str) -> SharedEgressRequest:
    return SharedEgressRequest(
        "model",
        EgressHttpRequest(
            "POST",
            f"{origin}/v1/decision",
            headers=(
                ("authorization", "Bearer synthetic-provider-key"),
                ("content-type", "application/json"),
                ("accept", "application/json"),
            ),
            body=b'{"question":"synthetic"}',
        ),
        EgressProfile.MODEL_JSON,
    )


class Fetcher:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def request(self, outbound, *, policy):
        self.calls.append((outbound, policy))
        if self.error is not None:
            raise self.error
        return self.result


class GitHubDoubleFetcher:
    def __init__(self, *, operation_id):
        self.operation_id = operation_id
        self.calls = []

    def request(self, outbound, *, policy):
        self.calls.append(outbound)
        time.sleep(0.005)
        body = json.dumps(
            {
                "ref": f"refs/heads/signal/{self.operation_id.hex}",
                "object": {"type": "commit", "sha": "c" * 40},
            }
        ).encode()
        return EgressHttpResult(
            schema_version=1,
            request_url=outbound.url,
            final_url=outbound.url,
            method=outbound.method,
            outcome="fetched",
            http_status=201,
            media_type="application/json",
            response_headers=(("content-type", "application/json"),),
            resolved_address="8.8.8.8",
            body=body,
            body_sha256=hashlib.sha256(body).hexdigest(),
            decoded_bytes=len(body),
            elapsed_ms=5,
        )


def response(outbound: SharedEgressRequest, *, status=200, retry_after=None):
    body = b'{"answer":"ask_owner"}'
    headers = [("content-type", "application/json")]
    if retry_after is not None:
        headers.append(("retry-after", retry_after))
    return EgressHttpResult(
        schema_version=1,
        request_url=outbound.http.url,
        final_url=outbound.http.url,
        method=outbound.http.method,
        outcome="fetched",
        http_status=status,
        media_type="application/json",
        response_headers=tuple(sorted(headers)),
        resolved_address="8.8.8.8",
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        decoded_bytes=len(body),
        elapsed_ms=12,
    )


def test_shared_egress_persists_exact_evidence_and_never_refetches(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "objects")
    run, policy, snapshot = authority(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scopes[0], "success", store
    )
    outbound = request(policy.allowed_origins[0])
    fetcher = Fetcher(response(outbound))
    operation_id = uuid4()

    first = execute_shared_egress(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        fetcher,
        outbound,
        operation_id=operation_id,
        worker_key="worker.model-egress",
        admission_policy=OriginAdmissionPolicy(),
        artifact_key=None,
    )
    replay = execute_shared_egress(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        fetcher,
        outbound,
        operation_id=operation_id,
        worker_key="worker.model-egress",
        admission_policy=OriginAdmissionPolicy(),
        artifact_key=None,
    )

    assert isinstance(first, SharedEgressReceipt)
    assert first.egress_profile == EgressProfile.MODEL_JSON
    assert first.state == "observed"
    assert first.response.body == b'{"answer":"ask_owner"}'
    assert len(fetcher.calls) == 1
    assert replay == SharedEgressPending(operation_id, "body_not_retained")
    stored = admin.execute(
        "SELECT purpose, egress_profile, method, request_url, encode(request_sha256, 'hex'), "
        "credentialed, robots_snapshot_id, state, network_outcome, http_status, "
        "encode(response_sha256, 'hex'), response_bytes, resolved_address::text "
        "FROM app.egress_operations WHERE tenant_id = %s AND site_id = %s AND id = %s",
        (run.tenant_id, run.site_id, operation_id),
    ).fetchone()
    assert stored == (
        "model",
        "model_json",
        "POST",
        outbound.http.url,
        outbound.request_sha256.hex(),
        True,
        snapshot.snapshot_id,
        "observed",
        "fetched",
        200,
        first.response.body_sha256,
        len(first.response.body),
        "8.8.8.8/32",
    )


def test_provider_adapter_uses_durable_boundary_without_storing_credential(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "provider")
    run, policy, _ = authority(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scopes[0], "provider", store
    )
    outbound = request(policy.allowed_origins[0])
    operation_id = uuid4()
    provider = SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        Fetcher(response(outbound)),
        "worker.provider-egress",
        OriginAdmissionPolicy(),
        None,
    )

    received = provider.post_json(
        url=outbound.http.url,
        authorization="Bearer synthetic-provider-key",
        body=outbound.http.body,
        operation_id=operation_id,
        timeout_seconds=20,
        max_response_bytes=128 * 1024,
        profile=EgressProfile.MODEL_JSON,
    )

    assert received.status_code == 200
    assert received.media_type == "application/json"
    assert received.body == b'{"answer":"ask_owner"}'
    stored = admin.execute(
        "SELECT to_jsonb(operation)::text FROM app.egress_operations AS operation "
        "WHERE tenant_id = %s AND site_id = %s AND id = %s",
        (run.tenant_id, run.site_id, operation_id),
    ).fetchone()[0]
    assert "synthetic-provider-key" not in stored
    assert "synthetic-provider-key" not in repr(provider)


def test_connector_get_uses_same_durable_boundary(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "connector-get")
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "connector-get",
        store,
        origin="https://api.github.com",
    )
    url = f"{policy.allowed_origins[0]}/repos/SignalOwner/website"
    outbound = SharedEgressRequest(
        "connector",
        EgressHttpRequest(
            "GET",
            url,
            headers=(
                ("accept", "application/vnd.github+json"),
                ("authorization", "Bearer synthetic-provider-key"),
                ("x-github-api-version", "2026-03-10"),
            ),
            accepted_media_types=("application/json", "application/vnd.github+json"),
            max_response_bytes=256 * 1024,
            timeout_seconds=5,
        ),
        EgressProfile.GITHUB_REST,
    )
    fetcher = Fetcher(response(outbound))
    provider = SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        fetcher,
        "worker.connector-egress",
        OriginAdmissionPolicy(),
        None,
        "connector",
    )
    received = provider.request_json(
        method="GET",
        url=url,
        profile=EgressProfile.GITHUB_REST,
        authorization="Bearer synthetic-provider-key",
        body=b"",
        operation_id=uuid4(),
        timeout_seconds=5,
        max_response_bytes=256 * 1024,
    )
    assert received.body == b'{"answer":"ask_owner"}'
    assert len(fetcher.calls) == 1
    stored = admin.execute(
        "SELECT method, purpose, credentialed, to_jsonb(operation)::text "
        "FROM app.egress_operations AS operation WHERE tenant_id = %s AND site_id = %s "
        "AND request_url = %s",
        (run.tenant_id, run.site_id, url),
    ).fetchone()
    assert stored[:3] == ("GET", "connector", True)
    assert "synthetic-provider-key" not in stored[3]
    assert "synthetic-provider-key" not in repr(received)


@pytest.mark.anyio
async def test_github_branch_write_double_stays_behind_fenced_shared_egress(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "github-write-egress")
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "github-write",
        store,
        origin_override="https://api.github.com",
        github_profile=True,
    )
    operation_id = uuid4()
    fetcher = GitHubDoubleFetcher(operation_id=operation_id)
    provider = SharedEgressProvider(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        fetcher,
        "worker.github-pr-egress",
        OriginAdmissionPolicy(),
        None,
        "connector",
    )
    permitted = []
    patch = GitHubPrPatch(
        "index.html",
        b"<title>Fixed</title>\n",
        "a" * 64,
        "b" * 40,
        "c" * 40,
        "Fix title\n",
        "2026-09-29T12:00:00Z",
    )
    transport = GitHubWriteEgressTransport(
        provider,
        GitHubRepositoryTarget(1234, "SignalOwner", "website", "main", "index.html"),
        permitted.append,
        operation_id=operation_id,
        patch=patch,
        base_sha="d" * 40,
        base_tree_sha="e" * 40,
        pr_body="Exact body",
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + "t" * 32,
        "User-Agent": "Signal-SEO-Agent/0.0.0",
        "X-GitHub-Api-Version": "2026-03-10",
        "Content-Type": "application/json",
    }
    request = httpx2.Request(
        "POST",
        "https://api.github.com/repos/SignalOwner/website/git/refs",
        headers=headers,
        json={"ref": f"refs/heads/signal/{operation_id.hex}", "sha": patch.commit_sha},
    )
    transport.write_scope = GitHubRepositoryWriteScope("SignalOwner", "website", "t" * 32)
    response = await transport.handle_async_request(request)
    assert response.status_code == 201
    assert permitted == ["branch"] and len(fetcher.calls) == 1
    stored = admin.execute(
        "SELECT purpose,method,request_url,state,egress_profile FROM app.egress_operations "
        "WHERE tenant_id=%s AND site_id=%s AND id=%s",
        (run.tenant_id, run.site_id, transport.last_egress_operation_id),
    ).fetchone()
    assert stored == ("connector", "POST", str(request.url), "observed", "github_repository_write")
    assert "Bearer" not in str(stored)
    with pytest.raises(httpx2.ConnectError):
        await transport.handle_async_request(
            httpx2.Request(
                "POST",
                "https://api.github.com/repos/SignalOwner/website/git/refs",
                headers=headers,
                json={"ref": "refs/heads/main", "sha": patch.commit_sha},
            )
        )
    assert len(fetcher.calls) == 1


@pytest.mark.parametrize(
    ("error", "expected_outcome", "completion"),
    [
        (CrawlFetchRejected(), "policy_rejected", "cancelled"),
        (CrawlFetchUnavailable(), "transport_error", "transport_error"),
    ],
)
def test_known_network_failures_are_terminal_and_release_capacity(
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    tmp_path,
    error,
    expected_outcome,
    completion,
):
    store = EncryptedLocalArtifactStore(tmp_path / expected_outcome)
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        expected_outcome.replace("_", "-"),
        store,
    )
    outbound = request(policy.allowed_origins[0])
    receipt = execute_shared_egress(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        Fetcher(error=error),
        outbound,
        operation_id=uuid4(),
        worker_key="worker.failure-egress",
        admission_policy=OriginAdmissionPolicy(),
        artifact_key=None,
    )

    assert isinstance(receipt, SharedEgressReceipt)
    assert receipt.state == "failed"
    assert receipt.network_outcome == expected_outcome
    assert receipt.completion_kind == completion
    assert receipt.active_in_flight == 0


def test_inconsistent_fetcher_result_is_rejected_and_never_returned(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "invalid-result")
    run, policy, _ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "invalid-result",
        store,
    )
    outbound = request(policy.allowed_origins[0])
    invalid = replace(response(outbound), body_sha256="00" * 32)

    receipt = execute_shared_egress(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        Fetcher(invalid),
        outbound,
        operation_id=uuid4(),
        worker_key="worker.invalid-result",
        admission_policy=OriginAdmissionPolicy(),
        artifact_key=None,
    )

    assert isinstance(receipt, SharedEgressReceipt)
    assert receipt.state == "failed"
    assert receipt.network_outcome == "policy_rejected"
    assert receipt.response.body == b""


def test_shared_egress_uses_the_same_global_origin_bucket_as_crawler(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "shared-bucket")
    run, policy, _ = authority(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scopes[0], "bucket", store
    )
    lease = claim_crawl_frontier(
        crawl_admission,
        run,
        worker_key="crawler.shared-bucket",
        lease_id=uuid4(),
        lease_seconds=60,
    )
    crawler_permit = acquire_origin_permit(
        crawl_admission,
        lease,
        permit_id=uuid4(),
        permit_kind="html_navigation",
        policy=OriginAdmissionPolicy(),
    )
    assert isinstance(crawler_permit, OriginPermitGrant)
    outbound = request(policy.allowed_origins[0])
    fetcher = Fetcher(response(outbound))

    result = execute_shared_egress(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        fetcher,
        outbound,
        operation_id=uuid4(),
        worker_key="worker.shared-bucket",
        admission_policy=OriginAdmissionPolicy(),
        artifact_key=None,
    )

    assert isinstance(result, SharedEgressDeferred)
    assert result.reason == "in_flight"
    assert fetcher.calls == []


def test_operation_identity_conflict_and_table_mutation_fail_closed(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "conflict")
    run, policy, _ = authority(
        api, scheduler, workflow, crawl_admission, crawl_ingest, scopes[0], "conflict", store
    )
    outbound = request(policy.allowed_origins[0])
    operation_id = uuid4()
    execute_shared_egress(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        Fetcher(response(outbound)),
        outbound,
        operation_id=operation_id,
        worker_key="worker.conflict-egress",
        admission_policy=OriginAdmissionPolicy(),
        artifact_key=None,
    )
    changed = SharedEgressRequest(
        "model",
        EgressHttpRequest(
            "POST",
            f"{policy.allowed_origins[0]}/v1/other",
            headers=outbound.http.headers,
            body=outbound.http.body,
        ),
        EgressProfile.MODEL_JSON,
    )
    with pytest.raises(SharedEgressConflict):
        execute_shared_egress(
            crawl_admission,
            crawl_ingest,
            store,
            run,
            policy,
            Fetcher(response(changed)),
            changed,
            operation_id=operation_id,
            worker_key="worker.conflict-egress",
            admission_policy=OriginAdmissionPolicy(),
            artifact_key=None,
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "DELETE FROM app.egress_operations WHERE tenant_id = %s AND id = %s",
            (run.tenant_id, operation_id),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.egress_operations SET egress_profile = 'jev' "
            "WHERE tenant_id = %s AND id = %s",
            (run.tenant_id, operation_id),
        )


def test_inconsistent_completion_is_rejected_without_releasing_dispatch(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "inconsistent")
    run, policy, snapshot = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "inconsistent",
        store,
    )
    outbound = request(policy.allowed_origins[0])
    operation_id = uuid4()
    begun = crawl_admission.execute(
        "SELECT bucket_id, outcome FROM control.begin_shared_egress_operation("
        + ", ".join(["%s"] * 19)
        + ")",
        (
            run.tenant_id,
            run.site_id,
            run.run_id,
            "worker.inconsistent-egress",
            operation_id,
            outbound.purpose,
            outbound.http.method,
            outbound.http.url,
            policy.allowed_origins[0],
            outbound.request_sha256,
            outbound.body_sha256,
            len(outbound.http.body),
            outbound.http.max_response_bytes,
            outbound.credentialed,
            snapshot.snapshot_id,
            "robots_not_found",
            1,
            1000,
            30,
        ),
    ).fetchone()
    assert begun[1] == "admitted"
    response_body = b'{"error":"rate limited"}'

    with pytest.raises(psycopg.errors.InvalidParameterValue):
        crawl_admission.execute(
            "SELECT * FROM control.finish_shared_egress_operation(" + ", ".join(["%s"] * 20) + ")",
            (
                run.tenant_id,
                run.site_id,
                run.run_id,
                "worker.inconsistent-egress",
                operation_id,
                begun[0],
                policy.allowed_origins[0],
                1,
                outbound.request_sha256,
                snapshot.snapshot_id,
                "fetched",
                429,
                Jsonb({"content-type": "application/json"}),
                hashlib.sha256(response_body).digest(),
                len(response_body),
                "application/json",
                "8.8.8.8",
                "success",
                12,
                None,
            ),
        )

    assert admin.execute(
        "SELECT operation.state, lease.released_at FROM app.egress_operations AS operation "
        "JOIN control.admission_leases AS lease ON lease.id = operation.origin_permit_id "
        "WHERE operation.tenant_id = %s AND operation.id = %s",
        (run.tenant_id, operation_id),
    ).fetchone() == ("dispatched", None)


def test_egress_table_is_forced_rls_and_function_only(admin, crawl_admission):
    assert admin.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'app.egress_operations'::regclass"
    ).fetchone() == (True, True)
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
        assert not admin.execute(
            "SELECT has_table_privilege('signal_crawl_admission', 'app.egress_operations', %s)",
            (privilege,),
        ).fetchone()[0]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        crawl_admission.execute("SELECT count(*) FROM app.egress_operations")
