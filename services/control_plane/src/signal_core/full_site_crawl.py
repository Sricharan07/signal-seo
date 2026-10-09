"""Verified, bounded site crawl composed from durable admission and egress boundaries."""

import asyncio
import hashlib
import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4, uuid5

from psycopg import Connection, OperationalError
from psycopg.types.json import Jsonb
from temporalio import activity

from signal_core.crawl_admission import OriginAdmissionPolicy
from signal_core.crawl_artifacts import (
    ArtifactEncryptionKey,
    ArtifactUnavailable,
    EncryptedLocalArtifactStore,
    FetchObservationRecorded,
    load_fetch_observation,
    persist_fetch_observation,
)
from signal_core.crawl_audit import analyze_crawl_manifest
from signal_core.crawl_frontier import (
    CrawlFrontierLease,
    CrawlFrontierRejected,
    CrawlFrontierUnavailable,
    CrawlRunLimits,
    CrawlRunOpened,
    claim_crawl_frontier,
    enqueue_crawl_url,
    list_crawl_recovery_leases,
    open_crawl_run,
)
from signal_core.crawl_http import CrawlFetchResult, EgressHttpRequest
from signal_core.crawl_parser import ParsedCrawlPage, parse_crawl_page
from signal_core.crawl_urls import CrawlScopePolicy, CrawlUrlRejected
from signal_core.crawl_workflow_activities import CrawlExecutionRejected, CrawlExecutionRetryable
from signal_core.database import _clean_transaction
from signal_core.egress_profiles import EgressProfile
from signal_core.shared_egress import (
    SharedEgressBlocked,
    SharedEgressDeferred,
    SharedEgressPending,
    SharedEgressReceipt,
    SharedEgressRequest,
    SharedRobotsDeferred,
    SharedRobotsPending,
    SharedRobotsPrepared,
    execute_shared_egress,
    prepare_robots_through_shared_egress,
)
from signal_core.workflow_contracts import (
    CrawlHeartbeat,
    CrawlManifestReference,
    CrawlSiteWorkflowInput,
    validate_crawl_workflow_input,
)

_NAMESPACE = UUID("e961576c-397b-426f-9c14-42020671ff9c")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_STATES = (
    "attempts_exhausted",
    "budget_exhausted",
    "dispatch_unknown",
    "fetched",
    "http_error",
    "parse_error",
    "policy_rejected",
    "robots_denied",
    "transport_error",
)


@dataclass(frozen=True)
class CrawlProgress:
    status: str
    started_at: datetime
    max_duration_seconds: int
    max_total_bytes: int
    discovered_count: int
    terminal_count: int
    consumed_bytes: int
    pending_count: int
    leased_count: int
    state_counts: dict[str, int]

    @property
    def deadline(self) -> datetime:
        return self.started_at + timedelta(seconds=self.max_duration_seconds)


