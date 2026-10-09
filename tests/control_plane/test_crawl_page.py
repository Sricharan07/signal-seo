import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock
from uuid import uuid4

import psycopg
import pytest
import signal_core.crawl_page as crawl_page
from signal_core.commands import accept_snapshot
from signal_core.crawl_admission import (
    OriginAdmissionPolicy,
    OriginPermitCompletion,
    OriginPermitGrant,
    acquire_origin_permit,
    finish_origin_permit,
)
from signal_core.crawl_artifacts import (
    ArtifactEncryptionKey,
    ArtifactUnavailable,
    EncryptedLocalArtifactStore,
    load_fetch_observation,
    persist_fetch_observation,
)
from signal_core.crawl_frontier import (
    CrawlRunLimits,
    claim_crawl_frontier,
    open_crawl_run,
)
from signal_core.crawl_http import CrawlFetchRejected, CrawlFetchResult, CrawlFetchUnavailable
from signal_core.crawl_page import (
    CrawlPageAttemptBlocked,
    CrawlPageAttemptConflict,
    CrawlPageAttemptDeferred,
    CrawlPageAttemptPending,
    CrawlPageAttemptReceipt,
    execute_crawl_page_attempt,
)
from signal_core.crawl_robots import authorize_from_current_snapshot, persist_robots_snapshot
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.outbox_dispatch import claim_outbox_batch
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_contracts import CrawlSiteWorkflowInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started

NETWORK_PROFILE = "ef" * 32


def artifact_key():
    return ArtifactEncryptionKey("artifact-key:v1:page-test", bytes(range(32)))


def page_authority(
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scope,
    key,
    store,
    *,
    path="/",
    robots_body=None,
):
    origin = f"https://{key}.crawl.example"
    command = accept_snapshot(api, scope, actor_service="crawl-page-test", idempotency_key=key)
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scope.tenant_id,
        worker_key="worker.crawl-page",
        batch_size=1,
    )[0]
    admission = admit_command_event(workflow, envelope)
    started = record_workflow_started(
        workflow,
        admission,
        WorkflowStartReceipt(admission.workflow_id, str(uuid4()), "start_acknowledged"),
    )
    crawl_input = CrawlSiteWorkflowInput(
        schema_version=1,
        tenant_id=str(scope.tenant_id),
        site_id=str(scope.site_id),
        command_id=str(command.id),
        source_event_id=str(admission.source_event_id),
        scope_version=1,
        crawl_policy_version=1,
    )
    policy = CrawlScopePolicy(
        schema_version=1,
        allowed_origins=(origin,),
        user_agent="SignalBot/1.0 (+https://signal.example/bot)",
        request_timeout_seconds=2,
        total_timeout_seconds=5,
    )
    run = open_crawl_run(
        crawl_admission,
        crawl_input,
        first_run_id=started.first_run_id,
        policy=policy,
        limits=CrawlRunLimits(),
        seed_url=f"{origin}{path}",
    )
    observed_at = datetime.now(UTC)
    if robots_body is None:
        robots = _robots_result(origin, outcome="not_found", status=404)
        robots_key = None
        retain_until = None
    else:
        robots = _robots_result(origin, body=robots_body)
        robots_key = artifact_key()
        retain_until = observed_at + timedelta(days=30)
    snapshot = persist_robots_snapshot(
        crawl_ingest,
        store,
        run,
        policy,
        robots,
        snapshot_id=uuid4(),
        fetched_at=observed_at,
        network_profile_sha256=NETWORK_PROFILE,
        artifact_key=robots_key,
        retain_until=retain_until,
        recorded_at=observed_at + timedelta(milliseconds=20),
    )
    lease = claim_crawl_frontier(
        crawl_admission,
        run,
        worker_key=f"crawler.{key}",
        lease_id=uuid4(),
        lease_seconds=60,
    )
    assert lease is not None
    return run, policy, lease, snapshot


