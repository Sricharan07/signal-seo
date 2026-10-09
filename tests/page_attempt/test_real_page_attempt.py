import base64
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import uuid4

from signal_core.commands import accept_snapshot
from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_frontier import CrawlRunLimits, claim_crawl_frontier, open_crawl_run
from signal_core.crawl_http import CrawlFetchResult
from signal_core.crawl_page import CrawlPageAttemptReceipt, execute_crawl_page_attempt
from signal_core.crawl_robots import persist_robots_snapshot
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.outbox_dispatch import claim_outbox_batch
from signal_core.workflow_admission import admit_command_event
from signal_core.workflow_contracts import CrawlSiteWorkflowInput
from signal_core.workflow_start import WorkflowStartReceipt, record_workflow_started

IMAGE = os.environ["SIGNAL_CRAWLER_NETWORK_IMAGE"]
NETWORK = os.environ["SIGNAL_CRAWLER_NETWORK_NAME"]
RUN_ID = os.environ["SIGNAL_CRAWLER_NETWORK_RUN_ID"]
SERVER_ADDRESS = os.environ["SIGNAL_CRAWLER_NETWORK_ADDRESS"]
LABEL = "dev.signal.crawler-network-lab"
NETWORK_PROFILE = "91" * 32


class DockerPinnedFetcher:
    """Run the production pinned HTTP boundary inside the isolated client image."""

    def __init__(self):
        self.calls = 0
        self._lock = Lock()

    def fetch(self, value, *, policy):
        payload = json.dumps(
            {
                "url": value,
                "allowed_origins": policy.allowed_origins,
                "user_agent": policy.user_agent,
                "max_redirects": policy.max_redirects,
                "max_body_bytes": policy.max_body_bytes,
                "request_timeout_seconds": policy.request_timeout_seconds,
                "total_timeout_seconds": policy.total_timeout_seconds,
            }
        )
        program = f"""
import base64, json
from signal_core.crawl_http import PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy
value = json.loads({payload!r})
policy = CrawlScopePolicy(schema_version=1,
    allowed_origins=tuple(value['allowed_origins']), user_agent=value['user_agent'],
    max_redirects=value['max_redirects'], max_body_bytes=value['max_body_bytes'],
    request_timeout_seconds=value['request_timeout_seconds'],
    total_timeout_seconds=value['total_timeout_seconds'])
result = PinnedHttpFetcher(lambda host, port, timeout: [{SERVER_ADDRESS!r}]).fetch(
    value['url'], policy=policy)
print(json.dumps({{
    'schema_version': result.schema_version, 'original_url': result.original_url,
    'final_url': result.final_url, 'normalized_key': result.normalized_key,
    'outcome': result.outcome, 'http_status': result.http_status,
    'media_type': result.media_type, 'response_headers': result.response_headers,
    'redirect_chain': result.redirect_chain, 'resolved_address': result.resolved_address,
    'body': base64.b64encode(result.body).decode('ascii'),
    'body_sha256': result.body_sha256, 'decoded_bytes': result.decoded_bytes,
    'elapsed_ms': result.elapsed_ms,
}}))
"""
        completed = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                NETWORK,
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=1m,mode=1777",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--pids-limit",
                "64",
                "--memory",
                "128m",
                "--cpus",
                "0.5",
                "--label",
                f"{LABEL}={RUN_ID}",
                IMAGE,
                "-c",
                program,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        with self._lock:
            self.calls += 1
        record = json.loads(completed.stdout)
        record["body"] = base64.b64decode(record["body"], validate=True)
        record["response_headers"] = tuple(tuple(item) for item in record["response_headers"])
        record["redirect_chain"] = tuple(record["redirect_chain"])
        return CrawlFetchResult(**record)


def artifact_key():
    return ArtifactEncryptionKey("artifact-key:v1:joint-page-lab", bytes(range(32)))