class FullSiteCrawlExecutor:
    """One serial crawler for one exact verified origin and one Temporal command."""

    def __init__(
        self,
        *,
        admission_connection_factory: Callable[[], AbstractContextManager[Connection]],
        ingest_connection_factory: Callable[[], AbstractContextManager[Connection]],
        store: EncryptedLocalArtifactStore,
        artifact_key: ArtifactEncryptionKey,
        fetcher: object,
        network_profile_sha256: str,
        worker_key: str,
        user_agent: str = "SignalBot/1.0 (+https://signal.local/bot)",
        limits: CrawlRunLimits | None = None,
        origin_policy: OriginAdmissionPolicy | None = None,
    ) -> None:
        limits = limits if limits is not None else CrawlRunLimits()
        origin_policy = origin_policy if origin_policy is not None else OriginAdmissionPolicy()
        if (
            not callable(admission_connection_factory)
            or not callable(ingest_connection_factory)
            or not isinstance(store, EncryptedLocalArtifactStore)
            or not isinstance(artifact_key, ArtifactEncryptionKey)
            or not callable(getattr(fetcher, "request", None))
            or not isinstance(network_profile_sha256, str)
            or _SHA256.fullmatch(network_profile_sha256) is None
            or not isinstance(worker_key, str)
            or re.fullmatch(r"[a-z][a-z0-9_.:-]{0,127}", worker_key) is None
            or not isinstance(limits, CrawlRunLimits)
            or limits.max_duration_seconds > 3600
            or not isinstance(origin_policy, OriginAdmissionPolicy)
        ):
            raise ValueError("Validated full-site crawler dependencies are required.")
        self._admission_factory = admission_connection_factory
        self._ingest_factory = ingest_connection_factory
        self._store = store
        self._key = artifact_key
        self._fetcher = fetcher
        self._network_profile = network_profile_sha256
        self._worker_key = worker_key
        self._user_agent = user_agent
        self._limits = limits
        self._origin_policy = origin_policy

    async def execute(
        self,
        command: CrawlSiteWorkflowInput,
        *,
        heartbeat: Callable[[CrawlHeartbeat], None],
    ) -> CrawlManifestReference:
        run_id = activity.info().workflow_run_id
        if run_id is None:
            raise CrawlExecutionRejected()
        progress = [CrawlHeartbeat(1, "starting", 0, 0)]

        async def pulse() -> None:
            while True:
                heartbeat(progress[0])
                await asyncio.sleep(5)

        pulse_task = asyncio.create_task(pulse())
        cancelled = Event()
        run_task = asyncio.create_task(
            asyncio.to_thread(
                self.run,
                command,
                first_run_id=run_id,
                progress=lambda value: progress.__setitem__(0, value),
                cancelled=cancelled,
            )
        )
        try:
            return await asyncio.shield(run_task)
        except asyncio.CancelledError:
            cancelled.set()
            try:
                await asyncio.shield(run_task)
            except Exception:
                pass
            raise
        except (OperationalError, ArtifactUnavailable) as error:
            raise CrawlExecutionRetryable() from error
        finally:
            pulse_task.cancel()
            try:
                await pulse_task
            except asyncio.CancelledError:
                pass

    def run(
        self,
        command: CrawlSiteWorkflowInput,
        *,
        first_run_id: str,
        progress: Callable[[CrawlHeartbeat], None] | None = None,
        cancelled: Event | None = None,
    ) -> CrawlManifestReference:
        validated = validate_crawl_workflow_input(command)
        progress = progress or (lambda _: None)
        cancelled = cancelled or Event()
        tenant_id, site_id = UUID(validated.tenant_id), UUID(validated.site_id)
        with self._admission_factory() as admission, self._ingest_factory() as ingest:
            existing = _load_manifest(ingest, validated, first_run_id)
            if existing is not None:
                analyze_crawl_manifest(
                    ingest,
                    tenant_id=tenant_id,
                    site_id=site_id,
                    manifest_id=UUID(existing.manifest_id),
                )
                return existing
            with _clean_transaction(admission):
                verified = admission.execute(
                    "SELECT origin, outcome FROM control.verified_site_origin(%s, %s)",
                    (tenant_id, site_id),
                ).fetchone()
            if verified is None or verified[1] != "verified" or not isinstance(verified[0], str):
                raise CrawlExecutionRejected()
            origin = verified[0]
            policy = CrawlScopePolicy(
                schema_version=1,
                allowed_origins=(origin,),
                user_agent=self._user_agent,
                max_redirects=0,
            )
            run = open_crawl_run(
                admission,
                validated,
                first_run_id=first_run_id,
                policy=policy,
                limits=self._limits,
                seed_url=f"{origin}/",
            )
            while True:
                _check_cancelled(cancelled)
                current = _load_progress(ingest, run)
                progress(
                    CrawlHeartbeat(1, "crawling", current.discovered_count, current.terminal_count)
                )
                budget_end = datetime.now(UTC) >= current.deadline
                budget_full = current.consumed_bytes >= current.max_total_bytes
                recovered = list_crawl_recovery_leases(ingest, run, worker_key=self._worker_key)
                worked = False
                for item in recovered:
                    lease = item.lease
                    observation = load_fetch_observation(ingest, lease)
                    if observation is not None:
                        self._finish_observation(admission, ingest, run, lease, observation)
                    elif item.egress_operation_id is not None or item.robots_dispatched:
                        _settle(
                            ingest,
                            lease,
                            "dispatch_unknown",
                            "dispatch_outcome_unknown",
                            egress_operation_id=item.egress_operation_id,
                            robots_snapshot_id=item.egress_robots_snapshot_id,
                        )
                    elif budget_end or budget_full:
                        _settle(
                            ingest,
                            lease,
                            "budget_exhausted",
                            "duration_budget" if budget_end else "byte_budget",
                        )
                    elif lease.lease_until > datetime.now(UTC):
                        self._process(admission, ingest, run, lease, policy, current, cancelled)
                    else:
                        continue
                    worked = True
                    break
                if worked:
                    continue
                if not budget_end and not budget_full:
                    try:
                        lease = claim_crawl_frontier(
                            admission,
                            run,
                            worker_key=self._worker_key,
                            lease_id=uuid4(),
                            lease_seconds=120,
                        )
                    except CrawlFrontierUnavailable:
                        lease = None
                    if lease is not None:
                        self._process(admission, ingest, run, lease, policy, current, cancelled)
                        continue
                current = _load_progress(ingest, run)
                if current.terminal_count == current.discovered_count:
                    progress(
                        CrawlHeartbeat(
                            1, "finalizing", current.discovered_count, current.terminal_count
                        )
                    )
                    manifest = _finalize(ingest, run, validated)
                    analyze_crawl_manifest(
                        ingest,
                        tenant_id=tenant_id,
                        site_id=site_id,
                        manifest_id=UUID(manifest.manifest_id),
                    )
                    return manifest
                if budget_end or budget_full or current.pending_count == 0:
                    finalized = _finalize(ingest, run, validated, allow_not_ready=True)
                    if finalized is not None:
                        analyze_crawl_manifest(
                            ingest,
                            tenant_id=tenant_id,
                            site_id=site_id,
                            manifest_id=UUID(finalized.manifest_id),
                        )
                        return finalized
                time.sleep(
                    min(1.0, max(0.1, (current.deadline - datetime.now(UTC)).total_seconds()))
                )

    def _process(
        self,
        admission: Connection,
        ingest: Connection,
        run: CrawlRunOpened,
        lease: CrawlFrontierLease,
        policy: CrawlScopePolicy,
        current: CrawlProgress,
        cancelled: Event,
    ) -> None:
        _check_cancelled(cancelled)
        identity = f"{run.tenant_id}:{lease.lease_id}"
        robots = prepare_robots_through_shared_egress(
            admission,
            ingest,
            self._store,
            run,
            lease,
            policy,
            self._fetcher,
            dispatch_id=uuid5(_NAMESPACE, f"robots:{identity}"),
            permit_id=uuid5(_NAMESPACE, f"robots-permit:{identity}"),
            worker_key=self._worker_key,
            admission_policy=self._origin_policy,
            network_profile_sha256=self._network_profile,
            artifact_key=self._key,
            retain_until=datetime.now(UTC) + timedelta(days=30),
        )
        if isinstance(robots, SharedRobotsDeferred):
            _wait_until(robots.retry_at, lease.lease_until, current.deadline)
            return
        if isinstance(robots, SharedRobotsPending):
            _settle(ingest, lease, "dispatch_unknown", "dispatch_outcome_unknown")
            return
        if not isinstance(robots, SharedRobotsPrepared):
            raise RuntimeError("Robots gateway returned invalid evidence.")
        if not robots.decision.allowed:
            _settle(
                ingest,
                lease,
                "robots_denied",
                "policy",
                robots_snapshot_id=robots.decision.snapshot_id,
            )
            return
        remaining = current.max_total_bytes - current.consumed_bytes
        if remaining <= 0:
            _settle(ingest, lease, "budget_exhausted", "byte_budget")
            return
        _check_cancelled(cancelled)
        request = SharedEgressRequest(
            "crawl",
            EgressHttpRequest(
                "GET",
                lease.url.fetch_url,
                headers=(("accept", "text/html,application/xhtml+xml"),),
                accepted_media_types=("text/html", "application/xhtml+xml"),
                max_response_bytes=min(policy.max_body_bytes, remaining),
                timeout_seconds=policy.total_timeout_seconds,
            ),
            EgressProfile.CRAWL_PAGE,
        )
        operation_id = _stable_v4(f"page:{identity}")
        result = execute_shared_egress(
            admission,
            ingest,
            self._store,
            run,
            policy,
            self._fetcher,
            request,
            operation_id=operation_id,
            worker_key=self._worker_key,
            admission_policy=self._origin_policy,
            artifact_key=self._key,
        )
        if isinstance(result, SharedEgressDeferred):
            _wait_until(result.retry_at, lease.lease_until, current.deadline)
            return
        if isinstance(result, SharedEgressPending):
            _settle(
                ingest,
                lease,
                "dispatch_unknown",
                "dispatch_outcome_unknown",
                egress_operation_id=operation_id,
                robots_snapshot_id=robots.decision.snapshot_id,
            )
            return
        if isinstance(result, SharedEgressBlocked):
            _settle(
                ingest,
                lease,
                "robots_denied",
                "policy",
                robots_snapshot_id=result.robots_snapshot_id,
            )
            return
        if not isinstance(result, SharedEgressReceipt):
            raise RuntimeError("Shared egress returned invalid evidence.")
        response = result.response
        if (
            response.outcome != "fetched"
            or response.http_status is None
            or not (200 <= response.http_status < 300)
        ):
            state, error_class = _classify_response(response.outcome, response.http_status)
            _settle(
                ingest,
                lease,
                state,
                error_class,
                egress_operation_id=operation_id,
                robots_snapshot_id=robots.decision.snapshot_id,
                decoded_bytes=response.decoded_bytes,
            )
            return
        fetch = CrawlFetchResult(
            schema_version=1,
            original_url=lease.url.original_url,
            final_url=response.final_url,
            normalized_key=lease.url.normalized_key,
            outcome="fetched",
            http_status=response.http_status,
            media_type=response.media_type,
            response_headers=response.response_headers,
            redirect_chain=(),
            resolved_address=response.resolved_address or "",
            body=response.body,
            body_sha256=response.body_sha256,
            decoded_bytes=response.decoded_bytes,
            elapsed_ms=response.elapsed_ms,
        )
        started = result.finished_at - timedelta(milliseconds=fetch.elapsed_ms)
        recorded_at = max(datetime.now(UTC), result.finished_at)
        observation = persist_fetch_observation(
            ingest,
            self._store,
            lease,
            fetch,
            started_at=started,
            finished_at=result.finished_at,
            network_profile_sha256=self._network_profile,
            artifact_key=self._key,
            retain_until=recorded_at + timedelta(days=30),
            recorded_at=recorded_at,
        )
        self._finish_observation(
            admission,
            ingest,
            run,
            lease,
            observation,
            egress_operation_id=operation_id,
            robots_snapshot_id=robots.decision.snapshot_id,
        )

    def _finish_observation(
        self,
        admission: Connection,
        ingest: Connection,
        run: CrawlRunOpened,
        lease: CrawlFrontierLease,
        observation: FetchObservationRecorded,
        *,
        egress_operation_id: UUID | None = None,
        robots_snapshot_id: UUID | None = None,
    ) -> None:
        if observation.raw_artifact is None or observation.outcome != "fetched":
            _settle(ingest, lease, "parse_error", "parser", decoded_bytes=observation.decoded_bytes)
            return
        if egress_operation_id is None or robots_snapshot_id is None:
            egress_operation_id = _stable_v4(f"page:{run.tenant_id}:{lease.lease_id}")
            with _clean_transaction(ingest):
                binding = ingest.execute(
                    "SELECT operation.robots_snapshot_id "
                    "FROM app.egress_operations AS operation "
                    "WHERE operation.tenant_id = %s AND operation.site_id = %s "
                    "AND operation.crawl_run_id = %s AND operation.id = %s "
                    "AND operation.purpose = 'crawl' AND operation.request_url = %s "
                    "AND operation.state = 'observed' "
                    "AND operation.network_outcome = 'fetched' "
                    "AND operation.http_status = %s AND operation.response_sha256 = %s "
                    "AND operation.response_bytes = %s",
                    (
                        run.tenant_id,
                        run.site_id,
                        run.run_id,
                        egress_operation_id,
                        lease.url.fetch_url,
                        observation.http_status,
                        bytes.fromhex(observation.raw_artifact.sha256),
                        observation.decoded_bytes,
                    ),
                ).fetchone()
            if binding is None:
                raise RuntimeError("Fetched observation has no durable egress binding.")
            robots_snapshot_id = binding[0]
        body = self._store.read(observation.raw_artifact, key=self._key)
        try:
            parsed = parse_crawl_page(
                body, page_url=lease.url.fetch_url, exact_origin=lease.url.origin
            )
        except (ValueError, RecursionError):
            _settle(
                ingest,
                lease,
                "parse_error",
                "parser",
                egress_operation_id=egress_operation_id,
                robots_snapshot_id=robots_snapshot_id,
                observation_id=observation.observation_id,
                decoded_bytes=observation.decoded_bytes,
            )
            return
        page_id = _record_page(ingest, lease, observation, parsed)
        if lease.depth < lease.limits.max_depth:
            for url in parsed.internal_links:
                try:
                    enqueue_crawl_url(
                        admission,
                        run,
                        discovered_url=url,
                        discovered_from_url_id=lease.url_id,
                        depth=lease.depth + 1,
                        discovery_reason="internal_link",
                        priority=500,
                    )
                except (CrawlFrontierRejected, CrawlUrlRejected):
                    continue
        _settle(
            ingest,
            lease,
            "fetched",
            None,
            egress_operation_id=egress_operation_id,
            robots_snapshot_id=robots_snapshot_id,
            observation_id=observation.observation_id,
            page_record_id=page_id,
            decoded_bytes=observation.decoded_bytes,
        )