def _robots_result(origin, *, body=None, outcome="fetched", status=200):
    from signal_core.crawl_http import RobotsFetchResult

    material = body or b""
    fetched = outcome == "fetched"
    return RobotsFetchResult(
        schema_version=1,
        origin=origin,
        robots_url=f"{origin}/robots.txt",
        final_url=f"{origin}/robots.txt",
        outcome=outcome,
        http_status=status,
        media_type="text/plain" if fetched else None,
        response_headers=(("content-type", "text/plain"),) if fetched else (),
        redirect_chain=(),
        resolved_address="8.8.8.8",
        body=material,
        body_sha256=hashlib.sha256(material).hexdigest() if fetched else None,
        decoded_bytes=len(material) if fetched else 0,
        elapsed_ms=5,
    )


def fetch_result(lease, *, status=200, retry_after=None):
    body = b"<html><title>Qualified page</title></html>"
    headers = [("content-type", "text/html")]
    if retry_after is not None:
        headers.append(("retry-after", retry_after))
    return CrawlFetchResult(
        schema_version=1,
        original_url=lease.url.fetch_url,
        final_url=lease.url.fetch_url,
        normalized_key=lease.url.normalized_key,
        outcome="fetched",
        http_status=status,
        media_type="text/html",
        response_headers=tuple(sorted(headers)),
        redirect_chain=(),
        resolved_address="8.8.8.8",
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        decoded_bytes=len(body),
        elapsed_ms=15,
    )


class Fetcher:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0
        self._lock = Lock()

    def fetch(self, value, *, policy):
        with self._lock:
            self.calls += 1
        assert policy.admit(value).fetch_url == value
        if self.error is not None:
            raise self.error
        return self.result


def execute(crawl_admission, crawl_ingest, store, run, policy, lease, fetcher, permit_id):
    return execute_crawl_page_attempt(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        lease,
        policy,
        fetcher,
        permit_id=permit_id,
        admission_policy=OriginAdmissionPolicy(),
        network_profile_sha256=NETWORK_PROFILE,
        artifact_key=artifact_key(),
        retain_until=datetime.now(UTC) + timedelta(days=30),
    )


def test_page_attempt_persists_exact_evidence_releases_permit_and_retries_without_fetch(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    run, policy, lease, snapshot = page_authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "page-success",
        store,
    )
    fetcher = Fetcher(fetch_result(lease))
    permit_id = uuid4()

    first = execute(crawl_admission, crawl_ingest, store, run, policy, lease, fetcher, permit_id)
    duplicate = execute(
        crawl_admission, crawl_ingest, store, run, policy, lease, fetcher, permit_id
    )

    assert isinstance(first, CrawlPageAttemptReceipt)
    assert first.state == "observed"
    assert first.robots_snapshot_id == snapshot.snapshot_id
    assert first.completion_kind == "success"
    assert first.observation is not None
    assert store.read(first.observation.raw_artifact, key=artifact_key()) == fetcher.result.body
    assert duplicate.observation.observation_id == first.observation.observation_id
    assert duplicate.duplicate is True
    assert fetcher.calls == 1
    assert admin.execute(
        "SELECT attempt.state, attempt.origin_permit_id = attempt.id, permit.released_at IS NOT "
        "NULL, permit.completion_kind, bucket.in_flight_count FROM app.crawl_page_attempts attempt "
        "JOIN control.admission_leases permit ON permit.id = attempt.origin_permit_id "
        "JOIN control.origin_buckets bucket ON bucket.id = permit.bucket_id "
        "WHERE attempt.tenant_id = %s AND attempt.id = %s",
        (lease.tenant_id, permit_id),
    ).fetchone() == ("observed", True, True, "success", 0)


def test_robots_denial_does_not_acquire_or_fetch(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    run, policy, lease, snapshot = page_authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "page-denied",
        store,
        path="/private",
        robots_body=b"User-agent: *\nDisallow: /private\n",
    )
    fetcher = Fetcher(fetch_result(lease))

    result = execute(crawl_admission, crawl_ingest, store, run, policy, lease, fetcher, uuid4())

    assert isinstance(result, CrawlPageAttemptBlocked)
    assert result.robots_snapshot_id == snapshot.snapshot_id
    assert result.reason == "disallowed_by_rules"
    assert fetcher.calls == 0
    assert admin.execute(
        "SELECT count(*) FROM app.crawl_page_attempts WHERE tenant_id = %s",
        (lease.tenant_id,),
    ).fetchone() == (0,)


