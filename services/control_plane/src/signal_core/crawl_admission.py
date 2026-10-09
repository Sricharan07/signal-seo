"""Global origin-wide crawl admission with expiring, retry-safe permits."""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection

from signal_core.crawl_frontier import CrawlFrontierLease
from signal_core.crawl_urls import CrawlUrl
from signal_core.database import _clean_transaction


class OriginPermitUnavailable(Exception):
    """The current frontier lease cannot receive global network authority."""


class OriginPermitConflict(Exception):
    """A permit identity was already bound to different immutable input."""


class OriginPermitExpired(Exception):
    """The global permit expired before its completion could be recorded."""


@dataclass(frozen=True)
class OriginAdmissionPolicy:
    schema_version: int = 1
    profile_version: int = 1
    min_delay_ms: int = 1000
    permit_lease_seconds: int = 30

    def __post_init__(self) -> None:
        if (
            not _integer_between(self.schema_version, 1, 1)
            or not _integer_between(self.profile_version, 1, 1)
            or not _integer_between(self.min_delay_ms, 1000, 60_000)
            or not _integer_between(self.permit_lease_seconds, 1, 120)
        ):
            raise ValueError("Invalid origin admission policy.")


@dataclass(frozen=True)
class OriginPermitCompletion:
    kind: str
    observed_latency_ms: int
    retry_after_ms: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, str)
            or self.kind
            not in {
                "success",
                "rate_limited",
                "service_unavailable",
                "transport_error",
                "cancelled",
            }
            or not _integer_between(self.observed_latency_ms, 0, 120_000)
        ):
            raise ValueError("Invalid origin permit completion.")
        if self.kind == "rate_limited":
            if not _integer_between(self.retry_after_ms, 1000, 86_400_000):
                raise ValueError("Rate-limited completion requires bounded Retry-After.")
        elif self.kind == "service_unavailable":
            if self.retry_after_ms is not None and not _integer_between(
                self.retry_after_ms, 1000, 86_400_000
            ):
                raise ValueError("Invalid service-unavailable Retry-After.")
        elif self.retry_after_ms is not None:
            raise ValueError("Retry-After is not valid for this completion kind.")


@dataclass(frozen=True, repr=False)
class OriginPermitGrant:
    tenant_id: UUID
    site_id: UUID
    crawl_run_id: UUID
    frontier_id: UUID
    frontier_lease_id: UUID
    worker_key: str
    permit_id: UUID
    bucket_id: UUID
    origin: str
    profile_version: int
    workload_id: str
    permit_kind: str
    min_delay_ms: int
    requested_lease_seconds: int
    issued_at: datetime
    expires_at: datetime
    authority_fingerprint_sha256: str
    duplicate: bool


@dataclass(frozen=True, repr=False)
class OriginPermitReplay:
    permit_id: UUID
    bucket_id: UUID
    origin: str
    profile_version: int
    workload_id: str
    permit_kind: str
    issued_at: datetime
    expires_at: datetime
    released_at: datetime
    completion_kind: str
    observed_latency_ms: int | None
    retry_after_ms: int | None
    next_available_at: datetime


@dataclass(frozen=True, repr=False)
class OriginPermitDeferred:
    bucket_id: UUID
    origin: str
    profile_version: int
    permit_kind: str
    reason: str
    retry_at: datetime
    active_in_flight: int


@dataclass(frozen=True, repr=False)
class OriginPermitFinished:
    permit_id: UUID
    completion_kind: str
    finished_at: datetime
    next_allowed_at: datetime
    degraded_until: datetime | None
    active_in_flight: int
    duplicate: bool


@dataclass(frozen=True, repr=False)
class OriginPermitReconciliation:
    reconciled_count: int
    observed_at: datetime


