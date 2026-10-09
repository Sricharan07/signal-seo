import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from signal_core.commands import accept_snapshot
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_frontier import CrawlRunLimits, open_crawl_run
from signal_core.crawl_http import RobotsFetchResult
from signal_core.crawl_robots import (
    RobotsSnapshotConflict,
    RobotsSnapshotUnavailable,
    authorize_from_current_snapshot,
    persist_robots_snapshot,
)
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.outbox_dispatch import claim_outbox_batch
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_contracts import CrawlSiteWorkflowInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started

NETWORK_PROFILE = "cd" * 32


def artifact_key():
    return ArtifactEncryptionKey("artifact-key:v1:robots-test", bytes(reversed(range(32))))


def running_run(api, scheduler, workflow, crawl_admission, scope, key):
    command = accept_snapshot(api, scope, actor_service="crawl-robots-test", idempotency_key=key)
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scope.tenant_id,
        worker_key="worker.crawl-robots",
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
    run = open_crawl_run(
        crawl_admission,
        crawl_input,
        first_run_id=started.first_run_id,
        policy=policy,
        limits=CrawlRunLimits(),
        seed_url="https://crawl.example/",
    )
    return run, policy


def robots_result(body=b"User-agent: SignalBot\nDisallow: /private\n", **overrides):
    values = {
        "schema_version": 1,
        "origin": "https://crawl.example",
        "robots_url": "https://crawl.example/robots.txt",
        "final_url": "https://crawl.example/robots.txt",
        "outcome": "fetched",
        "http_status": 200,
        "media_type": "text/plain",
        "response_headers": (("content-type", "text/plain; charset=utf-8"),),
        "redirect_chain": (),
        "resolved_address": "8.8.8.8",
        "body": body,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "decoded_bytes": len(body),
        "elapsed_ms": 12,
        **overrides,
    }
    return RobotsFetchResult(**values)


def bodyless_result(outcome, status, **overrides):
    return robots_result(
        body=b"",
        outcome=outcome,
        http_status=status,
        media_type=None,
        response_headers=(("retry-after", "30"),) if outcome == "backoff" else (),
        body_sha256=None,
        decoded_bytes=0,
        **overrides,
    )


def persist(crawl_ingest, store, run, policy, result, snapshot_id, fetched):
    return persist_robots_snapshot(
        crawl_ingest,
        store,
        run,
        policy,
        result,
        snapshot_id=snapshot_id,
        fetched_at=fetched,
        network_profile_sha256=NETWORK_PROFILE,
        artifact_key=artifact_key() if result.outcome == "fetched" else None,
        retain_until=fetched + timedelta(days=30) if result.outcome == "fetched" else None,
        recorded_at=fetched + timedelta(milliseconds=20),
    )


