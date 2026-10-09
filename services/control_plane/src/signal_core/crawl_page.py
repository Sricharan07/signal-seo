"""Durable, retry-conservative composition of one admitted crawl page request."""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from uuid import UUID

from psycopg import Connection
from psycopg import Error as DatabaseError
from psycopg.pq import TransactionStatus

from signal_core.crawl_admission import (
    OriginAdmissionPolicy,
    OriginPermitCompletion,
    OriginPermitConflict,
    OriginPermitDeferred,
    OriginPermitExpired,
    OriginPermitGrant,
    OriginPermitReplay,
    acquire_origin_permit,
    finish_origin_permit,
)
from signal_core.crawl_artifacts import (
    ArtifactConflict,
    ArtifactEncryptionKey,
    ArtifactIntegrityError,
    ArtifactUnavailable,
    EncryptedLocalArtifactStore,
    FetchObservationConflict,
    FetchObservationRecorded,
    FetchObservationUnavailable,
    load_fetch_observation,
    persist_fetch_observation,
)
from signal_core.crawl_frontier import CrawlFrontierLease, CrawlRunOpened
from signal_core.crawl_http import CrawlFetchRejected, CrawlFetchResult, CrawlFetchUnavailable
from signal_core.crawl_robots import (
    RobotsAccessDecision,
    RobotsSnapshotUnavailable,
    authorize_from_current_snapshot,
)
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.database import _clean_transaction


class CrawlPageAttemptUnavailable(RuntimeError):
    """Current authority cannot start or reconcile the exact page attempt."""


class CrawlPageAttemptConflict(RuntimeError):
    """A durable page-attempt identity is bound to different evidence."""


class CrawlPageAttemptExpired(RuntimeError):
    """The request permit expired before exact completion was committed."""


@dataclass(frozen=True, repr=False)
class CrawlPageAttemptReceipt:
    attempt_id: UUID
    tenant_id: UUID
    site_id: UUID
    crawl_run_id: UUID
    frontier_id: UUID
    url_id: UUID
    fetch_attempt_id: UUID
    robots_snapshot_id: UUID
    robots_reason: str
    origin_permit_id: UUID
    dispatched_at: datetime
    finished_at: datetime
    state: str
    terminal_reason: str | None
    completion_kind: str
    observed_latency_ms: int
    retry_after_ms: int | None
    backoff_basis: str
    observation: FetchObservationRecorded | None
    duplicate: bool


@dataclass(frozen=True, repr=False)
class CrawlPageAttemptPending:
    attempt_id: UUID
    crawl_run_id: UUID
    frontier_id: UUID
    fetch_attempt_id: UUID
    origin_permit_id: UUID
    dispatched_at: datetime | None
    reason: str


@dataclass(frozen=True, repr=False)
class CrawlPageAttemptBlocked:
    crawl_run_id: UUID
    frontier_id: UUID
    url_id: UUID
    robots_snapshot_id: UUID
    reason: str
    snapshot_expires_at: datetime


@dataclass(frozen=True, repr=False)
class CrawlPageAttemptDeferred:
    crawl_run_id: UUID
    frontier_id: UUID
    origin: str
    reason: str
    retry_at: datetime


@dataclass(frozen=True, repr=False)
class _AttemptRecord:
    attempt_id: UUID
    state: str
    crawl_run_id: UUID
    frontier_id: UUID
    url_id: UUID
    fetch_attempt_id: UUID
    robots_snapshot_id: UUID
    robots_reason: str
    origin_permit_id: UUID
    dispatched_at: datetime
    finished_at: datetime | None
    observation_id: UUID | None
    terminal_reason: str | None
    completion_kind: str | None
    observed_latency_ms: int | None
    retry_after_ms: int | None
    backoff_basis: str | None
    duplicate: bool


