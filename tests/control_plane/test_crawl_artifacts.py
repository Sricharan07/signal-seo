import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.commands import accept_snapshot
from signal_core.crawl_artifacts import (
    ArtifactEncryptionKey,
    ArtifactUnavailable,
    EncryptedLocalArtifactStore,
    FetchObservationConflict,
    FetchObservationUnavailable,
    attest_artifact,
    cleanup_orphan_artifacts,
    load_artifact,
    persist_fetch_observation,
)
from signal_core.crawl_frontier import CrawlRunLimits, claim_crawl_frontier, open_crawl_run
from signal_core.crawl_http import CrawlFetchResult
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.outbox_dispatch import claim_outbox_batch
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_contracts import CrawlSiteWorkflowInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started

NETWORK_PROFILE = "ab" * 32


def artifact_key():
    return ArtifactEncryptionKey("artifact-key:v1:crawl-test", bytes(range(32)))


def running_lease(api, scheduler, workflow, crawl_admission, scope, key, lease_seconds=30):
    command = accept_snapshot(
        api,
        scope,
        actor_service="crawl-artifact-test",
        idempotency_key=key,
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scope.tenant_id,
        worker_key="worker.crawl-artifact",
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
        allowed_origins=("https://crawl.example", "https://static.crawl.example"),
        user_agent="SignalBot/1.0 (+https://signal.example/bot)",
        request_timeout_seconds=2,
        total_timeout_seconds=5,
    )
    opened = open_crawl_run(
        crawl_admission,
        crawl_input,
        first_run_id=started.first_run_id,
        policy=policy,
        limits=CrawlRunLimits(),
        seed_url="https://crawl.example/",
    )
    lease = claim_crawl_frontier(
        crawl_admission,
        opened,
        worker_key="crawler.artifact-test",
        lease_id=uuid4(),
        lease_seconds=lease_seconds,
    )
    assert lease is not None
    return lease


def fetch_result(lease, **overrides):
    body = overrides.pop("body", b"<html><title>Evidence</title></html>")
    values = {
        "schema_version": 1,
        "original_url": lease.url.fetch_url,
        "final_url": lease.url.fetch_url,
        "normalized_key": lease.url.normalized_key,
        "outcome": "fetched",
        "http_status": 200,
        "media_type": "text/html",
        "response_headers": (("content-type", "text/html; charset=utf-8"), ("etag", '"v1"')),
        "redirect_chain": (),
        "resolved_address": "8.8.8.8",
        "body": body,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "decoded_bytes": len(body),
        "elapsed_ms": 12,
        **overrides,
    }
    return CrawlFetchResult(**values)


def observation_times():
    started = datetime.now(UTC)
    finished = started + timedelta(milliseconds=20)
    recorded = finished + timedelta(milliseconds=20)
    return started, finished, recorded


def persist(crawl_ingest, store, lease, result, times):
    started, finished, recorded = times
    return persist_fetch_observation(
        crawl_ingest,
        store,
        lease,
        result,
        started_at=started,
        finished_at=finished,
        network_profile_sha256=NETWORK_PROFILE,
        artifact_key=artifact_key() if result.outcome == "fetched" else None,
        retain_until=recorded + timedelta(days=30) if result.outcome == "fetched" else None,
        recorded_at=recorded,
    )


def bodyless_result(lease, **overrides):
    return fetch_result(
        lease,
        body=b"",
        outcome="unsupported_media_type",
        media_type="application/pdf",
        body_sha256=None,
        decoded_bytes=0,
        **overrides,
    )