def _load_manifest(
    connection: Connection, command: CrawlSiteWorkflowInput, first_run_id: str
) -> CrawlManifestReference | None:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.load_crawl_manifest(%s, %s, %s, %s)",
            (
                UUID(command.tenant_id),
                UUID(command.site_id),
                UUID(command.command_id),
                first_run_id,
            ),
        ).fetchone()
    if row is None:
        return None
    return CrawlManifestReference(
        1, str(row[0]), row[1].hex(), row[2], row[3], row[4], row[5], row[6]
    )


def _load_progress(connection: Connection, run: CrawlRunOpened) -> CrawlProgress:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.crawl_run_progress(%s, %s, %s)",
            (run.tenant_id, run.site_id, run.run_id),
        ).fetchone()
    if row is None or row[10] != "found":
        raise RuntimeError("Crawl progress is unavailable.")
    counts = row[9]
    if not isinstance(counts, dict) or set(counts) != set(_STATES):
        raise RuntimeError("Crawl progress state counts are invalid.")
    return CrawlProgress(*row[:9], counts)


def _record_page(
    connection: Connection,
    lease: CrawlFrontierLease,
    observation: FetchObservationRecorded,
    parsed: ParsedCrawlPage,
) -> UUID:
    page_id = uuid5(_NAMESPACE, f"page-record:{lease.tenant_id}:{lease.frontier_id}")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT page_record_id, duplicate, outcome FROM control.record_crawl_page("
            + ", ".join(["%s"] * 20)
            + ")",
            (
                lease.tenant_id,
                lease.site_id,
                lease.run_id,
                lease.frontier_id,
                lease.url_id,
                observation.observation_id,
                page_id,
                observation.http_status,
                parsed.canonical_url,
                parsed.title,
                parsed.meta_description,
                Jsonb(list(parsed.robots_meta)),
                Jsonb([{"level": h.level, "text": h.text} for h in parsed.headings]),
                Jsonb([{"language": h.language, "url": h.url} for h in parsed.hreflang]),
                Jsonb(list(parsed.structured_data_types)),
                Jsonb(list(parsed.internal_links)),
                Jsonb(list(parsed.external_links)),
                bytes.fromhex(parsed.body_sha256),
                parsed.parse_error_count,
                parsed.output_truncated,
            ),
        ).fetchone()
        if row is None or row[2] != "recorded" or row[0] != page_id:
            raise RuntimeError("Crawl page evidence was not committed.")
        image_result = connection.execute(
            "SELECT control.record_crawl_image_evidence(%s, %s, %s, %s, %s)",
            (
                lease.tenant_id,
                lease.site_id,
                page_id,
                bytes.fromhex(parsed.body_sha256),
                Jsonb(list(parsed.missing_alt_images)),
            ),
        ).fetchone()
        if image_result is None or image_result[0] != "recorded":
            raise RuntimeError("Crawl image evidence was not committed.")
    return page_id