def test_global_origin_deferral_does_not_dispatch_http(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    run, policy, lease, _ = page_authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "page-deferred",
        store,
    )
    blocker = acquire_origin_permit(
        crawl_admission,
        lease,
        permit_id=uuid4(),
        permit_kind="robots",
        policy=OriginAdmissionPolicy(),
    )
    assert isinstance(blocker, OriginPermitGrant)
    fetcher = Fetcher(fetch_result(lease))

    result = execute(crawl_admission, crawl_ingest, store, run, policy, lease, fetcher, uuid4())

    assert isinstance(result, CrawlPageAttemptDeferred)
    assert result.reason == "in_flight"
    assert fetcher.calls == 0
    finish_origin_permit(crawl_admission, blocker, OriginPermitCompletion("cancelled", 0))


@pytest.mark.parametrize(
    ("error", "state", "reason", "completion"),
    [
        (CrawlFetchUnavailable(), "failed", "transport_error", "transport_error"),
        (CrawlFetchRejected(), "failed", "policy_rejected", "cancelled"),
    ],
)
def test_known_fetch_failure_is_terminal_and_releases_global_capacity(
    admin,
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    tmp_path,
    error,
    state,
    reason,
    completion,
):
    store = EncryptedLocalArtifactStore(tmp_path / reason)
    run, policy, lease, _ = page_authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        f"page-{reason.replace('_', '-')}",
        store,
    )
    result = execute(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        lease,
        Fetcher(error=error),
        uuid4(),
    )

    assert (result.state, result.terminal_reason, result.completion_kind) == (
        state,
        reason,
        completion,
    )
    assert admin.execute(
        "SELECT released_at IS NOT NULL FROM control.admission_leases WHERE id = %s",
        (result.origin_permit_id,),
    ).fetchone() == (True,)


def test_unknown_dispatch_is_never_blindly_repeated(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    run, policy, lease, _ = page_authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "page-unknown",
        store,
    )
    crashing = Fetcher(error=RuntimeError("synthetic crash after dispatch"))
    permit_id = uuid4()
    with pytest.raises(RuntimeError, match="synthetic crash"):
        execute(crawl_admission, crawl_ingest, store, run, policy, lease, crashing, permit_id)
    replacement = Fetcher(fetch_result(lease))

    result = execute(
        crawl_admission, crawl_ingest, store, run, policy, lease, replacement, permit_id
    )

    assert isinstance(result, CrawlPageAttemptPending)
    assert result.reason == "dispatch_outcome_unknown"
    assert crashing.calls == 1
    assert replacement.calls == 0