def test_real_network_fetch_is_durably_authorized_observed_and_not_repeated(
    admin,
    api,
    scheduler,
    workflow,
    crawl_admission,
    crawl_ingest,
    scope,
    tmp_path,
):
    origin = "http://crawl.example"
    command = accept_snapshot(
        api, scope, actor_service="joint-page-lab", idempotency_key="real-page-attempt"
    )
    envelope = claim_outbox_batch(
        scheduler,
        tenant_id=scope.tenant_id,
        worker_key="worker.joint-page-lab",
        batch_size=1,
    )[0]
    admission = admit_command_event(workflow, envelope)
    started = record_workflow_started(
        workflow,
        admission,
        WorkflowStartReceipt(admission.workflow_id, str(uuid4()), "start_acknowledged"),
    )
    workflow_input = CrawlSiteWorkflowInput(
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
        max_body_bytes=1024,
        request_timeout_seconds=2,
        total_timeout_seconds=5,
    )
    run = open_crawl_run(
        crawl_admission,
        workflow_input,
        first_run_id=started.first_run_id,
        policy=policy,
        limits=CrawlRunLimits(),
        seed_url=f"{origin}/start",
    )
    store = EncryptedLocalArtifactStore(tmp_path / "artifacts")
    observed_at = datetime.now(UTC)
    from signal_core.crawl_http import RobotsFetchResult

    persist_robots_snapshot(
        crawl_ingest,
        store,
        run,
        policy,
        RobotsFetchResult(
            1,
            origin,
            f"{origin}/robots.txt",
            f"{origin}/robots.txt",
            "not_found",
            404,
            None,
            (),
            (),
            SERVER_ADDRESS,
            b"",
            None,
            0,
            5,
        ),
        snapshot_id=uuid4(),
        fetched_at=observed_at,
        network_profile_sha256=NETWORK_PROFILE,
        artifact_key=None,
        retain_until=None,
        recorded_at=observed_at + timedelta(milliseconds=10),
    )
    lease = claim_crawl_frontier(
        crawl_admission,
        run,
        worker_key="crawler.joint-page-lab",
        lease_id=uuid4(),
        lease_seconds=60,
    )
    assert lease is not None
    fetcher = DockerPinnedFetcher()
    permit_id = uuid4()
    arguments = dict(
        permit_id=permit_id,
        admission_policy=OriginAdmissionPolicy(),
        network_profile_sha256=NETWORK_PROFILE,
        artifact_key=artifact_key(),
        retain_until=datetime.now(UTC) + timedelta(days=30),
    )

    first = execute_crawl_page_attempt(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        lease,
        policy,
        fetcher,
        **arguments,
    )
    duplicate = execute_crawl_page_attempt(
        crawl_admission,
        crawl_ingest,
        store,
        run,
        lease,
        policy,
        fetcher,
        **arguments,
    )

    assert isinstance(first, CrawlPageAttemptReceipt)
    assert first.state == "observed"
    assert first.observation.final_url == f"{origin}/final?b=2&a=1"
    assert first.observation.redirect_chain == (f"{origin}/final?b=2&a=1",)
    assert first.observation.resolved_address == SERVER_ADDRESS
    assert store.read(first.observation.raw_artifact, key=artifact_key()) == (
        b"<html><title>Lab</title></html>"
    )
    assert duplicate.duplicate is True
    assert fetcher.calls == 1
    assert admin.execute(
        "SELECT attempt.state, observation.outcome, permit.completion_kind, "
        "permit.released_at IS NOT NULL FROM app.crawl_page_attempts attempt "
        "JOIN app.fetch_observations observation ON observation.tenant_id = attempt.tenant_id "
        "AND observation.site_id = attempt.site_id AND observation.id = attempt.observation_id "
        "JOIN control.admission_leases permit ON permit.id = attempt.origin_permit_id "
        "WHERE attempt.tenant_id = %s AND attempt.id = %s",
        (scope.tenant_id, permit_id),
    ).fetchone() == ("observed", "fetched", "success", True)