def acquire_origin_permit(
    connection: Connection,
    frontier: object,
    *,
    permit_id: object,
    permit_kind: object,
    policy: object,
) -> OriginPermitGrant | OriginPermitReplay | OriginPermitDeferred:
    """Acquire one globally shared request permit for an active frontier lease."""
    _validate_frontier(frontier)
    if not isinstance(permit_id, UUID):
        raise ValueError("A typed origin permit identity is required.")
    if not isinstance(permit_kind, str) or permit_kind not in _PERMIT_KINDS:
        raise ValueError("Invalid origin permit kind.")
    if not isinstance(policy, OriginAdmissionPolicy):
        raise ValueError("A validated origin admission policy is required.")
    workload_id = _workload_id(frontier)
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT permit_id, bucket_id, admitted_origin, profile_version, workload_id, "
            "permit_kind, min_delay_ms, requested_lease_seconds, issued_at, expires_at, "
            "released_at, completion_kind, observed_latency_ms, retry_after_ms, "
            "authority_fingerprint, available_at, active_in_flight, duplicate, outcome "
            "FROM control.acquire_origin_permit(" + ", ".join(["%s"] * 13) + ")",
            (
                frontier.tenant_id,
                frontier.site_id,
                frontier.run_id,
                frontier.frontier_id,
                frontier.lease_id,
                frontier.lease_owner,
                permit_id,
                frontier.url.origin,
                policy.profile_version,
                workload_id,
                permit_kind,
                policy.min_delay_ms,
                policy.permit_lease_seconds,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Origin admission returned no outcome.")
    outcome = row[18]
    if not isinstance(outcome, str):
        raise RuntimeError("Origin admission returned an invalid outcome.")
    if outcome == "frontier_unavailable":
        raise OriginPermitUnavailable()
    if outcome == "permit_conflict":
        raise OriginPermitConflict()
    if outcome.startswith("deferred_"):
        return _deferred_from_row(row, frontier.url.origin, permit_kind, policy)
    if outcome == "replayed":
        return _replay_from_row(row, frontier, permit_id, permit_kind, policy, workload_id)
    if outcome != "admitted":
        raise RuntimeError("Origin admission returned an invalid outcome.")
    return _grant_from_row(row, frontier, permit_id, permit_kind, policy, workload_id)


def finish_origin_permit(
    connection: Connection,
    grant: object,
    completion: object,
) -> OriginPermitFinished:
    """Release one exact permit and apply any origin-wide adaptive backoff."""
    _validate_grant(grant)
    if not isinstance(completion, OriginPermitCompletion):
        raise ValueError("A validated origin permit completion is required.")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT finished_at, next_allowed_at, degraded_until, active_in_flight, "
            "duplicate, outcome FROM control.finish_origin_permit(" + ", ".join(["%s"] * 17) + ")",
            (
                grant.tenant_id,
                grant.site_id,
                grant.crawl_run_id,
                grant.frontier_id,
                grant.frontier_lease_id,
                grant.worker_key,
                grant.permit_id,
                grant.bucket_id,
                grant.origin,
                grant.profile_version,
                grant.workload_id,
                grant.permit_kind,
                grant.issued_at,
                grant.expires_at,
                completion.kind,
                completion.observed_latency_ms,
                completion.retry_after_ms,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Origin permit completion returned no outcome.")
    if row[5] == "permit_conflict":
        raise OriginPermitConflict()
    if row[5] == "permit_expired":
        raise OriginPermitExpired()
    if row[5] != "finished":
        raise RuntimeError("Origin permit completion returned an invalid outcome.")
    finished = OriginPermitFinished(
        permit_id=grant.permit_id,
        completion_kind=completion.kind,
        finished_at=row[0],
        next_allowed_at=row[1],
        degraded_until=row[2],
        active_in_flight=row[3],
        duplicate=row[4],
    )
    if (
        not _aware(finished.finished_at)
        or not _aware(finished.next_allowed_at)
        or (finished.degraded_until is not None and not _aware(finished.degraded_until))
        or not _integer_between(finished.active_in_flight, 0, 1)
        or not isinstance(finished.duplicate, bool)
    ):
        raise RuntimeError("Origin permit completion returned invalid evidence.")
    return finished


def reconcile_expired_origin_permits(
    connection: Connection, *, limit: object = 100
) -> OriginPermitReconciliation:
    """Release a bounded batch of permits abandoned by failed workers."""
    if not _integer_between(limit, 1, 1000):
        raise ValueError("Invalid origin permit reconciliation limit.")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT reconciled_count, observed_at "
            "FROM control.reconcile_expired_origin_permits(%s)",
            (limit,),
        ).fetchone()
    if row is None or not _integer_between(row[0], 0, limit) or not _aware(row[1]):
        raise RuntimeError("Origin permit reconciliation returned invalid evidence.")
    return OriginPermitReconciliation(reconciled_count=row[0], observed_at=row[1])


def _grant_from_row(
    row: tuple,
    frontier: CrawlFrontierLease,
    permit_id: UUID,
    permit_kind: str,
    policy: OriginAdmissionPolicy,
    workload_id: str,
) -> OriginPermitGrant:
    expected_fingerprint = _authority_fingerprint(frontier)
    grant = OriginPermitGrant(
        tenant_id=frontier.tenant_id,
        site_id=frontier.site_id,
        crawl_run_id=frontier.run_id,
        frontier_id=frontier.frontier_id,
        frontier_lease_id=frontier.lease_id,
        worker_key=frontier.lease_owner,
        permit_id=row[0],
        bucket_id=row[1],
        origin=row[2],
        profile_version=row[3],
        workload_id=row[4],
        permit_kind=row[5],
        min_delay_ms=row[6],
        requested_lease_seconds=row[7],
        issued_at=row[8],
        expires_at=row[9],
        authority_fingerprint_sha256=row[14].hex() if isinstance(row[14], bytes) else "",
        duplicate=row[17],
    )
    if (
        grant.permit_id != permit_id
        or not isinstance(grant.bucket_id, UUID)
        or grant.origin != frontier.url.origin
        or grant.profile_version != policy.profile_version
        or grant.workload_id != workload_id
        or grant.permit_kind != permit_kind
        or grant.min_delay_ms != policy.min_delay_ms
        or grant.requested_lease_seconds != policy.permit_lease_seconds
        or not _aware(grant.issued_at)
        or not _aware(grant.expires_at)
        or grant.expires_at <= grant.issued_at
        or grant.expires_at > frontier.lease_until
        or grant.authority_fingerprint_sha256 != expected_fingerprint.hex()
        or row[10] is not None
        or row[11] is not None
        or row[12] is not None
        or row[13] is not None
        or not isinstance(grant.duplicate, bool)
        or row[16] != 1
    ):
        raise RuntimeError("Origin admission returned invalid permit evidence.")
    return grant


def _replay_from_row(
    row: tuple,
    frontier: CrawlFrontierLease,
    permit_id: UUID,
    permit_kind: str,
    policy: OriginAdmissionPolicy,
    workload_id: str,
) -> OriginPermitReplay:
    replay = OriginPermitReplay(
        permit_id=row[0],
        bucket_id=row[1],
        origin=row[2],
        profile_version=row[3],
        workload_id=row[4],
        permit_kind=row[5],
        issued_at=row[8],
        expires_at=row[9],
        released_at=row[10],
        completion_kind=row[11],
        observed_latency_ms=row[12],
        retry_after_ms=row[13],
        next_available_at=row[15],
    )
    if (
        replay.permit_id != permit_id
        or not isinstance(replay.bucket_id, UUID)
        or replay.origin != frontier.url.origin
        or replay.profile_version != policy.profile_version
        or replay.workload_id != workload_id
        or replay.permit_kind != permit_kind
        or row[6] != policy.min_delay_ms
        or row[7] != policy.permit_lease_seconds
        or not _aware(replay.issued_at)
        or not _aware(replay.expires_at)
        or not _aware(replay.released_at)
        or replay.completion_kind not in _TERMINAL_COMPLETIONS
        or not _aware(replay.next_available_at)
        or not isinstance(row[14], bytes)
        or row[14] != _authority_fingerprint(frontier)
        or row[17] is not True
    ):
        raise RuntimeError("Origin admission returned invalid replay evidence.")
    return replay


def _deferred_from_row(
    row: tuple,
    origin: str,
    permit_kind: str,
    policy: OriginAdmissionPolicy,
) -> OriginPermitDeferred:
    reason = row[18].removeprefix("deferred_")
    deferred = OriginPermitDeferred(
        bucket_id=row[1],
        origin=row[2],
        profile_version=row[3],
        permit_kind=row[5],
        reason=reason,
        retry_at=row[15],
        active_in_flight=row[16],
    )
    if (
        not isinstance(deferred.bucket_id, UUID)
        or deferred.origin != origin
        or deferred.profile_version != policy.profile_version
        or deferred.permit_kind != permit_kind
        or deferred.reason not in {"backoff", "in_flight", "politeness"}
        or not _aware(deferred.retry_at)
        or not _integer_between(deferred.active_in_flight, 0, 1)
        or row[0] is not None
        or row[17] is not False
    ):
        raise RuntimeError("Origin admission returned invalid deferral evidence.")
    return deferred


def _validate_frontier(value: object) -> None:
    if (
        not isinstance(value, CrawlFrontierLease)
        or not all(
            isinstance(identifier, UUID)
            for identifier in (
                value.tenant_id,
                value.site_id,
                value.run_id,
                value.frontier_id,
                value.lease_id,
            )
        )
        or not isinstance(value.lease_owner, str)
        or _WORKER_KEY.fullmatch(value.lease_owner) is None
        or not isinstance(value.url, CrawlUrl)
        or not _aware(value.lease_until)
    ):
        raise ValueError("A validated crawl frontier lease is required.")


def _validate_grant(value: object) -> None:
    if (
        not isinstance(value, OriginPermitGrant)
        or not all(
            isinstance(identifier, UUID)
            for identifier in (
                value.tenant_id,
                value.site_id,
                value.crawl_run_id,
                value.frontier_id,
                value.frontier_lease_id,
                value.permit_id,
                value.bucket_id,
            )
        )
        or not _integer_between(value.profile_version, 1, 1)
        or not isinstance(value.worker_key, str)
        or _WORKER_KEY.fullmatch(value.worker_key) is None
        or not isinstance(value.origin, str)
        or not isinstance(value.workload_id, str)
        or not isinstance(value.permit_kind, str)
        or value.permit_kind not in _PERMIT_KINDS
        or value.workload_id != f"crawl:{value.crawl_run_id}:{value.frontier_lease_id}"
        or not _integer_between(value.min_delay_ms, 1000, 60_000)
        or not _integer_between(value.requested_lease_seconds, 1, 120)
        or not _aware(value.issued_at)
        or not _aware(value.expires_at)
        or value.expires_at <= value.issued_at
        or not isinstance(value.authority_fingerprint_sha256, str)
        or _SHA256_HEX.fullmatch(value.authority_fingerprint_sha256) is None
        or value.authority_fingerprint_sha256 != _grant_authority_fingerprint(value).hex()
        or not isinstance(value.duplicate, bool)
    ):
        raise ValueError("A validated active origin permit is required.")


def _workload_id(frontier: CrawlFrontierLease) -> str:
    return f"crawl:{frontier.run_id}:{frontier.lease_id}"


def _authority_fingerprint(frontier: CrawlFrontierLease) -> bytes:
    value = ":".join(
        str(part)
        for part in (
            frontier.tenant_id,
            frontier.site_id,
            frontier.run_id,
            frontier.frontier_id,
            frontier.lease_id,
            frontier.lease_owner,
        )
    )
    return hashlib.sha256(value.encode("utf-8")).digest()


def _grant_authority_fingerprint(grant: OriginPermitGrant) -> bytes:
    value = ":".join(
        str(part)
        for part in (
            grant.tenant_id,
            grant.site_id,
            grant.crawl_run_id,
            grant.frontier_id,
            grant.frontier_lease_id,
            grant.worker_key,
        )
    )
    return hashlib.sha256(value.encode("utf-8")).digest()


def _integer_between(value: object, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


_PERMIT_KINDS = frozenset({"robots", "html_navigation"})
_WORKER_KEY = re.compile(r"[a-z][a-z0-9_.:-]{0,127}")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_TERMINAL_COMPLETIONS = frozenset(
    {
        "success",
        "rate_limited",
        "service_unavailable",
        "transport_error",
        "cancelled",
        "lease_expired",
    }
)