def test_persisted_observation_is_recovered_and_finalized_without_refetch(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    run, policy, lease, _ = page_authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "page-recover",
        store,
    )
    permit_id = uuid4()
    decision = authorize_from_current_snapshot(
        crawl_ingest,
        store,
        run,
        policy,
        lease.url.fetch_url,
        at_time=datetime.now(UTC),
        artifact_key=artifact_key(),
    )
    permit = acquire_origin_permit(
        crawl_admission,
        lease,
        permit_id=permit_id,
        permit_kind="html_navigation",
        policy=OriginAdmissionPolicy(),
    )
    assert isinstance(permit, OriginPermitGrant)
    begun = crawl_ingest.execute(
        "SELECT outcome FROM control.begin_crawl_page_attempt(" + ", ".join(["%s"] * 10) + ")",
        (
            lease.tenant_id,
            lease.site_id,
            lease.run_id,
            lease.frontier_id,
            lease.url_id,
            lease.lease_id,
            lease.lease_owner,
            permit_id,
            decision.snapshot_id,
            decision.reason,
        ),
    ).fetchone()
    assert begun == ("begun",)
    result = fetch_result(lease)
    started = datetime.now(UTC)
    persisted = persist_fetch_observation(
        crawl_ingest,
        store,
        lease,
        result,
        started_at=started,
        finished_at=started + timedelta(milliseconds=result.elapsed_ms),
        network_profile_sha256=NETWORK_PROFILE,
        artifact_key=artifact_key(),
        retain_until=started + timedelta(days=30),
        recorded_at=started + timedelta(milliseconds=result.elapsed_ms + 1),
    )
    assert load_fetch_observation(crawl_ingest, lease).observation_id == persisted.observation_id
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        crawl_ingest.execute(
            "SELECT * FROM control.finish_crawl_page_attempt(" + ", ".join(["%s"] * 14) + ")",
            (
                lease.tenant_id,
                lease.site_id,
                lease.run_id,
                lease.frontier_id,
                lease.url_id,
                lease.lease_id,
                lease.lease_owner,
                permit_id,
                persisted.observation_id,
                None,
                "service_unavailable",
                result.elapsed_ms,
                None,
                "local_persistence_failure",
            ),
        ).fetchone()
    failed_with_observation = crawl_ingest.execute(
        "SELECT outcome FROM control.finish_crawl_page_attempt(" + ", ".join(["%s"] * 14) + ")",
        (
            lease.tenant_id,
            lease.site_id,
            lease.run_id,
            lease.frontier_id,
            lease.url_id,
            lease.lease_id,
            lease.lease_owner,
            permit_id,
            None,
            "observation_persistence_failed",
            "service_unavailable",
            result.elapsed_ms,
            None,
            "local_persistence_failure",
        ),
    ).fetchone()
    assert failed_with_observation == ("observation_conflict",)
    admin.execute(
        "UPDATE app.tenants SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (lease.tenant_id,),
    )
    fetcher = Fetcher(result)

    receipt = execute(crawl_admission, crawl_ingest, store, run, policy, lease, fetcher, permit_id)

    assert receipt.state == "observed"
    assert receipt.observation.observation_id == persisted.observation_id
    assert fetcher.calls == 0


def test_artifact_persistence_failure_is_terminal_with_conservative_backoff(
    monkeypatch, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    run, policy, lease, _ = page_authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "page-persistence-failure",
        store,
    )

    def unavailable(*args, **kwargs):
        raise ArtifactUnavailable("synthetic store failure")

    monkeypatch.setattr(store, "stage_verified", unavailable)
    result = execute(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        lease,
        Fetcher(fetch_result(lease)),
        uuid4(),
    )

    assert result.state == "failed"
    assert result.terminal_reason == "observation_persistence_failed"
    assert result.completion_kind == "service_unavailable"
    assert result.retry_after_ms is None
    assert result.backoff_basis == "local_persistence_failure"


def test_database_persistence_uncertainty_remains_dispatched(
    monkeypatch, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    run, policy, lease, _ = page_authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "page-database-uncertain",
        store,
    )
    fetcher = Fetcher(fetch_result(lease))
    permit_id = uuid4()

    def uncertain(*args, **kwargs):
        raise psycopg.OperationalError("synthetic uncertain database outcome")

    with monkeypatch.context() as patch:
        patch.setattr(crawl_page, "persist_fetch_observation", uncertain)
        with pytest.raises(psycopg.OperationalError, match="uncertain database outcome"):
            execute(
                crawl_admission,
                crawl_ingest,
                store,
                run,
                policy,
                lease,
                fetcher,
                permit_id,
            )

    retry = execute(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        lease,
        fetcher,
        permit_id,
    )
    assert isinstance(retry, CrawlPageAttemptPending)
    assert retry.reason == "dispatch_outcome_unknown"
    assert fetcher.calls == 1


def test_preexisting_observation_prevents_late_dispatch(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    run, policy, lease, _ = page_authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "page-observation-before-dispatch",
        store,
    )
    permit_id = uuid4()
    permit = acquire_origin_permit(
        crawl_admission,
        lease,
        permit_id=permit_id,
        permit_kind="html_navigation",
        policy=OriginAdmissionPolicy(),
    )
    assert isinstance(permit, OriginPermitGrant)
    result = fetch_result(lease)
    started = datetime.now(UTC)
    persist_fetch_observation(
        crawl_ingest,
        store,
        lease,
        result,
        started_at=started,
        finished_at=started + timedelta(milliseconds=result.elapsed_ms),
        network_profile_sha256=NETWORK_PROFILE,
        artifact_key=artifact_key(),
        retain_until=started + timedelta(days=30),
        recorded_at=started + timedelta(milliseconds=result.elapsed_ms + 1),
    )
    fetcher = Fetcher(result)

    with pytest.raises(CrawlPageAttemptConflict):
        execute(
            crawl_admission,
            crawl_ingest,
            store,
            run,
            policy,
            lease,
            fetcher,
            permit_id,
        )
    assert fetcher.calls == 0