def test_fetched_snapshot_is_encrypted_append_only_and_drives_exact_rules(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    run, policy = running_run(
        api, scheduler, workflow, crawl_admission, scopes[0], "robots-fetched"
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    fetched = datetime.now(UTC)
    result = robots_result()
    recorded = persist(crawl_ingest, store, run, policy, result, uuid4(), fetched)

    assert recorded.decision_status == "rules"
    assert recorded.raw_artifact is not None
    assert recorded.raw_artifact.durability_state == "verified"
    assert store.read(recorded.raw_artifact, key=artifact_key()) == result.body
    object_path = store.root.joinpath(*recorded.raw_artifact.object_key.split("/"))
    assert result.body not in object_path.read_bytes()
    assert (
        authorize_from_current_snapshot(
            crawl_ingest,
            store,
            run,
            policy,
            "https://crawl.example/public",
            at_time=fetched + timedelta(minutes=1),
            artifact_key=artifact_key(),
        ).allowed
        is True
    )
    denied = authorize_from_current_snapshot(
        crawl_ingest,
        store,
        run,
        policy,
        "https://crawl.example/private/page",
        at_time=fetched + timedelta(minutes=1),
        artifact_key=artifact_key(),
    )
    assert denied.allowed is False
    assert denied.reason == "disallowed_by_rules"
    assert "private" not in repr(recorded)
    assert admin.execute(
        "SELECT snapshot.retrieval_outcome, snapshot.decision_status, artifact.media_type, "
        "attestation.check_type, attestation.result FROM app.robots_snapshots snapshot "
        "JOIN app.artifacts artifact ON artifact.tenant_id = snapshot.tenant_id "
        "AND artifact.site_id = snapshot.site_id AND artifact.id = snapshot.raw_artifact_id "
        "JOIN app.artifact_attestations attestation "
        "ON attestation.tenant_id = artifact.tenant_id "
        "AND attestation.site_id = artifact.site_id "
        "AND attestation.artifact_id = artifact.id "
        "WHERE snapshot.tenant_id = %s AND snapshot.id = %s",
        (scopes[0].tenant_id, recorded.snapshot_id),
    ).fetchone() == ("fetched", "rules", "text/plain", "upload_readback", "verified")


@pytest.mark.parametrize(
    ("result", "allowed", "reason"),
    [
        (bodyless_result("not_found", 404), True, "robots_not_found"),
        (bodyless_result("forbidden", 403), False, "deny"),
        (bodyless_result("backoff", 429), False, "backoff"),
        (bodyless_result("server_error", 503), False, "suspended"),
        (bodyless_result("client_error", 410), False, "suspended"),
        (bodyless_result("transport_error", None, resolved_address=None), False, "suspended"),
    ],
)
def test_status_policy_is_durable_bodyless_and_only_404_allows(
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scopes,
    tmp_path,
    result,
    allowed,
    reason,
):
    key = f"robots-status-{result.outcome}"
    run, policy = running_run(api, scheduler, workflow, crawl_admission, scopes[0], key)
    store = EncryptedLocalArtifactStore(tmp_path / key)
    fetched = datetime.now(UTC)
    recorded = persist(crawl_ingest, store, run, policy, result, uuid4(), fetched)
    decision = authorize_from_current_snapshot(
        crawl_ingest,
        store,
        run,
        policy,
        "https://crawl.example/page",
        at_time=fetched + timedelta(seconds=1),
        artifact_key=None,
    )
    assert decision.allowed is allowed
    assert decision.reason == reason
    assert recorded.raw_artifact is None
    assert list(store.root.rglob("*.sig")) == []


def test_newer_restrictions_replace_unsent_cached_decisions_and_expiry_closes(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    run, policy = running_run(api, scheduler, workflow, crawl_admission, scopes[0], "robots-newer")
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    first_at = datetime.now(UTC)
    persist(
        crawl_ingest,
        store,
        run,
        policy,
        robots_result(body=b"User-agent: *\nAllow: /\n"),
        uuid4(),
        first_at,
    )
    second = persist(
        crawl_ingest,
        store,
        run,
        policy,
        robots_result(body=b"User-agent: *\nDisallow: /future\n"),
        uuid4(),
        first_at + timedelta(seconds=1),
    )
    current = authorize_from_current_snapshot(
        crawl_ingest,
        store,
        run,
        policy,
        "https://crawl.example/future",
        at_time=first_at + timedelta(minutes=1),
        artifact_key=artifact_key(),
    )
    assert current.snapshot_id == second.snapshot_id
    assert current.allowed is False
    with pytest.raises(RobotsSnapshotUnavailable):
        authorize_from_current_snapshot(
            crawl_ingest,
            store,
            run,
            policy,
            "https://crawl.example/future",
            at_time=first_at + timedelta(hours=25),
            artifact_key=artifact_key(),
        )


def test_exact_and_concurrent_retry_create_one_snapshot_and_one_artifact(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    run, policy = running_run(api, scheduler, workflow, crawl_admission, scopes[0], "robots-retry")
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    fetched = datetime.now(UTC)
    snapshot_id = uuid4()
    result = robots_result()
    first = persist(crawl_ingest, store, run, policy, result, snapshot_id, fetched)
    assert persist(crawl_ingest, store, run, policy, result, snapshot_id, fetched) == replace(
        first, duplicate=True
    )

    second_id = uuid4()
    barrier = Barrier(2, timeout=10)

    def record_once(_):
        with psycopg.connect(
            os.environ["SIGNAL_TEST_CRAWL_INGEST_DSN"], autocommit=True
        ) as connection:
            barrier.wait()
            return persist(connection, store, run, policy, result, second_id, fetched)

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(record_once, range(2)))
    assert sorted(receipt.duplicate for receipt in receipts) == [False, True]
    assert len(list(store.root.rglob("*.sig"))) == 2


def test_conflicting_identity_is_rejected_and_wrong_scope_cannot_register(
    api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    run, policy = running_run(
        api, scheduler, workflow, crawl_admission, scopes[0], "robots-conflict"
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    fetched = datetime.now(UTC)
    snapshot_id = uuid4()
    persist(crawl_ingest, store, run, policy, robots_result(), snapshot_id, fetched)
    with pytest.raises(RobotsSnapshotConflict):
        persist(
            crawl_ingest,
            store,
            run,
            policy,
            robots_result(body=b"User-agent: *\nDisallow: /different\n"),
            snapshot_id,
            fetched,
        )
    wrong = replace(run, site_id=scopes[1].site_id)
    with pytest.raises(RobotsSnapshotUnavailable):
        persist(
            crawl_ingest,
            store,
            wrong,
            policy,
            bodyless_result("not_found", 404),
            uuid4(),
            fetched,
        )


def test_invalid_redirect_evidence_fails_before_artifact_or_database_write(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    run, policy = running_run(
        api, scheduler, workflow, crawl_admission, scopes[0], "robots-invalid-local"
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    result = robots_result(
        redirect_chain=("https://static.crawl.example/robots.txt",),
    )
    with pytest.raises(ValueError, match="final URL"):
        persist(
            crawl_ingest,
            store,
            run,
            policy,
            result,
            uuid4(),
            datetime.now(UTC),
        )
    assert list(store.root.rglob("*.sig")) == []
    assert (
        admin.execute(
            "SELECT count(*) FROM app.robots_snapshots WHERE tenant_id = %s",
            (scopes[0].tenant_id,),
        ).fetchone()[0]
        == 0
    )


def test_robots_table_is_function_only_for_ingest_and_rows_are_immutable(
    admin, api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path
):
    run, policy = running_run(
        api, scheduler, workflow, crawl_admission, scopes[0], "robots-privileges"
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    recorded = persist(
        crawl_ingest,
        store,
        run,
        policy,
        bodyless_result("not_found", 404),
        uuid4(),
        datetime.now(UTC),
    )
    for privilege in ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]:
        assert not admin.execute(
            "SELECT has_table_privilege('signal_crawl_ingest', 'app.robots_snapshots', %s)",
            (privilege,),
        ).fetchone()[0]
    assert admin.execute(
        "SELECT has_function_privilege('signal_crawl_ingest', "
        "'control.get_current_robots_snapshot(uuid,uuid,uuid,text,bytea,timestamptz)', "
        "'EXECUTE')"
    ).fetchone()[0]
    for statement in [
        "UPDATE app.robots_snapshots SET expires_at = expires_at + interval '1 second' "
        "WHERE tenant_id = %s AND id = %s",
        "DELETE FROM app.robots_snapshots WHERE tenant_id = %s AND id = %s",
    ]:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(statement, (scopes[0].tenant_id, recorded.snapshot_id))
