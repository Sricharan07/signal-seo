"""Durable crawl scope, URL inventory, and lease-safe frontier admission."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.crawl_urls import CrawlScopePolicy, CrawlUrl, normalize_crawl_url
from signal_core.database import _clean_transaction
from signal_core.workflow_contracts import (
    CrawlSiteWorkflowInput,
    validate_crawl_workflow_input,
)


class CrawlRunUnavailable(Exception):
    """The exact running workflow cannot open or expose crawl work."""


class CrawlRunConflict(Exception):
    """The workflow is already bound to different immutable crawl configuration."""


class CrawlFrontierUnavailable(Exception):
    """The current run, scope, source, or lease is unavailable."""


class CrawlFrontierRejected(Exception):
    """A discovered URL exceeds the immutable scope, depth, or count budget."""


@dataclass(frozen=True)
class CrawlRunLimits:
    schema_version: int = 1
    max_urls: int = 1000
    max_depth: int = 8
    max_attempts_per_url: int = 3
    max_total_bytes: int = 100 * 1024 * 1024
    max_duration_seconds: int = 3600

    def __post_init__(self) -> None:
        if (
            self.schema_version != 1
            or not _integer_between(self.max_urls, 1, 1_000_000)
            or not _integer_between(self.max_depth, 0, 32)
            or not _integer_between(self.max_attempts_per_url, 1, 10)
            or not _integer_between(self.max_total_bytes, 1024, 100 * 1024**3)
            or not _integer_between(self.max_duration_seconds, 1, 86_400)
        ):
            raise ValueError("Invalid crawl run limits.")


@dataclass(frozen=True, repr=False)
class CrawlRunOpened:
    tenant_id: UUID
    site_id: UUID
    command_id: UUID
    run_id: UUID
    root_frontier_id: UUID
    root_url_id: UUID
    first_run_id: str
    started_at: datetime
    status: str
    duplicate: bool


@dataclass(frozen=True, repr=False)
class CrawlFrontierQueued:
    run_id: UUID
    frontier_id: UUID
    url_id: UUID
    status: str
    next_attempt_at: datetime
    duplicate: bool


@dataclass(frozen=True, repr=False)
class CrawlFrontierLease:
    tenant_id: UUID
    site_id: UUID
    run_id: UUID
    frontier_id: UUID
    url_id: UUID
    url: CrawlUrl
    depth: int
    discovery_reason: str
    priority: int
    attempt_count: int
    lease_id: UUID
    lease_owner: str
    lease_until: datetime
    scope_version: int
    crawl_policy_version: int
    policy: CrawlScopePolicy
    limits: CrawlRunLimits
    fetch_profile_sha256: str
    duplicate: bool


@dataclass(frozen=True, repr=False)
class CrawlRecoveryLease:
    lease: CrawlFrontierLease
    egress_operation_id: UUID | None
    egress_robots_snapshot_id: UUID | None
    robots_dispatched: bool
    has_observation: bool


def open_crawl_run(
    connection: Connection,
    command: object,
    *,
    first_run_id: object,
    policy: object,
    limits: object,
    seed_url: object,
) -> CrawlRunOpened:
    """Bind one running workflow to immutable crawl policy and its root URL."""
    validated = validate_crawl_workflow_input(command)
    if not _canonical_uuid_text(first_run_id):
        raise ValueError("A canonical first execution run ID is required.")
    if not isinstance(policy, CrawlScopePolicy) or not isinstance(limits, CrawlRunLimits):
        raise ValueError("Validated crawl policy and run limits are required.")
    root = policy.admit(seed_url)
    scope_snapshot = _scope_snapshot(policy, root.fetch_url)
    limits_snapshot = _limits_snapshot(policy, limits)
    profile_hash = _fetch_profile_hash(policy)
    tenant_id = UUID(validated.tenant_id)
    site_id = UUID(validated.site_id)
    command_id = UUID(validated.command_id)
    run_id = _stable_uuid("run", validated.tenant_id, validated.command_id)
    root_url_id = _stable_uuid("url", validated.tenant_id, validated.site_id, root.normalized_key)
    root_frontier_id = _stable_uuid("frontier", str(run_id), root.normalized_key)

    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT run_id, root_frontier_id, root_url_id, started_at, run_status, "
            "duplicate, outcome FROM control.open_crawl_run(" + ", ".join(["%s"] * 17) + ")",
            (
                tenant_id,
                site_id,
                command_id,
                f"signal:CrawlSite:{tenant_id}:{command_id}",
                first_run_id,
                run_id,
                validated.scope_version,
                validated.crawl_policy_version,
                Jsonb(scope_snapshot),
                Jsonb(limits_snapshot),
                profile_hash,
                root_url_id,
                root_frontier_id,
                root.original_url,
                root.fetch_url,
                root.normalized_key,
                root.origin,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Crawl run admission returned no outcome.")
    if row[6] == "workflow_unavailable":
        raise CrawlRunUnavailable()
    if row[6] == "run_conflict":
        raise CrawlRunConflict()
    if row[6] != "opened":
        raise RuntimeError("Crawl run admission returned an invalid outcome.")
    opened = CrawlRunOpened(
        tenant_id=tenant_id,
        site_id=site_id,
        command_id=command_id,
        run_id=row[0],
        root_frontier_id=row[1],
        root_url_id=row[2],
        first_run_id=first_run_id,
        started_at=row[3],
        status=row[4],
        duplicate=row[5],
    )
    _validate_opened(opened, validated, run_id, root_frontier_id, root_url_id)
    return opened


def enqueue_crawl_url(
    connection: Connection,
    run: object,
    *,
    discovered_url: object,
    discovered_from_url_id: object,
    depth: object,
    discovery_reason: object,
    priority: object,
    delay_seconds: object = 0,
) -> CrawlFrontierQueued:
    """Record one in-scope discovery once under the run's immutable budgets."""
    _validate_run_handle(run)
    if not isinstance(discovered_from_url_id, UUID):
        raise ValueError("A typed discovery source URL is required.")
    if not _integer_between(depth, 0, 32):
        raise ValueError("Invalid crawl discovery depth.")
    if discovery_reason not in {"internal_link", "sitemap", "redirect"}:
        raise ValueError("Invalid crawl discovery reason.")
    if not _integer_between(priority, 0, 1000):
        raise ValueError("Invalid crawl discovery priority.")
    if not _integer_between(delay_seconds, 0, 86_400):
        raise ValueError("Invalid crawl discovery delay.")
    url = normalize_crawl_url(discovered_url)
    url_id = _stable_uuid("url", str(run.tenant_id), str(run.site_id), url.normalized_key)
    frontier_id = _stable_uuid("frontier", str(run.run_id), url.normalized_key)
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT frontier_id, url_id, frontier_status, next_attempt_at, duplicate, "
            "outcome FROM control.enqueue_crawl_url(" + ", ".join(["%s"] * 14) + ")",
            (
                run.tenant_id,
                run.site_id,
                run.run_id,
                url_id,
                frontier_id,
                discovered_from_url_id,
                depth,
                discovery_reason,
                priority,
                delay_seconds,
                url.original_url,
                url.fetch_url,
                url.normalized_key,
                url.origin,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Crawl frontier enqueue returned no outcome.")
    if row[5] in {"run_unavailable", "scope_unavailable", "source_unavailable"}:
        raise CrawlFrontierUnavailable()
    if row[5] in {"url_out_of_scope", "depth_exceeded", "budget_exhausted"}:
        raise CrawlFrontierRejected()
    if row[5] != "queued":
        raise RuntimeError("Crawl frontier enqueue returned an invalid outcome.")
    queued = CrawlFrontierQueued(
        run_id=run.run_id,
        frontier_id=row[0],
        url_id=row[1],
        status=row[2],
        next_attempt_at=row[3],
        duplicate=row[4],
    )
    if (
        queued.frontier_id != frontier_id
        or queued.url_id != url_id
        or queued.status != "pending"
        or not _aware(queued.next_attempt_at)
        or not isinstance(queued.duplicate, bool)
    ):
        raise RuntimeError("Crawl frontier enqueue returned invalid evidence.")
    return queued


def claim_crawl_frontier(
    connection: Connection,
    run: object,
    *,
    worker_key: object,
    lease_id: object,
    lease_seconds: object = 30,
) -> CrawlFrontierLease | None:
    """Claim at most one ready URL while preserving exact lease retry identity."""
    _validate_run_handle(run)
    if not isinstance(worker_key, str) or _WORKER_KEY.fullmatch(worker_key) is None:
        raise ValueError("Invalid crawl worker identity.")
    if not isinstance(lease_id, UUID):
        raise ValueError("A typed crawl lease identity is required.")
    if not _integer_between(lease_seconds, 1, 300):
        raise ValueError("Invalid crawl lease duration.")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT crawl_run_id, frontier_id, url_id, original_url, fetch_url, "
            "normalized_key, origin, depth, discovery_reason, priority, attempt_count, "
            "lease_id, lease_owner, lease_until, scope_version, crawl_policy_version, "
            "scope_snapshot, limits_snapshot, fetch_profile_hash, duplicate, outcome "
            "FROM control.claim_crawl_frontier(%s, %s, %s, %s, %s, %s)",
            (
                run.tenant_id,
                run.site_id,
                run.run_id,
                worker_key,
                lease_id,
                lease_seconds,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Crawl frontier claim returned no outcome.")
    if row[20] == "empty":
        return None
    if row[20] in {
        "run_unavailable",
        "scope_unavailable",
        "lease_unavailable",
        "run_budget_exhausted",
    }:
        raise CrawlFrontierUnavailable()
    if row[20] != "leased":
        raise RuntimeError("Crawl frontier claim returned an invalid outcome.")
    lease = _lease_from_row(row, tenant_id=run.tenant_id, site_id=run.site_id)
    expected_url_id = _stable_uuid(
        "url", str(run.tenant_id), str(run.site_id), lease.url.normalized_key
    )
    expected_frontier_id = _stable_uuid("frontier", str(run.run_id), lease.url.normalized_key)
    if (
        lease.run_id != run.run_id
        or lease.url_id != expected_url_id
        or lease.frontier_id != expected_frontier_id
        or lease.lease_id != lease_id
        or lease.lease_owner != worker_key
    ):
        raise RuntimeError("Crawl frontier claim returned mismatched evidence.")
    return lease


def list_crawl_recovery_leases(
    connection: Connection,
    run: CrawlRunOpened,
    *,
    worker_key: str,
) -> tuple[CrawlRecoveryLease, ...]:
    """Find durable work from an interrupted activity without creating a new lease."""
    _validate_run_handle(run)
    if not isinstance(worker_key, str) or _WORKER_KEY.fullmatch(worker_key) is None:
        raise ValueError("Invalid crawl worker identity.")
    with _clean_transaction(connection):
        rows = connection.execute(
            "SELECT * FROM control.list_crawl_recovery_leases(%s, %s, %s, %s)",
            (run.tenant_id, run.site_id, run.run_id, worker_key),
        ).fetchall()
    recovered: list[CrawlRecoveryLease] = []
    for row in rows:
        lease = _lease_from_row(row[:20], tenant_id=run.tenant_id, site_id=run.site_id)
        if (
            lease.run_id != run.run_id
            or not isinstance(row[22], bool)
            or not isinstance(row[23], bool)
        ):
            raise RuntimeError("Crawl recovery returned invalid evidence.")
        if any(value is not None and not isinstance(value, UUID) for value in row[20:22]):
            raise RuntimeError("Crawl recovery returned invalid operation identity.")
        recovered.append(CrawlRecoveryLease(lease, row[20], row[21], row[22], row[23]))
    return tuple(recovered)


def _lease_from_row(row: tuple, *, tenant_id: UUID, site_id: UUID) -> CrawlFrontierLease:
    observed = normalize_crawl_url(row[3])
    if (
        observed.fetch_url != row[4]
        or observed.normalized_key != row[5]
        or observed.origin != row[6]
    ):
        raise RuntimeError("Crawl frontier claim returned invalid URL evidence.")
    scope = row[16]
    limit_values = row[17]
    try:
        policy = CrawlScopePolicy(
            schema_version=scope["schema_version"],
            allowed_origins=tuple(scope["allowed_origins"]),
            user_agent=scope["user_agent"],
            max_redirects=limit_values["max_redirects"],
            max_body_bytes=limit_values["max_body_bytes"],
            request_timeout_seconds=limit_values["request_timeout_ms"] / 1000,
            total_timeout_seconds=limit_values["total_timeout_ms"] / 1000,
        )
        limits = CrawlRunLimits(
            schema_version=limit_values["schema_version"],
            max_urls=limit_values["max_urls"],
            max_depth=limit_values["max_depth"],
            max_attempts_per_url=limit_values["max_attempts_per_url"],
            max_total_bytes=limit_values["max_total_bytes"],
            max_duration_seconds=limit_values["max_duration_seconds"],
        )
    except (IndexError, KeyError, TypeError, ValueError):
        raise RuntimeError("Crawl frontier claim returned invalid policy evidence.") from None
    if (
        scope != _scope_snapshot(policy, scope["seed_urls"][0])
        or limit_values != _limits_snapshot(policy, limits)
        or not isinstance(row[18], bytes)
        or row[18] != _fetch_profile_hash(policy)
    ):
        raise RuntimeError("Crawl frontier claim returned invalid policy evidence.")
    lease = CrawlFrontierLease(
        tenant_id=tenant_id,
        site_id=site_id,
        run_id=row[0],
        frontier_id=row[1],
        url_id=row[2],
        url=observed,
        depth=row[7],
        discovery_reason=row[8],
        priority=row[9],
        attempt_count=row[10],
        lease_id=row[11],
        lease_owner=row[12],
        lease_until=row[13],
        scope_version=row[14],
        crawl_policy_version=row[15],
        policy=policy,
        limits=limits,
        fetch_profile_sha256=row[18].hex(),
        duplicate=row[19],
    )
    if (
        not all(
            isinstance(value, UUID)
            for value in (
                lease.tenant_id,
                lease.site_id,
                lease.run_id,
                lease.frontier_id,
                lease.url_id,
            )
        )
        or not _integer_between(lease.depth, 0, 32)
        or lease.discovery_reason not in {"root", "internal_link", "sitemap", "redirect"}
        or not _integer_between(lease.priority, 0, 1000)
        or not _integer_between(lease.attempt_count, 1, 10)
        or not isinstance(lease.lease_id, UUID)
        or not _aware(lease.lease_until)
        or not isinstance(lease.duplicate, bool)
        or not _integer_between(lease.scope_version, 1, 2_147_483_647)
        or not _integer_between(lease.crawl_policy_version, 1, 2_147_483_647)
    ):
        raise RuntimeError("Crawl frontier claim returned invalid evidence.")
    return lease


def _scope_snapshot(policy: CrawlScopePolicy, seed_fetch_url: str) -> dict[str, object]:
    return {
        "allowed_origins": sorted(policy.allowed_origins),
        "authorized_content_classes": ["public_html"],
        "normalization_version": 1,
        "purpose": "connected_site_audit",
        "schema_version": 1,
        "seed_urls": [seed_fetch_url],
        "user_agent": policy.user_agent,
    }


def _limits_snapshot(policy: CrawlScopePolicy, limits: CrawlRunLimits) -> dict[str, int]:
    request_ms = _exact_milliseconds(policy.request_timeout_seconds)
    total_ms = _exact_milliseconds(policy.total_timeout_seconds)
    if limits.max_total_bytes < policy.max_body_bytes:
        raise ValueError("The total crawl byte budget cannot be smaller than one body limit.")
    return {
        "max_attempts_per_url": limits.max_attempts_per_url,
        "max_body_bytes": policy.max_body_bytes,
        "max_depth": limits.max_depth,
        "max_duration_seconds": limits.max_duration_seconds,
        "max_redirects": policy.max_redirects,
        "max_total_bytes": limits.max_total_bytes,
        "max_urls": limits.max_urls,
        "request_timeout_ms": request_ms,
        "schema_version": 1,
        "total_timeout_ms": total_ms,
    }


def _fetch_profile_hash(policy: CrawlScopePolicy) -> bytes:
    profile = {
        "max_body_bytes": policy.max_body_bytes,
        "max_redirects": policy.max_redirects,
        "request_timeout_ms": _exact_milliseconds(policy.request_timeout_seconds),
        "schema_version": 1,
        "total_timeout_ms": _exact_milliseconds(policy.total_timeout_seconds),
        "user_agent": policy.user_agent,
    }
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(canonical).digest()


def _exact_milliseconds(value: float) -> int:
    milliseconds = float(value) * 1000
    if not milliseconds.is_integer():
        raise ValueError("Durable crawl timeouts require exact millisecond precision.")
    return int(milliseconds)


def _stable_uuid(kind: str, *parts: str) -> UUID:
    return uuid5(_CRAWL_ID_NAMESPACE, ":".join((kind, *parts)))


def _validate_opened(
    opened: CrawlRunOpened,
    command: CrawlSiteWorkflowInput,
    expected_run_id: UUID,
    expected_frontier_id: UUID,
    expected_url_id: UUID,
) -> None:
    if (
        opened.tenant_id != UUID(command.tenant_id)
        or opened.site_id != UUID(command.site_id)
        or opened.command_id != UUID(command.command_id)
        or opened.run_id != expected_run_id
        or opened.root_frontier_id != expected_frontier_id
        or opened.root_url_id != expected_url_id
        or not _canonical_uuid_text(opened.first_run_id)
        or not _aware(opened.started_at)
        or opened.status != "running"
        or not isinstance(opened.duplicate, bool)
    ):
        raise RuntimeError("Crawl run admission returned invalid evidence.")


def _validate_run_handle(value: object) -> None:
    if (
        not isinstance(value, CrawlRunOpened)
        or not all(
            isinstance(identifier, UUID)
            for identifier in (
                value.tenant_id,
                value.site_id,
                value.command_id,
                value.run_id,
                value.root_frontier_id,
                value.root_url_id,
            )
        )
        or not _canonical_uuid_text(value.first_run_id)
        or not _aware(value.started_at)
        or value.status != "running"
        or not isinstance(value.duplicate, bool)
    ):
        raise ValueError("A validated crawl run handle is required.")


def _canonical_uuid_text(value: object) -> bool:
    if not isinstance(value, str) or _UUID.fullmatch(value) is None:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _integer_between(value: object, minimum: int, maximum: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


_CRAWL_ID_NAMESPACE = UUID("f54a94cb-5c97-4cba-9d03-3337cb61f02a")
_WORKER_KEY = re.compile(r"[a-z][a-z0-9_.:-]{0,127}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