def test_failed_attempt_rejects_late_observation(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    run, policy, lease, _ = page_authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "page-observation-after-failure",
        store,
    )
    receipt = execute(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        lease,
        Fetcher(error=CrawlFetchUnavailable("synthetic unavailable")),
        uuid4(),
    )
    assert receipt.state == "failed"
    result = fetch_result(lease)
    started = max(datetime.now(UTC), receipt.dispatched_at)

    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        persist_fetch_observation(
            crawl_ingest,
            store,
            lease,
            result,
            started_at=started,
            finished_at=started + timedelta(milliseconds=result.elapsed_ms),
            network_profile_sha256=NETWORK_PROFILE,
            artifact_key=artifact_key(),
            retain_until=started + timedelta(days=30),
            recorded_at=started + timedelta(milliseconds=result.elapsed_ms + 1),
        )
    assert load_fetch_observation(crawl_ingest, lease) is None


def test_rate_limit_preserves_provider_backoff_basis(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    run, policy, lease, _ = page_authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "page-rate-limit",
        store,
    )
    result = execute(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        lease,
        Fetcher(fetch_result(lease, status=429, retry_after="17")),
        uuid4(),
    )

    assert result.completion_kind == "rate_limited"
    assert result.retry_after_ms == 17_000
    assert result.backoff_basis == "provider_seconds"


def test_concurrent_exact_callers_dispatch_only_once(api, scheduler, workflow, scopes, tmp_path):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    with (
        psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"], autocommit=True
        ) as admission,
        psycopg.connect(os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"], autocommit=True) as ingest,
    ):
        run, policy, lease, _ = page_authority(
            api,
            scheduler,
            workflow,
            admission,
            ingest,
            scopes[0],
            "page-concurrent",
            store,
        )
    fetcher = Fetcher(fetch_result(lease))
    permit_id = uuid4()
    barrier = Barrier(2, timeout=10)

    def call_once(_):
        with (
            psycopg.connect(
                os.environ["SIGNAL_TEST_CRAWL_ADMISSION_DSN"], autocommit=True
            ) as admission,
            psycopg.connect(os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"], autocommit=True) as ingest,
        ):
            barrier.wait()
            return execute(admission, ingest, store, run, policy, lease, fetcher, permit_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(call_once, range(2)))

    assert fetcher.calls == 1
    assert any(isinstance(outcome, CrawlPageAttemptReceipt) for outcome in outcomes)
    assert all(
        isinstance(outcome, (CrawlPageAttemptReceipt, CrawlPageAttemptPending))
        for outcome in outcomes
    )


def test_page_attempt_table_is_function_only_and_terminal_identity_is_immutable(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    run, policy, lease, _ = page_authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        "page-privileges",
        store,
    )
    receipt = execute(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        policy,
        lease,
        Fetcher(fetch_result(lease)),
        uuid4(),
    )
    for privilege in ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]:
        assert not admin.execute(
            "SELECT has_table_privilege('signal_crawl_ingest', 'app.crawl_page_attempts', %s)",
            (privilege,),
        ).fetchone()[0]
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.crawl_page_attempts SET completion_kind = 'cancelled' "
            "WHERE tenant_id = %s AND id = %s",
            (lease.tenant_id, receipt.attempt_id),
        )
    assert admin.execute(
        "SELECT has_function_privilege('signal_crawl_ingest', "
        "'control.finish_crawl_page_attempt(uuid,uuid,uuid,uuid,uuid,uuid,text,uuid,uuid,"
        "text,text,integer,integer,text)', 'EXECUTE')"
    ).fetchone() == (True,)