def execute_crawl_page_attempt(
    admission_connection: Connection,
    ingest_connection: Connection,
    store: EncryptedLocalArtifactStore,
    run: CrawlRunOpened,
    lease: CrawlFrontierLease,
    policy: CrawlScopePolicy,
    fetcher: object,
    *,
    permit_id: UUID,
    admission_policy: OriginAdmissionPolicy,
    network_profile_sha256: str,
    artifact_key: ArtifactEncryptionKey,
    retain_until: datetime,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> (
    CrawlPageAttemptReceipt
    | CrawlPageAttemptPending
    | CrawlPageAttemptBlocked
    | CrawlPageAttemptDeferred
):
    """Execute at most one HTTP request after durable dispatch authorization."""
    now = _validate_inputs(
        admission_connection,
        ingest_connection,
        store,
        run,
        lease,
        policy,
        fetcher,
        permit_id,
        admission_policy,
        network_profile_sha256,
        artifact_key,
        retain_until,
        clock,
    )
    existing = _get_attempt(ingest_connection, lease, permit_id)
    if existing is not None:
        return _reconcile_attempt(ingest_connection, lease, existing)

    try:
        robots = authorize_from_current_snapshot(
            ingest_connection,
            store,
            run,
            policy,
            lease.url.fetch_url,
            at_time=now,
            artifact_key=artifact_key,
        )
    except RobotsSnapshotUnavailable:
        raise CrawlPageAttemptUnavailable("Current robots evidence is unavailable.") from None
    if not robots.allowed:
        return CrawlPageAttemptBlocked(
            run.run_id,
            lease.frontier_id,
            lease.url_id,
            robots.snapshot_id,
            robots.reason,
            robots.expires_at,
        )

    effective_policy = replace(
        admission_policy,
        min_delay_ms=max(1000, admission_policy.min_delay_ms, robots.crawl_delay_ms or 1000),
    )
    permit = acquire_origin_permit(
        admission_connection,
        lease,
        permit_id=permit_id,
        permit_kind="html_navigation",
        policy=effective_policy,
    )
    if isinstance(permit, OriginPermitDeferred):
        return CrawlPageAttemptDeferred(
            run.run_id, lease.frontier_id, lease.url.origin, permit.reason, permit.retry_at
        )
    if isinstance(permit, OriginPermitReplay):
        existing = _get_attempt(ingest_connection, lease, permit_id)
        if existing is not None:
            return _reconcile_attempt(ingest_connection, lease, existing)
        return CrawlPageAttemptPending(
            permit_id,
            run.run_id,
            lease.frontier_id,
            lease.lease_id,
            permit_id,
            None,
            "permit_finished_without_attempt",
        )
    if not isinstance(permit, OriginPermitGrant):
        raise RuntimeError("Origin admission returned an unsupported permit outcome.")

    begun = _begin_attempt(ingest_connection, lease, permit, robots)
    if begun is None:
        _release_unused_permit(admission_connection, permit)
        raise CrawlPageAttemptUnavailable("Page dispatch authority is unavailable.")
    if begun.duplicate:
        return _reconcile_attempt(ingest_connection, lease, begun)

    started_at = max(_clock_value(clock), begun.dispatched_at)
    try:
        result = fetcher.fetch(lease.url.fetch_url, policy=policy)
    except CrawlFetchRejected:
        latency = _wall_latency(started_at, _clock_value(clock))
        return _finish_failure(
            ingest_connection,
            lease,
            begun,
            terminal_reason="policy_rejected",
            completion_kind="cancelled",
            observed_latency_ms=latency,
            backoff_basis="none",
        )
    except CrawlFetchUnavailable:
        latency = _wall_latency(started_at, _clock_value(clock))
        return _finish_failure(
            ingest_connection,
            lease,
            begun,
            terminal_reason="transport_error",
            completion_kind="transport_error",
            observed_latency_ms=latency,
            backoff_basis="none",
        )
    if not isinstance(result, CrawlFetchResult):
        return _finish_failure(
            ingest_connection,
            lease,
            begun,
            terminal_reason="policy_rejected",
            completion_kind="cancelled",
            observed_latency_ms=_wall_latency(started_at, _clock_value(clock)),
            backoff_basis="none",
        )

    finished_at = started_at + timedelta(milliseconds=result.elapsed_ms)
    recorded_at = max(_clock_value(clock), finished_at)
    try:
        observation = persist_fetch_observation(
            ingest_connection,
            store,
            lease,
            result,
            started_at=started_at,
            finished_at=finished_at,
            network_profile_sha256=network_profile_sha256,
            artifact_key=artifact_key if result.outcome == "fetched" else None,
            retain_until=retain_until if result.outcome == "fetched" else None,
            recorded_at=recorded_at,
        )
    except ValueError:
        return _finish_failure(
            ingest_connection,
            lease,
            begun,
            terminal_reason="policy_rejected",
            completion_kind="cancelled",
            observed_latency_ms=_bounded_latency(result.elapsed_ms),
            backoff_basis="none",
        )
    except (
        ArtifactConflict,
        ArtifactIntegrityError,
        ArtifactUnavailable,
        FetchObservationConflict,
        FetchObservationUnavailable,
    ):
        return _finish_failure(
            ingest_connection,
            lease,
            begun,
            terminal_reason="observation_persistence_failed",
            completion_kind="service_unavailable",
            observed_latency_ms=_bounded_latency(result.elapsed_ms),
            backoff_basis="local_persistence_failure",
        )
    return _finish_observed(ingest_connection, lease, begun, observation)


def classify_origin_completion(
    observation: FetchObservationRecorded,
) -> tuple[OriginPermitCompletion, str]:
    """Map persisted HTTP evidence to bounded global-origin feedback."""
    if not isinstance(observation, FetchObservationRecorded):
        raise ValueError("A validated fetch observation is required.")
    if observation.http_status not in {429, 503}:
        return OriginPermitCompletion("success", observation.elapsed_ms), "none"
    retry_header = dict(observation.response_headers).get("retry-after")
    retry_after_ms, basis = _retry_after(
        retry_header,
        observed_at=observation.finished_at,
        fallback_ms=60_000 if observation.http_status == 429 else None,
    )
    kind = "rate_limited" if observation.http_status == 429 else "service_unavailable"
    return OriginPermitCompletion(kind, observation.elapsed_ms, retry_after_ms), basis


def _get_attempt(
    connection: Connection, lease: CrawlFrontierLease, permit_id: UUID
) -> _AttemptRecord | None:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT attempt_id, attempt_state, crawl_run_id, frontier_id, url_id, "
            "fetch_attempt_id, robots_snapshot_id, robots_reason, origin_permit_id, "
            "dispatched_at, finished_at, observation_id, terminal_reason, completion_kind, "
            "observed_latency_ms, retry_after_ms, backoff_basis, duplicate, outcome "
            "FROM control.get_crawl_page_attempt(%s, %s, %s, %s, %s, %s, %s)",
            _attempt_scope(lease, permit_id),
        ).fetchone()
    if row is None:
        raise RuntimeError("Crawl page-attempt lookup returned no outcome.")
    if row[18] == "missing":
        return None
    if row[18] != "found":
        raise RuntimeError("Crawl page-attempt lookup returned an invalid outcome.")
    return _attempt_from_row(row, lease, permit_id)