def _settle(
    connection: Connection,
    lease: CrawlFrontierLease,
    state: str,
    error_class: str | None,
    *,
    egress_operation_id: UUID | None = None,
    robots_snapshot_id: UUID | None = None,
    observation_id: UUID | None = None,
    page_record_id: UUID | None = None,
    decoded_bytes: int = 0,
) -> None:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT terminal_state, consumed_bytes, duplicate, outcome "
            "FROM control.settle_crawl_frontier(" + ", ".join(["%s"] * 14) + ")",
            (
                lease.tenant_id,
                lease.site_id,
                lease.run_id,
                lease.frontier_id,
                lease.url_id,
                lease.lease_id,
                lease.frontier_id,
                state,
                error_class,
                egress_operation_id,
                robots_snapshot_id,
                observation_id,
                page_record_id,
                decoded_bytes,
            ),
        ).fetchone()
    if row is None or row[3] != "settled" or row[0] != state:
        raise RuntimeError("Crawl frontier could not be settled safely.")


def _finalize(
    connection: Connection,
    run: CrawlRunOpened,
    command: CrawlSiteWorkflowInput,
    *,
    allow_not_ready: bool = False,
) -> CrawlManifestReference | None:
    manifest_id = uuid5(_NAMESPACE, f"manifest:{run.tenant_id}:{run.run_id}")
    current = _load_progress(connection, run)
    material = (
        f"crawl-manifest-v1|{run.run_id}|{current.discovered_count}|"
        f"{current.terminal_count}|{current.consumed_bytes}"
        + "".join(f"|{state}={current.state_counts[state]}" for state in _STATES)
    )
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    for _ in range(2):
        with _clean_transaction(connection):
            row = connection.execute(
                "SELECT * FROM control.finalize_crawl_run(%s, %s, %s, %s, %s)",
                (run.tenant_id, run.site_id, run.run_id, manifest_id, digest),
            ).fetchone()
        if row is None:
            raise RuntimeError("Crawl manifest finalization returned no outcome.")
        if row[9] == "manifest_hash_mismatch" and isinstance(row[1], bytes):
            digest = row[1]
            continue
        if row[9] == "not_ready" and allow_not_ready:
            return None
        if row[9] != "finalized" or row[0] != manifest_id or row[1] != digest:
            raise RuntimeError("Crawl manifest finalization failed safely.")
        return CrawlManifestReference(
            1,
            str(manifest_id),
            digest.hex(),
            row[2],
            row[3],
            row[4],
            command.scope_version,
            command.crawl_policy_version,
        )
    raise RuntimeError("Crawl manifest changed during finalization.")


def _classify_response(outcome: str, status: int | None) -> tuple[str, str]:
    if outcome == "transport_error":
        return "transport_error", "transport"
    if outcome == "policy_rejected":
        return "policy_rejected", "policy"
    if outcome == "redirect_rejected":
        return "http_error", "redirect"
    if outcome in {"body_limit", "unsupported_encoding", "unsupported_media_type"}:
        return "http_error", outcome
    if status == 429:
        return "http_error", "rate_limited"
    if status is not None and status >= 500:
        return "http_error", "server_error"
    return "http_error", "client_error"


def _stable_v4(value: str) -> UUID:
    return UUID(bytes=hashlib.sha256(value.encode("ascii")).digest()[:16], version=4)


def _wait_until(retry_at: datetime, lease_until: datetime, deadline: datetime) -> None:
    remaining = min(retry_at, lease_until, deadline) - datetime.now(UTC)
    if remaining.total_seconds() > 0:
        time.sleep(min(remaining.total_seconds(), 5.0))


def _check_cancelled(cancelled: Event) -> None:
    if cancelled.is_set():
        raise CrawlExecutionRetryable("Crawl activity was cancelled before the next dispatch.")