def test_fetched_body_commits_encrypted_artifact_attestation_and_observation(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    lease = running_lease(api, scheduler, workflow, crawl_admission, scopes[0], "artifact-fetched")
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    result = fetch_result(lease)
    recorded = persist(crawl_ingest, store, lease, result, observation_times())

    assert recorded.tenant_id == scopes[0].tenant_id
    assert recorded.site_id == scopes[0].site_id
    assert recorded.fetch_attempt_id == lease.lease_id
    assert recorded.raw_artifact is not None
    assert recorded.raw_artifact.durability_state == "verified"
    assert recorded.raw_artifact.sha256 == result.body_sha256
    assert store.read(recorded.raw_artifact, key=artifact_key()) == result.body
    path = store.root.joinpath(*recorded.raw_artifact.object_key.split("/"))
    assert result.body not in path.read_bytes()
    assert "Evidence" not in repr(recorded)
    assert admin.execute(
        "SELECT observation.outcome, observation.http_status, observation.final_url, "
        "observation.response_headers, host(observation.resolved_address), "
        "artifact.durability_state, artifact.byte_length, "
        "attestation.check_type, attestation.result "
        "FROM app.fetch_observations AS observation "
        "JOIN app.artifacts AS artifact ON artifact.tenant_id = observation.tenant_id "
        "AND artifact.site_id = observation.site_id "
        "AND artifact.id = observation.raw_artifact_id "
        "JOIN app.artifact_attestations AS attestation "
        "ON attestation.tenant_id = artifact.tenant_id "
        "AND attestation.site_id = artifact.site_id "
        "AND attestation.artifact_id = artifact.id "
        "WHERE observation.tenant_id = %s AND observation.id = %s",
        (scopes[0].tenant_id, recorded.observation_id),
    ).fetchone() == (
        "fetched",
        200,
        "https://crawl.example/",
        {"content-type": "text/html; charset=utf-8", "etag": '"v1"'},
        "8.8.8.8",
        "verified",
        len(result.body),
        "upload_readback",
        "verified",
    )


def test_exact_observation_retry_returns_original_without_duplicate_rows(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    lease = running_lease(
        api, scheduler, workflow, crawl_admission, scopes[0], "artifact-idempotency"
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    result = fetch_result(lease)
    times = observation_times()
    first = persist(crawl_ingest, store, lease, result, times)
    duplicate = persist(crawl_ingest, store, lease, result, times)

    assert duplicate == replace(first, duplicate=True)
    assert admin.execute(
        "SELECT (SELECT count(*) FROM app.fetch_observations WHERE tenant_id = %s "
        "AND fetch_attempt_id = %s), "
        "(SELECT count(*) FROM app.artifact_attestations WHERE tenant_id = %s "
        "AND artifact_id = %s)",
        (
            scopes[0].tenant_id,
            lease.lease_id,
            scopes[0].tenant_id,
            first.raw_artifact.artifact_id,
        ),
    ).fetchone() == (1, 1)


def test_concurrent_exact_persistence_creates_one_object_and_observation(
    api, scheduler, workflow, crawl_admission, scopes, tmp_path
):
    lease = running_lease(
        api, scheduler, workflow, crawl_admission, scopes[0], "artifact-concurrency"
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    result = fetch_result(lease)
    times = observation_times()
    barrier = Barrier(2, timeout=10)

    def record_once(_):
        with psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"], autocommit=True
        ) as connection:
            barrier.wait()
            return persist(connection, store, lease, result, times)

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(record_once, range(2)))

    assert sorted(receipt.duplicate for receipt in receipts) == [False, True]
    assert len(list(store.root.rglob("*.sig"))) == 1


def test_conflicting_retry_leaves_reconcilable_orphan_then_cleanup_preserves_registered(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    lease = running_lease(api, scheduler, workflow, crawl_admission, scopes[0], "artifact-orphan")
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    times = observation_times()
    first = persist(crawl_ingest, store, lease, fetch_result(lease), times)
    with pytest.raises(FetchObservationConflict):
        persist(
            crawl_ingest,
            store,
            lease,
            fetch_result(lease, body=b"<html>different</html>"),
            times,
        )
    assert len(list(store.root.rglob("*.sig"))) == 2

    recent = cleanup_orphan_artifacts(
        crawl_ingest,
        store,
        grace_seconds=300,
        batch_size=10,
        now=times[2] + timedelta(seconds=100),
    )
    assert recent.scanned == 2
    assert recent.deleted == 0
    assert recent.registered == 0
    assert recent.recent == 2
    assert recent.unreadable == 0

    cleanup = cleanup_orphan_artifacts(
        crawl_ingest,
        store,
        grace_seconds=300,
        batch_size=10,
        now=times[2] + timedelta(hours=2),
    )
    assert cleanup.scanned == 2
    assert cleanup.deleted == 1
    assert cleanup.registered == 1
    assert cleanup.recent == 0
    assert cleanup.unreadable == 0
    assert len(list(store.root.rglob("*.sig"))) == 1
    assert store.read(first.raw_artifact, key=artifact_key()) == fetch_result(lease).body


def test_bodyless_outcome_is_append_only_without_inventing_an_artifact(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    lease = running_lease(api, scheduler, workflow, crawl_admission, scopes[0], "artifact-bodyless")
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    recorded = persist(crawl_ingest, store, lease, bodyless_result(lease), observation_times())

    assert recorded.outcome == "unsupported_media_type"
    assert recorded.media_type == "application/pdf"
    assert recorded.raw_artifact is None
    assert list(store.root.rglob("*.sig")) == []
    assert admin.execute(
        "SELECT raw_artifact_id, decoded_bytes FROM app.fetch_observations "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, recorded.observation_id),
    ).fetchone() == (None, 0)


def test_completed_attempt_can_be_recorded_after_lease_expiry_and_authority_reduction(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    lease = running_lease(
        api,
        scheduler,
        workflow,
        crawl_admission,
        scopes[0],
        "artifact-late-result",
        lease_seconds=1,
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    result = bodyless_result(lease)
    times = observation_times()
    admin.execute(
        "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    time.sleep(1.05)

    recorded = persist(crawl_ingest, store, lease, result, times)
    assert recorded.fetch_attempt_id == lease.lease_id
    assert recorded.duplicate is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"response_headers": (("set-cookie", "secret=value"),)},
        {"resolved_address": "127.0.0.1"},
        {"resolved_address": "::ffff:8.8.8.8"},
        {"final_url": "https://static.crawl.example/other"},
        {"redirect_chain": ("https://crawl.example/",)},
        {"body_sha256": "00" * 32},
        {"elapsed_ms": 5001},
    ],
)
def test_invalid_fetch_evidence_fails_before_object_or_database_write(
    admin,
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    tmp_path,
    overrides,
):
    lease = running_lease(
        api, scheduler, workflow, crawl_admission, scopes[0], f"artifact-invalid-{uuid4()}"
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError):
        persist(crawl_ingest, store, lease, fetch_result(lease, **overrides), observation_times())
    assert list(store.root.rglob("*.sig")) == []
    assert admin.execute(
        "SELECT count(*) FROM app.fetch_observations WHERE tenant_id = %s "
        "AND fetch_attempt_id = %s",
        (scopes[0].tenant_id, lease.lease_id),
    ).fetchone() == (0,)


def test_wrong_site_or_worker_cannot_record_another_lease(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    lease = running_lease(
        api, scheduler, workflow, crawl_admission, scopes[0], "artifact-wrong-scope"
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(FetchObservationUnavailable):
        persist(
            crawl_ingest,
            store,
            replace(lease, site_id=scopes[1].site_id),
            bodyless_result(lease),
            observation_times(),
        )
    with pytest.raises(FetchObservationUnavailable):
        persist(
            crawl_ingest,
            store,
            replace(lease, lease_owner="crawler.other"),
            bodyless_result(lease),
            observation_times(),
        )


def test_database_rejects_untrusted_network_and_time_evidence_for_runtime_role(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes
):
    lease = running_lease(
        api, scheduler, workflow, crawl_admission, scopes[0], "artifact-db-validation"
    )
    started, finished, _ = observation_times()
    base = [
        lease.tenant_id,
        lease.site_id,
        lease.run_id,
        lease.frontier_id,
        lease.url_id,
        lease.lease_id,
        lease.lease_owner,
        uuid4(),
        started,
        finished,
        "unsupported_media_type",
        200,
        lease.url.fetch_url,
        Jsonb({"content-type": "application/pdf"}),
        Jsonb([]),
        "8.8.8.8",
        "application/pdf",
        0,
        1,
        bytes.fromhex(NETWORK_PROFILE),
        *(None,) * 10,
    ]
    query = "SELECT * FROM control.commit_fetch_observation(" + ", ".join(["%s"] * 30) + ")"
    for replacements in [
        {13: Jsonb({"set-cookie": "secret=value"})},
        {13: Jsonb({"content-type": 123})},
        {15: "127.0.0.1"},
        {15: "::ffff:8.8.8.8"},
        {9: datetime.now(UTC) + timedelta(minutes=10)},
        {18: 4000},
    ]:
        arguments = list(base)
        for index, value in replacements.items():
            arguments[index] = value
        arguments[7] = uuid4()
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            crawl_ingest.execute(query, arguments)


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_integrity_attestation_records_failure_and_updates_projection(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path, failure
):
    lease = running_lease(
        api, scheduler, workflow, crawl_admission, scopes[0], f"artifact-attestation-{failure}"
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    observation = persist(crawl_ingest, store, lease, fetch_result(lease), observation_times())
    artifact = observation.raw_artifact
    with pytest.raises(ValueError, match="verification time"):
        attest_artifact(
            crawl_ingest,
            store,
            artifact,
            key=artifact_key(),
            attestation_id=uuid4(),
            check_type="scheduled_integrity",
            verified_at=artifact.created_at - timedelta(seconds=1),
        )
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        crawl_ingest.execute(
            "SELECT * FROM control.record_artifact_attestation(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                artifact.tenant_id,
                artifact.site_id,
                artifact.artifact_id,
                uuid4(),
                "scheduled_integrity",
                "missing",
                None,
                artifact.created_at - timedelta(seconds=1),
            ),
        )
    path = store.root.joinpath(*artifact.object_key.split("/"))
    original_envelope = path.read_bytes()
    if failure == "missing":
        path.unlink()
    else:
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 1
        path.write_bytes(raw)
    attestation_id = uuid4()
    checked_at = max(datetime.now(UTC), artifact.created_at)

    first = attest_artifact(
        crawl_ingest,
        store,
        artifact,
        key=artifact_key(),
        attestation_id=attestation_id,
        check_type="scheduled_integrity",
        verified_at=checked_at,
    )
    duplicate = attest_artifact(
        crawl_ingest,
        store,
        artifact,
        key=artifact_key(),
        attestation_id=attestation_id,
        check_type="scheduled_integrity",
        verified_at=checked_at,
    )
    assert first.result == failure
    assert duplicate == replace(first, duplicate=True)
    failed_artifact = load_artifact(
        crawl_ingest,
        tenant_id=artifact.tenant_id,
        site_id=artifact.site_id,
        artifact_id=artifact.artifact_id,
    )
    assert failed_artifact.durability_state == failure
    with pytest.raises(ArtifactUnavailable, match="verified durability"):
        store.read(failed_artifact, key=artifact_key())

    path.write_bytes(original_envelope)
    path.chmod(0o600)
    restored = attest_artifact(
        crawl_ingest,
        store,
        failed_artifact,
        key=artifact_key(),
        attestation_id=uuid4(),
        check_type="restore_verification",
        verified_at=checked_at + timedelta(milliseconds=1),
    )
    assert restored.result == "verified"
    restored_artifact = load_artifact(
        crawl_ingest,
        tenant_id=artifact.tenant_id,
        site_id=artifact.site_id,
        artifact_id=artifact.artifact_id,
    )
    assert restored_artifact.durability_state == "verified"
    assert store.read(restored_artifact, key=artifact_key()) == fetch_result(lease).body
    assert admin.execute(
        "SELECT count(*) FROM app.artifact_attestations WHERE tenant_id = %s AND artifact_id = %s",
        (artifact.tenant_id, artifact.artifact_id),
    ).fetchone() == (3,)


def test_artifact_lookup_is_exact_and_ingest_role_is_function_only(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    lease = running_lease(
        api, scheduler, workflow, crawl_admission, scopes[0], "artifact-lookup-role"
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    observation = persist(crawl_ingest, store, lease, fetch_result(lease), observation_times())
    artifact = observation.raw_artifact
    assert (
        load_artifact(
            crawl_ingest,
            tenant_id=artifact.tenant_id,
            site_id=artifact.site_id,
            artifact_id=artifact.artifact_id,
        )
        == artifact
    )
    assert (
        load_artifact(
            crawl_ingest,
            tenant_id=artifact.tenant_id,
            site_id=scopes[1].site_id,
            artifact_id=artifact.artifact_id,
        )
        is None
    )

    signatures = [
        "control.get_artifact(uuid,uuid,uuid)",
        (
            "control.commit_fetch_observation(uuid,uuid,uuid,uuid,uuid,uuid,text,uuid,"
            "timestamp with time zone,timestamp with time zone,text,integer,text,jsonb,jsonb,"
            "inet,text,bigint,integer,bytea,uuid,text,text,bytea,bigint,text,text,"
            "timestamp with time zone,timestamp with time zone,uuid)"
        ),
        (
            "control.record_artifact_attestation(uuid,uuid,uuid,uuid,text,text,bytea,"
            "timestamp with time zone)"
        ),
    ]
    for signature in signatures:
        assert admin.execute(
            "SELECT has_function_privilege('signal_crawl_ingest', %s, 'EXECUTE')",
            (signature,),
        ).fetchone() == (True,)
        for role in ("public", "signal_api", "signal_workflow", "signal_crawl_admission"):
            assert admin.execute(
                "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, signature)
            ).fetchone() == (False,)
    for table in ("artifacts", "artifact_attestations", "fetch_observations"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            crawl_ingest.execute(f"SELECT * FROM app.{table}")


def test_artifact_observation_and_attestation_rows_reject_direct_mutation(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    lease = running_lease(
        api, scheduler, workflow, crawl_admission, scopes[0], "artifact-immutability"
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    observation = persist(crawl_ingest, store, lease, fetch_result(lease), observation_times())
    artifact = observation.raw_artifact
    rows = admin.execute(
        "SELECT id FROM app.artifact_attestations WHERE tenant_id = %s "
        "AND artifact_id = %s ORDER BY verified_at LIMIT 1",
        (artifact.tenant_id, artifact.artifact_id),
    ).fetchone()
    statements = [
        (
            "UPDATE app.artifacts SET durability_state = 'missing' "
            "WHERE tenant_id = %s AND id = %s",
            (artifact.tenant_id, artifact.artifact_id),
        ),
        (
            "DELETE FROM app.artifact_attestations WHERE tenant_id = %s AND id = %s",
            (artifact.tenant_id, rows[0]),
        ),
        (
            "UPDATE app.fetch_observations SET outcome = outcome WHERE tenant_id = %s AND id = %s",
            (artifact.tenant_id, observation.observation_id),
        ),
    ]
    for statement, arguments in statements:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(statement, arguments)


def test_non_autocommit_and_bad_cleanup_inputs_fail_before_file_mutation(
    api, scheduler, workflow, crawl_admission, scopes, tmp_path
):
    lease = running_lease(
        api, scheduler, workflow, crawl_admission, scopes[0], "artifact-connection"
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    with psycopg.connect(os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"]) as connection:
        with pytest.raises(ValueError, match="autocommit"):
            persist(connection, store, lease, fetch_result(lease), observation_times())
    assert list(store.root.rglob("*.sig")) == []

    with psycopg.connect(os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"], autocommit=True) as connection:
        with pytest.raises(ValueError, match="grace"):
            cleanup_orphan_artifacts(connection, store, grace_seconds=0)
        with pytest.raises(ValueError, match="batch"):
            cleanup_orphan_artifacts(connection, store, batch_size=0)