def _begin_attempt(
    connection: Connection,
    lease: CrawlFrontierLease,
    permit: OriginPermitGrant,
    robots: RobotsAccessDecision,
) -> _AttemptRecord | None:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT attempt_id, attempt_state, crawl_run_id, frontier_id, url_id, "
            "fetch_attempt_id, robots_snapshot_id, robots_reason, origin_permit_id, "
            "dispatched_at, finished_at, observation_id, terminal_reason, completion_kind, "
            "observed_latency_ms, retry_after_ms, backoff_basis, duplicate, outcome "
            "FROM control.begin_crawl_page_attempt(" + ", ".join(["%s"] * 10) + ")",
            (
                lease.tenant_id,
                lease.site_id,
                lease.run_id,
                lease.frontier_id,
                lease.url_id,
                lease.lease_id,
                lease.lease_owner,
                permit.permit_id,
                robots.snapshot_id,
                robots.reason,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Crawl page-attempt begin returned no outcome.")
    if row[18] in {"authority_unavailable", "permit_unavailable"}:
        return None
    if row[18] == "attempt_conflict":
        raise CrawlPageAttemptConflict()
    if row[18] != "begun":
        raise RuntimeError("Crawl page-attempt begin returned an invalid outcome.")
    return _attempt_from_row(row, lease, permit.permit_id)


def _finish_observed(
    connection: Connection,
    lease: CrawlFrontierLease,
    attempt: _AttemptRecord,
    observation: FetchObservationRecorded,
) -> CrawlPageAttemptReceipt:
    completion, basis = classify_origin_completion(observation)
    record = _finish_attempt(
        connection,
        lease,
        attempt,
        observation_id=observation.observation_id,
        terminal_reason=None,
        completion=completion,
        backoff_basis=basis,
    )
    return _receipt(record, lease, observation)


def _finish_failure(
    connection: Connection,
    lease: CrawlFrontierLease,
    attempt: _AttemptRecord,
    *,
    terminal_reason: str,
    completion_kind: str,
    observed_latency_ms: int,
    backoff_basis: str,
) -> CrawlPageAttemptReceipt:
    completion = OriginPermitCompletion(completion_kind, _bounded_latency(observed_latency_ms))
    record = _finish_attempt(
        connection,
        lease,
        attempt,
        observation_id=None,
        terminal_reason=terminal_reason,
        completion=completion,
        backoff_basis=backoff_basis,
    )
    return _receipt(record, lease, None)


def _finish_attempt(
    connection: Connection,
    lease: CrawlFrontierLease,
    attempt: _AttemptRecord,
    *,
    observation_id: UUID | None,
    terminal_reason: str | None,
    completion: OriginPermitCompletion,
    backoff_basis: str,
) -> _AttemptRecord:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT attempt_id, attempt_state, crawl_run_id, frontier_id, url_id, "
            "fetch_attempt_id, robots_snapshot_id, robots_reason, origin_permit_id, "
            "dispatched_at, finished_at, observation_id, terminal_reason, completion_kind, "
            "observed_latency_ms, retry_after_ms, backoff_basis, duplicate, outcome "
            "FROM control.finish_crawl_page_attempt(" + ", ".join(["%s"] * 14) + ")",
            (
                lease.tenant_id,
                lease.site_id,
                lease.run_id,
                lease.frontier_id,
                lease.url_id,
                lease.lease_id,
                lease.lease_owner,
                attempt.origin_permit_id,
                observation_id,
                terminal_reason,
                completion.kind,
                completion.observed_latency_ms,
                completion.retry_after_ms,
                backoff_basis,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Crawl page-attempt completion returned no outcome.")
    if row[18] in {"attempt_conflict", "observation_conflict", "permit_conflict"}:
        raise CrawlPageAttemptConflict()
    if row[18] in {"attempt_unavailable", "permit_unavailable"}:
        raise CrawlPageAttemptUnavailable()
    if row[18] == "permit_expired":
        raise CrawlPageAttemptExpired()
    if row[18] != "finished":
        raise RuntimeError("Crawl page-attempt completion returned an invalid outcome.")
    return _attempt_from_row(row, lease, attempt.origin_permit_id)


def _reconcile_attempt(
    connection: Connection,
    lease: CrawlFrontierLease,
    attempt: _AttemptRecord,
) -> CrawlPageAttemptReceipt | CrawlPageAttemptPending:
    observation = load_fetch_observation(connection, lease)
    if attempt.state == "dispatched":
        if observation is None:
            return CrawlPageAttemptPending(
                attempt.attempt_id,
                attempt.crawl_run_id,
                attempt.frontier_id,
                attempt.fetch_attempt_id,
                attempt.origin_permit_id,
                attempt.dispatched_at,
                "dispatch_outcome_unknown",
            )
        return _finish_observed(connection, lease, attempt, observation)
    if attempt.state == "observed":
        if observation is None or observation.observation_id != attempt.observation_id:
            raise CrawlPageAttemptConflict("Terminal attempt is missing its exact observation.")
        return _receipt(attempt, lease, observation)
    if attempt.state == "failed":
        if observation is not None:
            raise CrawlPageAttemptConflict("Failed attempt has unexpected observation evidence.")
        return _receipt(attempt, lease, None)
    raise RuntimeError("Crawl page-attempt state is invalid.")


def _attempt_from_row(row: tuple, lease: CrawlFrontierLease, permit_id: UUID) -> _AttemptRecord:
    record = _AttemptRecord(
        attempt_id=row[0],
        state=row[1],
        crawl_run_id=row[2],
        frontier_id=row[3],
        url_id=row[4],
        fetch_attempt_id=row[5],
        robots_snapshot_id=row[6],
        robots_reason=row[7],
        origin_permit_id=row[8],
        dispatched_at=row[9],
        finished_at=row[10],
        observation_id=row[11],
        terminal_reason=row[12],
        completion_kind=row[13],
        observed_latency_ms=row[14],
        retry_after_ms=row[15],
        backoff_basis=row[16],
        duplicate=row[17],
    )
    terminal = record.state in {"observed", "failed"}
    if (
        record.attempt_id != permit_id
        or record.origin_permit_id != permit_id
        or record.crawl_run_id != lease.run_id
        or record.frontier_id != lease.frontier_id
        or record.url_id != lease.url_id
        or record.fetch_attempt_id != lease.lease_id
        or not isinstance(record.robots_snapshot_id, UUID)
        or record.robots_reason not in {"allowed_by_rules", "robots_not_found"}
        or record.state not in {"dispatched", "observed", "failed"}
        or not _aware(record.dispatched_at)
        or not isinstance(record.duplicate, bool)
        or (terminal != (record.finished_at is not None))
        or (record.finished_at is not None and not _aware(record.finished_at))
        or (record.finished_at is not None and record.finished_at < record.dispatched_at)
        or (record.state == "observed" and record.observation_id is None)
        or (record.state != "observed" and record.observation_id is not None)
        or (record.state == "failed" and record.terminal_reason is None)
        or (record.state != "failed" and record.terminal_reason is not None)
        or (
            not terminal
            and any(
                value is not None
                for value in (
                    record.completion_kind,
                    record.observed_latency_ms,
                    record.retry_after_ms,
                    record.backoff_basis,
                )
            )
        )
        or (terminal and record.completion_kind is None)
        or (terminal and record.observed_latency_ms is None)
        or (terminal and record.backoff_basis is None)
        or (terminal and not _valid_terminal(record))
    ):
        raise RuntimeError("Crawl page-attempt operation returned invalid evidence.")
    return record


def _receipt(
    attempt: _AttemptRecord,
    lease: CrawlFrontierLease,
    observation: FetchObservationRecorded | None,
) -> CrawlPageAttemptReceipt:
    if (
        attempt.finished_at is None
        or attempt.completion_kind is None
        or attempt.observed_latency_ms is None
        or attempt.backoff_basis is None
    ):
        raise RuntimeError("A non-terminal crawl page attempt cannot produce a receipt.")
    return CrawlPageAttemptReceipt(
        attempt.attempt_id,
        lease.tenant_id,
        lease.site_id,
        attempt.crawl_run_id,
        attempt.frontier_id,
        attempt.url_id,
        attempt.fetch_attempt_id,
        attempt.robots_snapshot_id,
        attempt.robots_reason,
        attempt.origin_permit_id,
        attempt.dispatched_at,
        attempt.finished_at,
        attempt.state,
        attempt.terminal_reason,
        attempt.completion_kind,
        attempt.observed_latency_ms,
        attempt.retry_after_ms,
        attempt.backoff_basis,
        observation,
        attempt.duplicate,
    )


def _release_unused_permit(connection: Connection, permit: OriginPermitGrant) -> None:
    try:
        finish_origin_permit(connection, permit, OriginPermitCompletion("cancelled", 0))
    except (DatabaseError, OriginPermitConflict, OriginPermitExpired, RuntimeError):
        # The bounded lease remains the conservative recovery path when release is unavailable.
        return


def _retry_after(
    value: str | None,
    *,
    observed_at: datetime,
    fallback_ms: int | None,
) -> tuple[int | None, str]:
    if value is None:
        return fallback_ms, "fallback_missing"
    if not isinstance(value, str) or not value or len(value) > 256:
        return fallback_ms, "fallback_invalid"
    seconds: float
    basis: str
    if value.isdigit():
        seconds = float(int(value))
        basis = "provider_seconds"
    else:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                raise ValueError
            seconds = (parsed.astimezone(UTC) - observed_at.astimezone(UTC)).total_seconds()
            basis = "provider_date"
        except (OverflowError, TypeError, ValueError):
            return fallback_ms, "fallback_invalid"
    if not math.isfinite(seconds) or seconds <= 0:
        return fallback_ms, "fallback_invalid"
    milliseconds = math.ceil(seconds * 1000)
    if milliseconds > 86_400_000:
        return 86_400_000, "capped"
    return max(1000, milliseconds), basis


def _validate_inputs(
    admission_connection: object,
    ingest_connection: object,
    store: object,
    run: object,
    lease: object,
    policy: object,
    fetcher: object,
    permit_id: object,
    admission_policy: object,
    network_profile_sha256: object,
    artifact_key: object,
    retain_until: object,
    clock: object,
) -> datetime:
    if admission_connection is ingest_connection:
        raise ValueError("Admission and ingest require separate database connections.")
    for connection in (admission_connection, ingest_connection):
        if (
            not isinstance(connection, Connection)
            or not connection.autocommit
            or connection.info.transaction_status != TransactionStatus.IDLE
        ):
            raise ValueError("Page attempts require idle autocommit database connections.")
    if not isinstance(store, EncryptedLocalArtifactStore):
        raise ValueError("A validated encrypted artifact store is required.")
    if not isinstance(run, CrawlRunOpened) or not isinstance(lease, CrawlFrontierLease):
        raise ValueError("Validated crawl run and frontier authority are required.")
    if (
        not all(
            isinstance(identifier, UUID)
            for identifier in (
                run.tenant_id,
                run.site_id,
                run.command_id,
                run.run_id,
                run.root_frontier_id,
                run.root_url_id,
                lease.tenant_id,
                lease.site_id,
                lease.run_id,
                lease.frontier_id,
                lease.url_id,
                lease.lease_id,
            )
        )
        or not isinstance(lease.lease_owner, str)
        or _WORKER.fullmatch(lease.lease_owner) is None
        or not _aware(lease.lease_until)
        or run.tenant_id != lease.tenant_id
        or run.site_id != lease.site_id
        or run.run_id != lease.run_id
        or run.status != "running"
    ):
        raise ValueError("Crawl run and frontier authority do not match.")
    if not isinstance(policy, CrawlScopePolicy) or policy != lease.policy:
        raise ValueError("The exact leased crawl policy is required.")
    if not callable(getattr(fetcher, "fetch", None)):
        raise ValueError("A crawl fetch boundary is required.")
    if not isinstance(permit_id, UUID):
        raise ValueError("A typed page-attempt permit identity is required.")
    if not isinstance(admission_policy, OriginAdmissionPolicy):
        raise ValueError("A validated origin admission policy is required.")
    if (
        not isinstance(network_profile_sha256, str)
        or _SHA256.fullmatch(network_profile_sha256) is None
    ):
        raise ValueError("A canonical network profile SHA-256 is required.")
    if not isinstance(artifact_key, ArtifactEncryptionKey):
        raise ValueError("Page fetches require an artifact encryption key.")
    if not callable(clock):
        raise ValueError("A wall clock is required.")
    now = _clock_value(clock)
    retention = _utc(retain_until, "artifact retention deadline")
    if retention <= now:
        raise ValueError("Artifact retention must extend beyond dispatch.")
    return now


def _valid_terminal(record: _AttemptRecord) -> bool:
    if (
        record.completion_kind
        not in {"success", "rate_limited", "service_unavailable", "transport_error", "cancelled"}
        or not _integer_between(record.observed_latency_ms, 0, 120_000)
        or record.backoff_basis
        not in {
            "none",
            "provider_seconds",
            "provider_date",
            "fallback_missing",
            "fallback_invalid",
            "capped",
            "local_persistence_failure",
        }
    ):
        return False
    try:
        OriginPermitCompletion(
            record.completion_kind, record.observed_latency_ms, record.retry_after_ms
        )
    except ValueError:
        return False
    if record.state == "observed" and record.completion_kind not in {
        "success",
        "rate_limited",
        "service_unavailable",
    }:
        return False
    if record.completion_kind in {"success", "transport_error", "cancelled"}:
        valid_basis = record.backoff_basis == "none"
    elif record.completion_kind == "rate_limited":
        valid_basis = record.backoff_basis in {
            "provider_seconds",
            "provider_date",
            "fallback_missing",
            "fallback_invalid",
            "capped",
        }
    else:
        valid_basis = record.backoff_basis in {
            "provider_seconds",
            "provider_date",
            "fallback_missing",
            "fallback_invalid",
            "capped",
        }
    expected_reason = {
        "transport_error": "transport_error",
        "cancelled": "policy_rejected",
    }.get(record.completion_kind)
    if record.state == "failed":
        if record.terminal_reason == "observation_persistence_failed":
            return (
                record.completion_kind == "service_unavailable"
                and record.retry_after_ms is None
                and record.backoff_basis == "local_persistence_failure"
            )
        return valid_basis and record.terminal_reason == expected_reason
    return valid_basis and record.terminal_reason is None


def _attempt_scope(lease: CrawlFrontierLease, permit_id: UUID) -> tuple[object, ...]:
    return (
        lease.tenant_id,
        lease.site_id,
        lease.run_id,
        lease.frontier_id,
        lease.lease_id,
        lease.lease_owner,
        permit_id,
    )


def _clock_value(clock: Callable[[], datetime]) -> datetime:
    return _utc(clock(), "page-attempt time")


def _wall_latency(started: datetime, finished: datetime) -> int:
    return _bounded_latency(math.ceil(max(0.0, (finished - started).total_seconds() * 1000)))


def _bounded_latency(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 120_000
    return min(120_000, max(0, value))


def _integer_between(value: object, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


def _utc(value: object, name: str) -> datetime:
    if not _aware(value):
        raise ValueError(f"A timezone-aware {name} is required.")
    return value.astimezone(UTC)


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


_SHA256 = re.compile(r"[0-9a-f]{64}")
_WORKER = re.compile(r"[a-z][a-z0-9_.:-]{0,127}")
