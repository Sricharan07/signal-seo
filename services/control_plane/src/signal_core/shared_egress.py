"""Durable shared egress composition for crawler, browser, and connector traffic."""

import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from uuid import UUID, uuid5

import rfc8785
from psycopg import Connection
from psycopg import Error as DatabaseError
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from signal_core.crawl_admission import (
    OriginAdmissionPolicy,
    OriginPermitCompletion,
    OriginPermitDeferred,
    OriginPermitExpired,
    OriginPermitGrant,
    OriginPermitReplay,
    acquire_origin_permit,
    finish_origin_permit,
)
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_frontier import CrawlFrontierLease, CrawlRunOpened
from signal_core.crawl_http import (
    CrawlFetchRejected,
    CrawlFetchUnavailable,
    EgressHttpRequest,
    EgressHttpResult,
    RobotsFetchResult,
)
from signal_core.crawl_robots import (
    ROBOTS_MAX_BODY_BYTES,
    RobotsAccessDecision,
    RobotsSnapshotRecorded,
    RobotsSnapshotUnavailable,
    authorize_from_current_snapshot,
    persist_robots_snapshot,
)
from signal_core.crawl_urls import CrawlScopePolicy, CrawlUrl, validate_public_addresses
from signal_core.database import _clean_transaction
from signal_core.egress_profiles import (
    PROFILE_RULES,
    BingPageReadScope,
    DataForSeoCredentialScope,
    DrivePickedScope,
    EgressProfile,
    Ga4ReadScope,
    GitHubRepositoryWriteScope,
    PageSpeedScope,
    WebflowScope,
    WordPressScope,
    profile_headers,
    validate_profile_request,
    validate_provider_context,
)
from signal_core.email_messages import render_email
from signal_core.indexnow_protocol import IndexNowSubmitScope
from signal_core.owner_connector_egress import (
    OwnerConnectorContext,
    WeeklyDataForSeoContext,
    WeeklyPageSpeedContext,
    execute_owner_connector_request,
    execute_weekly_dataforseo_request,
    execute_weekly_pagespeed_request,
)
from signal_core.smtp_submission import (
    OpenBaoSmtpCredential,
    PinnedSmtpSubmitter,
    SmtpConfiguration,
)


class SharedEgressUnavailable(RuntimeError):
    """Current authority or evidence cannot support this outbound request."""


class SharedEgressConflict(RuntimeError):
    """An operation identity is already bound to different immutable facts."""


@dataclass(frozen=True, repr=False)
class SharedSmtpEgress:
    """The SMTP-only path shares admission buckets, pinning, and intent-before-I/O."""

    connection: Connection
    configuration: SmtpConfiguration
    credentials: OpenBaoSmtpCredential
    submitter: PinnedSmtpSubmitter

    async def deliver_due(
        self, generation: str, *, limit: int = 20, **secret_options
    ) -> tuple[str, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Email dispatch batch must be bounded.")
        with _clean_transaction(self.connection):
            rows = self.connection.execute(
                "SELECT control.email_due_outbox(%s)", (limit,)
            ).fetchall()
        return tuple([await self.deliver(row[0], generation, **secret_options) for row in rows])

    async def deliver(self, outbox_id: UUID, generation: str, **secret_options) -> str:
        with _clean_transaction(self.connection):
            row = self.connection.execute(
                "SELECT control.email_outbox_item(%s,%s)", (outbox_id, generation)
            ).fetchone()
        if not row or row[0] is None:
            raise SharedEgressUnavailable("Email outbox item is unavailable.")
        item = row[0]
        if item["state"] not in {"queued", "retry"}:
            return item["state"]
        configured = item["configuration_sha256"] == self.configuration.sha256.hex()
        if not configured:
            with _clean_transaction(self.connection):
                return self.connection.execute(
                    "SELECT control.suppress_email_outbox(%s,%s)", (outbox_id, "stale_binding")
                ).fetchone()[0]
        try:
            rendered = render_email(
                identifier=outbox_id,
                site_id=UUID(item["site_id"]),
                sender=self.configuration.sender,
                recipient=item["address"],
                origin=self.configuration.dashboard_origin,
                category=item["category"],
                projection=item["projection"],
            )
        except (ValueError, TypeError, KeyError):
            # Suppression still needs durable evidence when no recipient can be rendered.
            with _clean_transaction(self.connection):
                return self.connection.execute(
                    "SELECT control.suppress_email_outbox(%s,%s)",
                    (outbox_id, "recipient_unavailable"),
                ).fetchone()[0]
        try:
            credential = await self.credentials.read(**secret_options)
        except Exception:
            with _clean_transaction(self.connection):
                return self.connection.execute(
                    "SELECT control.suppress_email_outbox(%s,%s)",
                    (outbox_id, "provider_unavailable"),
                ).fetchone()[0]
        with _clean_transaction(self.connection):
            state = self.connection.execute(
                "SELECT control.begin_email_dispatch(%s,%s,%s,%s)",
                (outbox_id, generation, rendered.sha256, len(rendered.body)),
            ).fetchone()[0]
        if state != "claimed":
            return state
        try:
            outcome = self.submitter.submit(
                self.configuration, credential, item["address"], rendered.body
            )
        except Exception:
            outcome = "unknown"
        with _clean_transaction(self.connection):
            return self.connection.execute(
                "SELECT control.finish_email_dispatch(%s,%s,%s)",
                (outbox_id, rendered.sha256, outcome),
            ).fetchone()[0]


class SharedEgressPermitExpired(RuntimeError):
    """Network completion arrived after its bounded request permit expired."""


@dataclass(frozen=True, repr=False)
class SharedRobotsPending:
    dispatch_id: UUID
    reason: str


@dataclass(frozen=True, repr=False)
class SharedRobotsDeferred:
    retry_at: datetime
    reason: str


@dataclass(frozen=True, repr=False)
class SharedRobotsPrepared:
    snapshot: RobotsSnapshotRecorded | None
    decision: RobotsAccessDecision


class ProviderEgressUnavailable(RuntimeError):
    """The shared boundary could not return a trusted provider response."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class ProviderEgressResponse:
    status_code: int
    media_type: str
    body: bytes

    def __post_init__(self) -> None:
        if not _integer_between(self.status_code, 100, 599):
            raise ValueError("Provider egress response status is invalid.")
        if not isinstance(self.media_type, str) or not self.media_type:
            raise ValueError("Provider egress response media type is invalid.")
        if not isinstance(self.body, bytes):
            raise ValueError("Provider egress response body is invalid.")


@dataclass(frozen=True, repr=False)
class SharedEgressRequest:
    purpose: str
    http: EgressHttpRequest
    profile: EgressProfile
    sensitive_body: bool = False
    github_write_scope: GitHubRepositoryWriteScope | None = None
    dataforseo_scope: DataForSeoCredentialScope | None = None
    indexnow_scope: IndexNowSubmitScope | None = None
    ga4_scope: Ga4ReadScope | None = None
    wordpress_scope: WordPressScope | None = None
    drive_picked_scope: DrivePickedScope | None = None
    webflow_scope: WebflowScope | None = None

    bing_page_scope: BingPageReadScope | None = None

    pagespeed_scope: PageSpeedScope | None = None

    def __post_init__(self) -> None:
        if self.purpose not in {"crawl", "browser", "connector", "model"}:
            raise ValueError("Shared egress purpose is invalid.")
        if not isinstance(self.http, EgressHttpRequest):
            raise ValueError("Shared egress requires a validated HTTP request.")
        if not isinstance(self.sensitive_body, bool):
            raise ValueError("Shared egress body sensitivity is invalid.")
        validate_profile_request(
            self.profile,
            self.purpose,
            self.http,
            self.sensitive_body,
            self.github_write_scope,
            self.dataforseo_scope,
            self.indexnow_scope,
            self.ga4_scope,
            self.wordpress_scope,
            self.drive_picked_scope,
            self.webflow_scope,
            self.bing_page_scope,
            self.pagespeed_scope,
        )

    @property
    def credentialed(self) -> bool:
        headers = dict(self.http.headers)
        return (
            self.sensitive_body
            or "authorization" in headers
            or "x-goog-api-key" in headers
            or (self.pagespeed_scope is not None and self.pagespeed_scope.api_key is not None)
        )

    @property
    def evidence_url(self) -> str:
        projection = PROFILE_RULES[self.profile].evidence_projection
        return projection(self.http.url) if projection else self.http.url

    @property
    def body_sha256(self) -> bytes | None:
        if self.http.method != "POST":
            return None
        return hashlib.sha256(self.http.body).digest()

    @property
    def request_sha256(self) -> bytes:
        headers = {
            name: "present" if name in {"authorization", "x-goog-api-key"} else value
            for name, value in self.http.headers
        }
        material = {
            "schema_version": 1,
            "purpose": self.purpose,
            "profile": self.profile.value,
            "method": self.http.method,
            "url": self.evidence_url,
            "headers": headers,
            "body_sha256": self.body_sha256.hex() if self.body_sha256 else None,
            "sensitive_body": self.sensitive_body,
            "max_response_bytes": self.http.max_response_bytes,
            "accepted_media_types": list(self.http.accepted_media_types),
            "timeout_seconds": self.http.timeout_seconds,
        }
        if self.pagespeed_scope is not None:
            material["query_credential_present"] = self.pagespeed_scope.api_key is not None
        if self.github_write_scope is not None:
            material["github_repository"] = self.github_write_scope.full_name
        if self.dataforseo_scope is not None:
            material["dataforseo_generation"] = self.dataforseo_scope.generation
        if self.ga4_scope is not None:
            material["ga4_property"] = self.ga4_scope.property_resource_name
        if self.http.telegram_credential is not None:
            material["telegram_credential_sha256"] = hashlib.sha256(
                self.http.telegram_credential.token.encode("ascii")
            ).hexdigest()
        if self.webflow_scope is not None:
            material["webflow_site"] = self.webflow_scope.site_id
            material["webflow_collection"] = self.webflow_scope.collection_id
        return hashlib.sha256(rfc8785.dumps(material)).digest()


@dataclass(frozen=True, repr=False)
class SharedEgressDispatch:
    operation_id: UUID
    tenant_id: UUID
    site_id: UUID
    crawl_run_id: UUID
    worker_key: str
    bucket_id: UUID
    origin: str
    egress_profile: EgressProfile
    profile_version: int
    workload_id: str
    min_delay_ms: int
    requested_lease_seconds: int
    issued_at: datetime
    expires_at: datetime
    authority_fingerprint_sha256: str
    request_sha256: str
    robots_snapshot_id: UUID
    robots_reason: str


@dataclass(frozen=True, repr=False)
class SharedEgressDeferred:
    origin: str
    reason: str
    retry_at: datetime
    active_in_flight: int


@dataclass(frozen=True, repr=False)
class SharedEgressPending:
    operation_id: UUID
    reason: str


@dataclass(frozen=True, repr=False)
class SharedEgressBlocked:
    robots_snapshot_id: UUID
    reason: str
    expires_at: datetime


@dataclass(frozen=True, repr=False)
class SharedEgressReceipt:
    operation_id: UUID
    egress_profile: EgressProfile
    state: str
    finished_at: datetime
    network_outcome: str
    completion_kind: str
    next_allowed_at: datetime
    degraded_until: datetime | None
    active_in_flight: int
    response: EgressHttpResult
    duplicate: bool


@dataclass(frozen=True, repr=False)
class SharedEgressProvider:
    """Send bounded provider requests through one durable shared-egress authority."""

    admission_connection: Connection
    ingest_connection: Connection
    store: EncryptedLocalArtifactStore
    run: CrawlRunOpened | OwnerConnectorContext | WeeklyPageSpeedContext
    policy: CrawlScopePolicy
    fetcher: object
    worker_key: str
    admission_policy: OriginAdmissionPolicy
    artifact_key: ArtifactEncryptionKey | None
    purpose: str = "model"

    def __post_init__(self) -> None:
        if self.purpose not in {"connector", "model"}:
            raise ValueError("Provider egress purpose must be connector or model.")
        if _WORKER.fullmatch(self.worker_key) is None:
            raise ValueError("Provider egress worker identity is invalid.")

    def post_json(
        self,
        *,
        url: str,
        authorization: str,
        body: bytes,
        operation_id: UUID,
        timeout_seconds: float,
        max_response_bytes: int,
        profile: EgressProfile = EgressProfile.OPENAI_MODEL,
    ) -> ProviderEgressResponse:
        """Return a bounded body only after durable admission and completion evidence."""
        return self.request_json(
            method="POST",
            url=url,
            profile=profile,
            authorization=authorization,
            body=body,
            operation_id=operation_id,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )

    def request_json(
        self,
        *,
        method: str,
        url: str,
        profile: EgressProfile,
        github_write_scope: GitHubRepositoryWriteScope | None = None,
        dataforseo_scope: DataForSeoCredentialScope | None = None,
        indexnow_scope: IndexNowSubmitScope | None = None,
        ga4_scope: Ga4ReadScope | None = None,
        wordpress_scope: WordPressScope | None = None,
        drive_picked_scope: DrivePickedScope | None = None,
        webflow_scope: WebflowScope | None = None,
        bing_page_scope: BingPageReadScope | None = None,
        pagespeed_scope: PageSpeedScope | None = None,
        authorization: str | None,
        google_api_key: str | None = None,
        telegram_credential=None,
        body: bytes = b"",
        operation_id: UUID,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> ProviderEgressResponse:
        """Build headers from a closed profile, never from caller-supplied pairs."""
        if not isinstance(profile, EgressProfile) or profile not in PROFILE_RULES:
            raise ValueError("A known provider egress profile is required.")
        rules = PROFILE_RULES[profile]
        if rules.purpose != self.purpose:
            raise ValueError("Egress profile purpose is invalid.")
        validate_provider_context(profile, self.run)
        try:
            request = SharedEgressRequest(
                self.purpose,
                EgressHttpRequest(
                    method,
                    url,
                    headers=profile_headers(profile, method, authorization, google_api_key),
                    body=body,
                    accepted_media_types=rules.response_media_types,
                    max_response_bytes=max_response_bytes,
                    timeout_seconds=timeout_seconds,
                    telegram_credential=telegram_credential,
                ),
                profile=profile,
                sensitive_body=rules.sensitive_body,
                github_write_scope=github_write_scope,
                dataforseo_scope=dataforseo_scope,
                indexnow_scope=indexnow_scope,
                ga4_scope=ga4_scope,
                wordpress_scope=wordpress_scope,
                drive_picked_scope=drive_picked_scope,
                webflow_scope=webflow_scope,
                bing_page_scope=bing_page_scope,
                pagespeed_scope=pagespeed_scope,
            )
            if isinstance(self.run, OwnerConnectorContext):
                return execute_owner_connector_request(self, request, operation_id)
            if isinstance(self.run, WeeklyDataForSeoContext):
                return execute_weekly_dataforseo_request(self, request, operation_id)
            if isinstance(self.run, WeeklyPageSpeedContext):
                return execute_weekly_pagespeed_request(self, request, operation_id)
            result = execute_shared_egress(
                self.admission_connection,
                self.ingest_connection,
                self.store,
                self.run,
                self.policy,
                self.fetcher,
                request,
                operation_id=operation_id,
                worker_key=self.worker_key,
                admission_policy=self.admission_policy,
                artifact_key=self.artifact_key,
            )
        except SharedEgressConflict:
            raise ProviderEgressUnavailable(
                "EGRESS_OPERATION_CONFLICT",
                retryable=False,
            ) from None
        except SharedEgressPermitExpired:
            raise ProviderEgressUnavailable(
                "EGRESS_COMPLETION_UNCERTAIN",
                retryable=True,
            ) from None
        except SharedEgressUnavailable:
            raise ProviderEgressUnavailable(
                "EGRESS_AUTHORITY_UNAVAILABLE",
                retryable=True,
            ) from None
        except DatabaseError:
            raise ProviderEgressUnavailable(
                "EGRESS_STATE_UNAVAILABLE",
                retryable=True,
            ) from None

        if isinstance(result, SharedEgressDeferred):
            raise ProviderEgressUnavailable("EGRESS_DEFERRED", retryable=True)
        if isinstance(result, SharedEgressPending):
            raise ProviderEgressUnavailable(
                "EGRESS_DISPATCH_UNCERTAIN",
                retryable=True,
            )
        if isinstance(result, SharedEgressBlocked):
            raise ProviderEgressUnavailable("EGRESS_ROBOTS_DENIED", retryable=False)
        if not isinstance(result, SharedEgressReceipt):
            raise RuntimeError("Shared egress returned an invalid provider result.")
        if result.response.outcome != "fetched":
            raise ProviderEgressUnavailable(
                "EGRESS_RESPONSE_REJECTED",
                retryable=result.response.outcome == "transport_error",
            )
        if result.response.http_status is None or result.response.media_type is None:
            raise RuntimeError("Shared egress returned incomplete provider evidence.")
        return ProviderEgressResponse(
            result.response.http_status,
            result.response.media_type,
            result.response.body,
        )


def prepare_robots_through_shared_egress(
    admission_connection: Connection,
    ingest_connection: Connection,
    store: EncryptedLocalArtifactStore,
    run: CrawlRunOpened,
    lease: CrawlFrontierLease,
    policy: CrawlScopePolicy,
    fetcher: object,
    *,
    dispatch_id: UUID,
    permit_id: UUID,
    worker_key: str,
    admission_policy: OriginAdmissionPolicy,
    network_profile_sha256: str,
    artifact_key: ArtifactEncryptionKey,
    retain_until: datetime,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SharedRobotsPrepared | SharedRobotsDeferred | SharedRobotsPending:
    """Bootstrap or reuse robots evidence without opening a second network path."""
    if (
        not isinstance(lease, CrawlFrontierLease)
        or lease.run_id != run.run_id
        or lease.tenant_id != run.tenant_id
        or lease.site_id != run.site_id
        or lease.url.origin not in policy.allowed_origins
        or not isinstance(dispatch_id, UUID)
        or not isinstance(permit_id, UUID)
        or not isinstance(artifact_key, ArtifactEncryptionKey)
    ):
        raise ValueError("Validated robots egress authority is required.")
    observed_at = _utc(clock(), "robots egress time")
    try:
        current = authorize_from_current_snapshot(
            ingest_connection,
            store,
            run,
            policy,
            lease.url.fetch_url,
            at_time=observed_at,
            artifact_key=artifact_key,
        )
        return SharedRobotsPrepared(None, current)
    except RobotsSnapshotUnavailable:
        pass

    try:
        permit = acquire_origin_permit(
            admission_connection,
            lease,
            permit_id=permit_id,
            permit_kind="robots",
            policy=admission_policy,
        )
    except Exception as error:
        raise SharedEgressUnavailable("Robots admission is unavailable.") from error
    if isinstance(permit, OriginPermitDeferred):
        return SharedRobotsDeferred(permit.retry_at, permit.reason)
    if isinstance(permit, OriginPermitReplay):
        return SharedRobotsPending(dispatch_id, "dispatch_outcome_unknown")
    if not isinstance(permit, OriginPermitGrant):
        raise RuntimeError("Robots admission returned an invalid result.")

    robots_url = f"{lease.url.origin}/robots.txt"
    request = SharedEgressRequest(
        "crawl",
        EgressHttpRequest(
            "GET",
            robots_url,
            headers=(("accept", "text/plain,text/html;q=0.5"),),
            accepted_media_types=("text/plain", "text/html", "application/octet-stream"),
            max_response_bytes=ROBOTS_MAX_BODY_BYTES,
            timeout_seconds=min(60.0, policy.total_timeout_seconds),
        ),
        EgressProfile.CRAWL_ROBOTS,
    )
    with _clean_transaction(admission_connection):
        row = admission_connection.execute(
            "SELECT dispatch_id, dispatch_state, duplicate, outcome "
            "FROM control.begin_crawl_robots_dispatch(" + ", ".join(["%s"] * 11) + ")",
            (
                run.tenant_id,
                run.site_id,
                run.run_id,
                lease.frontier_id,
                lease.lease_id,
                permit.permit_id,
                worker_key,
                dispatch_id,
                robots_url,
                request.request_sha256,
                ROBOTS_MAX_BODY_BYTES,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Robots egress dispatch returned no outcome.")
    if row[3] == "dispatch_conflict":
        raise SharedEgressConflict()
    if row[3] == "authority_unavailable":
        raise SharedEgressUnavailable("Verified robots authority is unavailable.")
    if row[3] in {"dispatch_unknown", "replayed"}:
        try:
            current = authorize_from_current_snapshot(
                ingest_connection,
                store,
                run,
                policy,
                lease.url.fetch_url,
                at_time=_utc(clock(), "robots reconciliation time"),
                artifact_key=artifact_key,
            )
            return SharedRobotsPrepared(None, current)
        except RobotsSnapshotUnavailable:
            return SharedRobotsPending(dispatch_id, "dispatch_outcome_unknown")
    if row[3] != "admitted":
        raise RuntimeError("Robots egress dispatch returned an invalid outcome.")

    started_at = max(observed_at, permit.issued_at)
    try:
        response = fetcher.request(request.http, policy=policy)
    except CrawlFetchRejected:
        response = _failed_response(request.http, "policy_rejected", started_at, clock)
    except CrawlFetchUnavailable:
        response = _failed_response(request.http, "transport_error", started_at, clock)
    admitted_url = policy.admit(robots_url)
    if not isinstance(response, EgressHttpResult) or not _valid_response(
        request.http, admitted_url, response
    ):
        response = _failed_response(request.http, "policy_rejected", started_at, clock)
    result = _robots_result(lease.url.origin, robots_url, response)
    snapshot_id = uuid5(_ROBOTS_EGRESS_NAMESPACE, f"{run.tenant_id}:{dispatch_id}")
    recorded_at = _utc(clock(), "robots observation time")
    snapshot = persist_robots_snapshot(
        ingest_connection,
        store,
        run,
        policy,
        result,
        snapshot_id=snapshot_id,
        fetched_at=started_at + timedelta(milliseconds=result.elapsed_ms),
        network_profile_sha256=network_profile_sha256,
        artifact_key=artifact_key if result.outcome == "fetched" else None,
        retain_until=retain_until if result.outcome == "fetched" else None,
        recorded_at=max(recorded_at, started_at + timedelta(milliseconds=result.elapsed_ms)),
    )
    with _clean_transaction(admission_connection):
        finished = admission_connection.execute(
            "SELECT dispatch_state, finished_at, duplicate, outcome "
            "FROM control.finish_crawl_robots_dispatch(" + ", ".join(["%s"] * 7) + ")",
            (
                run.tenant_id,
                run.site_id,
                run.run_id,
                dispatch_id,
                request.request_sha256,
                result.outcome,
                snapshot.snapshot_id,
            ),
        ).fetchone()
    if finished is None or finished[3] != "finished":
        raise SharedEgressUnavailable("Robots completion evidence is unavailable.")
    completion, _ = _robots_completion(result)
    try:
        finish_origin_permit(admission_connection, permit, completion)
    except OriginPermitExpired:
        pass
    decision = authorize_from_current_snapshot(
        ingest_connection,
        store,
        run,
        policy,
        lease.url.fetch_url,
        at_time=_utc(clock(), "robots decision time"),
        artifact_key=artifact_key,
    )
    return SharedRobotsPrepared(snapshot, decision)


def execute_shared_egress(
    admission_connection: Connection,
    ingest_connection: Connection,
    store: EncryptedLocalArtifactStore,
    run: CrawlRunOpened,
    policy: CrawlScopePolicy,
    fetcher: object,
    request: SharedEgressRequest,
    *,
    operation_id: UUID,
    worker_key: str,
    admission_policy: OriginAdmissionPolicy,
    artifact_key: ArtifactEncryptionKey | None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SharedEgressReceipt | SharedEgressDeferred | SharedEgressPending | SharedEgressBlocked:
    """Dispatch at most once after current robots and global-origin admission."""
    now = _validate_inputs(
        admission_connection,
        ingest_connection,
        store,
        run,
        policy,
        fetcher,
        request,
        operation_id,
        worker_key,
        admission_policy,
        clock,
    )
    url = policy.admit(request.http.url)
    try:
        robots = authorize_from_current_snapshot(
            ingest_connection,
            store,
            run,
            policy,
            url.fetch_url,
            at_time=now,
            artifact_key=artifact_key,
            request_target=request.http.request_target(url)
            if request.http.telegram_credential
            else None,
        )
    except RobotsSnapshotUnavailable:
        raise SharedEgressUnavailable("Current robots evidence is unavailable.") from None
    if not robots.allowed:
        return SharedEgressBlocked(robots.snapshot_id, robots.reason, robots.expires_at)

    effective_policy = OriginAdmissionPolicy(
        schema_version=admission_policy.schema_version,
        profile_version=admission_policy.profile_version,
        min_delay_ms=max(
            admission_policy.min_delay_ms,
            robots.crawl_delay_ms or admission_policy.min_delay_ms,
        ),
        permit_lease_seconds=admission_policy.permit_lease_seconds,
    )
    begun = _begin(
        admission_connection,
        run,
        request,
        operation_id=operation_id,
        worker_key=worker_key,
        robots=robots,
        policy=effective_policy,
        origin=url.origin,
    )
    if isinstance(begun, (SharedEgressDeferred, SharedEgressPending)):
        return begun

    started_at = max(now, begun.issued_at)
    try:
        response = fetcher.request(request.http, policy=policy)
    except CrawlFetchRejected:
        response = _failed_response(request.http, "policy_rejected", started_at, clock)
    except CrawlFetchUnavailable:
        response = _failed_response(request.http, "transport_error", started_at, clock)
    except Exception:
        if request.http.telegram_credential is None:
            raise
        response = _failed_response(request.http, "transport_error", started_at, clock)
    if request.http.telegram_credential is not None and isinstance(response, EgressHttpResult):
        # Provider headers can reflect path credentials. Retain none for this profile.
        response = replace(response, response_headers=())
    if not isinstance(response, EgressHttpResult) or not _valid_response(
        request.http,
        url,
        response,
    ):
        response = _failed_response(request.http, "policy_rejected", started_at, clock)
    observed_at = _utc(clock(), "shared egress response time")
    completion_kind, retry_after_ms = _completion(response, observed_at=observed_at)
    return _finish(
        admission_connection,
        begun,
        response,
        completion_kind=completion_kind,
        retry_after_ms=retry_after_ms,
    )


def _begin(
    connection: Connection,
    run: CrawlRunOpened,
    request: SharedEgressRequest,
    *,
    operation_id: UUID,
    worker_key: str,
    robots: RobotsAccessDecision,
    policy: OriginAdmissionPolicy,
    origin: str,
) -> SharedEgressDispatch | SharedEgressDeferred | SharedEgressPending:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT operation_id, bucket_id, admitted_origin, profile_version, workload_id, "
            "permit_kind, min_delay_ms, requested_lease_seconds, issued_at, expires_at, "
            "authority_fingerprint, operation_state, finished_at, network_outcome, http_status, "
            "response_headers, response_sha256, response_bytes, media_type, resolved_address, "
            "completion_kind, observed_latency_ms, retry_after_ms, available_at, "
            "active_in_flight, duplicate, outcome "
            "FROM control.begin_shared_egress_operation(" + ", ".join(["%s"] * 19) + ")",
            (
                run.tenant_id,
                run.site_id,
                run.run_id,
                worker_key,
                operation_id,
                request.purpose,
                request.http.method,
                request.evidence_url,
                origin,
                request.request_sha256,
                request.body_sha256,
                len(request.http.body),
                request.http.max_response_bytes,
                request.credentialed,
                robots.snapshot_id,
                robots.reason,
                policy.profile_version,
                policy.min_delay_ms,
                policy.permit_lease_seconds,
            ),
        ).fetchone()
        if row is not None and row[26] == "admitted":
            binding = PROFILE_RULES[request.profile].binding
            args = (
                run.tenant_id,
                run.site_id,
                operation_id,
                request.request_sha256,
                *binding.arguments(request),
            )
            bound = connection.execute(
                f"SELECT control.{binding.function}(" + ", ".join(["%s"] * len(args)) + ")", args
            ).fetchone()
            if bound != ("bound",):
                raise SharedEgressUnavailable("Egress profile binding is unavailable.")
    if row is None or not isinstance(row[26], str):
        raise RuntimeError("Shared egress admission returned no valid outcome.")
    outcome = row[26]
    if outcome == "operation_conflict":
        raise SharedEgressConflict()
    if outcome == "authority_unavailable":
        raise SharedEgressUnavailable()
    if outcome.startswith("deferred_"):
        reason = outcome.removeprefix("deferred_")
        if reason not in {"backoff", "in_flight", "politeness"}:
            raise RuntimeError("Shared egress admission returned an invalid deferral.")
        return SharedEgressDeferred(origin, reason, row[23], row[24])
    if outcome in {"dispatch_unknown", "replayed"}:
        reason = (
            "dispatch_outcome_unknown" if outcome == "dispatch_unknown" else "body_not_retained"
        )
        return SharedEgressPending(operation_id, reason)
    if outcome != "admitted":
        raise RuntimeError("Shared egress admission returned an invalid outcome.")
    dispatch = SharedEgressDispatch(
        operation_id=row[0],
        tenant_id=run.tenant_id,
        site_id=run.site_id,
        crawl_run_id=run.run_id,
        worker_key=worker_key,
        bucket_id=row[1],
        origin=row[2],
        egress_profile=request.profile,
        profile_version=row[3],
        workload_id=row[4],
        min_delay_ms=row[6],
        requested_lease_seconds=row[7],
        issued_at=row[8],
        expires_at=row[9],
        authority_fingerprint_sha256=row[10].hex() if isinstance(row[10], bytes) else "",
        request_sha256=request.request_sha256.hex(),
        robots_snapshot_id=robots.snapshot_id,
        robots_reason=robots.reason,
    )
    _validate_dispatch(dispatch, request, policy)
    return dispatch


def _finish(
    connection: Connection,
    dispatch: SharedEgressDispatch,
    response: EgressHttpResult,
    *,
    completion_kind: str,
    retry_after_ms: int | None,
) -> SharedEgressReceipt:
    headers = dict(response.response_headers)
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT operation_id, operation_state, finished_at, network_outcome, http_status, "
            "response_headers, response_sha256, response_bytes, media_type, resolved_address, "
            "completion_kind, observed_latency_ms, retry_after_ms, next_allowed_at, "
            "degraded_until, active_in_flight, duplicate, outcome "
            "FROM control.finish_shared_egress_operation(" + ", ".join(["%s"] * 20) + ")",
            (
                dispatch.tenant_id,
                dispatch.site_id,
                dispatch.crawl_run_id,
                dispatch.worker_key,
                dispatch.operation_id,
                dispatch.bucket_id,
                dispatch.origin,
                dispatch.profile_version,
                bytes.fromhex(dispatch.request_sha256),
                dispatch.robots_snapshot_id,
                response.outcome,
                response.http_status,
                Jsonb(headers),
                bytes.fromhex(response.body_sha256) if response.body_sha256 else None,
                response.decoded_bytes,
                response.media_type,
                response.resolved_address,
                completion_kind,
                _bounded_latency(response.elapsed_ms),
                retry_after_ms,
            ),
        ).fetchone()
    if row is None:
        raise RuntimeError("Shared egress completion returned no outcome.")
    if row[17] == "operation_conflict":
        raise SharedEgressConflict()
    if row[17] == "permit_expired":
        raise SharedEgressPermitExpired()
    if row[17] != "finished":
        raise RuntimeError("Shared egress completion returned an invalid outcome.")
    receipt = SharedEgressReceipt(
        operation_id=row[0],
        egress_profile=dispatch.egress_profile,
        state=row[1],
        finished_at=row[2],
        network_outcome=row[3],
        completion_kind=row[10],
        next_allowed_at=row[13],
        degraded_until=row[14],
        active_in_flight=row[15],
        response=response,
        duplicate=row[16],
    )
    if (
        receipt.operation_id != dispatch.operation_id
        or receipt.state not in {"observed", "failed"}
        or not _aware(receipt.finished_at)
        or receipt.network_outcome != response.outcome
        or receipt.completion_kind != completion_kind
        or not _aware(receipt.next_allowed_at)
        or (receipt.degraded_until is not None and not _aware(receipt.degraded_until))
        or not _integer_between(receipt.active_in_flight, 0, 1)
        or not isinstance(receipt.duplicate, bool)
    ):
        raise RuntimeError("Shared egress completion returned invalid evidence.")
    return receipt


def _validate_inputs(
    admission_connection: object,
    ingest_connection: object,
    store: object,
    run: object,
    policy: object,
    fetcher: object,
    request: object,
    operation_id: object,
    worker_key: object,
    admission_policy: object,
    clock: object,
) -> datetime:
    if admission_connection is ingest_connection:
        raise ValueError("Shared egress requires separate admission and evidence connections.")
    for connection in (admission_connection, ingest_connection):
        if (
            not isinstance(connection, Connection)
            or not connection.autocommit
            or connection.info.transaction_status != TransactionStatus.IDLE
        ):
            raise ValueError("Shared egress requires idle autocommit database connections.")
    if not isinstance(store, EncryptedLocalArtifactStore):
        raise ValueError("Shared egress requires a validated artifact store.")
    if not isinstance(run, CrawlRunOpened) or run.status != "running":
        raise ValueError("Shared egress requires a running crawl authority record.")
    if not isinstance(policy, CrawlScopePolicy) or not isinstance(request, SharedEgressRequest):
        raise ValueError("Shared egress requires validated request policy.")
    policy.admit(request.http.url)
    if not callable(getattr(fetcher, "request", None)):
        raise ValueError("Shared egress requires a pinned HTTP boundary.")
    if not isinstance(operation_id, UUID) or operation_id.version != 4:
        raise ValueError("Shared egress requires a caller-generated UUIDv4.")
    if not isinstance(worker_key, str) or _WORKER.fullmatch(worker_key) is None:
        raise ValueError("Shared egress worker identity is invalid.")
    if not isinstance(admission_policy, OriginAdmissionPolicy):
        raise ValueError("Shared egress requires a global origin admission policy.")
    if not callable(clock):
        raise ValueError("Shared egress requires a wall clock.")
    return _utc(clock(), "shared egress dispatch time")


def _validate_dispatch(
    dispatch: SharedEgressDispatch,
    request: SharedEgressRequest,
    policy: OriginAdmissionPolicy,
) -> None:
    expected_fingerprint = hashlib.sha256(
        (
            f"{dispatch.tenant_id}:{dispatch.site_id}:{dispatch.crawl_run_id}:"
            f"{dispatch.worker_key}:{dispatch.operation_id}:{dispatch.request_sha256}:"
            f"{dispatch.robots_snapshot_id}"
        ).encode()
    ).hexdigest()
    if (
        dispatch.profile_version != policy.profile_version
        or dispatch.workload_id != f"egress:{dispatch.crawl_run_id}:{dispatch.operation_id}"
        or dispatch.min_delay_ms != policy.min_delay_ms
        or dispatch.requested_lease_seconds != policy.permit_lease_seconds
        or dispatch.request_sha256 != request.request_sha256.hex()
        or dispatch.authority_fingerprint_sha256 != expected_fingerprint
        or not _aware(dispatch.issued_at)
        or not _aware(dispatch.expires_at)
        or dispatch.expires_at <= dispatch.issued_at
    ):
        raise RuntimeError("Shared egress admission returned invalid permit evidence.")


def _completion(response: EgressHttpResult, *, observed_at: datetime) -> tuple[str, int | None]:
    if response.outcome == "transport_error":
        return "transport_error", None
    if response.outcome == "policy_rejected":
        return "cancelled", None
    if response.http_status == 429:
        retry_after, _ = _retry_after(
            dict(response.response_headers).get("retry-after"),
            observed_at=observed_at,
            fallback_ms=60_000,
        )
        return "rate_limited", retry_after
    if response.http_status == 503:
        retry_after, _ = _retry_after(
            dict(response.response_headers).get("retry-after"),
            observed_at=observed_at,
            fallback_ms=None,
        )
        return "service_unavailable", retry_after
    return "success", None


def _robots_result(origin: str, robots_url: str, response: EgressHttpResult) -> RobotsFetchResult:
    outcome = response.outcome
    if outcome == "fetched":
        status = response.http_status
        if status is None:
            outcome = "policy_rejected"
        elif 200 <= status <= 299:
            outcome = "fetched"
        elif status == 404:
            outcome = "not_found"
        elif status in {401, 403}:
            outcome = "forbidden"
        elif status == 429:
            outcome = "backoff"
        elif 500 <= status <= 599:
            outcome = "server_error"
        elif 400 <= status <= 499:
            outcome = "client_error"
        else:
            outcome = "policy_rejected"
    retained_body = response.body if outcome == "fetched" else b""
    return RobotsFetchResult(
        schema_version=1,
        origin=origin,
        robots_url=robots_url,
        final_url=robots_url,
        outcome=outcome,
        http_status=response.http_status,
        media_type=response.media_type if outcome == "fetched" else None,
        response_headers=response.response_headers,
        redirect_chain=(),
        resolved_address=response.resolved_address,
        body=retained_body,
        body_sha256=hashlib.sha256(retained_body).hexdigest() if outcome == "fetched" else None,
        decoded_bytes=len(retained_body),
        elapsed_ms=response.elapsed_ms,
    )


def _robots_completion(result: RobotsFetchResult) -> tuple[OriginPermitCompletion, str]:
    retry_after_ms: int | None = None
    basis = "none"
    if result.outcome == "backoff":
        retry_after_ms, basis = _retry_after(
            dict(result.response_headers).get("retry-after"),
            observed_at=datetime.now(UTC),
            fallback_ms=60_000,
        )
        return OriginPermitCompletion("rate_limited", result.elapsed_ms, retry_after_ms), basis
    if result.outcome == "server_error":
        retry_after_ms, basis = _retry_after(
            dict(result.response_headers).get("retry-after"),
            observed_at=datetime.now(UTC),
            fallback_ms=None,
        )
        return OriginPermitCompletion(
            "service_unavailable", result.elapsed_ms, retry_after_ms
        ), basis
    if result.outcome == "transport_error":
        return OriginPermitCompletion("transport_error", result.elapsed_ms), basis
    if result.outcome == "policy_rejected":
        return OriginPermitCompletion("cancelled", result.elapsed_ms), basis
    return OriginPermitCompletion("success", result.elapsed_ms), basis


def _failed_response(
    request: EgressHttpRequest,
    outcome: str,
    started_at: datetime,
    clock: Callable[[], datetime],
) -> EgressHttpResult:
    return EgressHttpResult(
        schema_version=1,
        request_url=request.url,
        final_url=request.url,
        method=request.method,
        outcome=outcome,
        http_status=None,
        media_type=None,
        response_headers=(),
        resolved_address=None,
        body=b"",
        body_sha256=None,
        decoded_bytes=0,
        elapsed_ms=_wall_latency(started_at, _utc(clock(), "shared egress completion time")),
    )


def _valid_response(
    request: EgressHttpRequest,
    admitted_url: CrawlUrl,
    response: EgressHttpResult,
) -> bool:
    if (
        response.schema_version != 1
        or response.request_url != request.url
        or response.final_url != admitted_url.fetch_url
        or response.method != request.method
        or response.outcome
        not in {
            "fetched",
            "redirect_rejected",
            "unsupported_encoding",
            "unsupported_media_type",
            "body_limit",
            "policy_rejected",
            "transport_error",
        }
        or not _integer_between(response.elapsed_ms, 0, 120_000)
        or not _valid_headers(response.response_headers)
    ):
        return False
    if response.outcome in {"policy_rejected", "transport_error"}:
        return (
            response.http_status is None
            and response.media_type is None
            and response.response_headers == ()
            and response.resolved_address is None
            and response.body == b""
            and response.body_sha256 is None
            and response.decoded_bytes == 0
        )
    if (
        not _integer_between(response.http_status, 100, 599)
        or response.http_status == 304
        or not isinstance(response.resolved_address, str)
    ):
        return False
    try:
        validate_public_addresses((response.resolved_address,))
    except ValueError:
        return False
    if response.outcome != "fetched":
        return response.body == b"" and response.body_sha256 is None and response.decoded_bytes == 0
    return (
        response.media_type in request.accepted_media_types
        and isinstance(response.body, bytes)
        and len(response.body) <= request.max_response_bytes
        and response.decoded_bytes == len(response.body)
        and response.body_sha256 == hashlib.sha256(response.body).hexdigest()
    )


def _valid_headers(headers: object) -> bool:
    if not isinstance(headers, tuple) or len(headers) > len(_RESPONSE_HEADERS):
        return False
    names: list[str] = []
    total = 0
    for item in headers:
        if not isinstance(item, tuple) or len(item) != 2:
            return False
        name, value = item
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or name not in _RESPONSE_HEADERS
            or name in names
            or not value
            or len(value) > 2048
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            return False
        names.append(name)
        total += len(name) + len(value)
    return names == sorted(names) and total <= 16_384


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


_WORKER = re.compile(r"[a-z][a-z0-9_.:-]{0,127}")
_ROBOTS_EGRESS_NAMESPACE = UUID("f273ba9a-5e35-45c8-8fce-91ddcad7c602")
_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-encoding",
        "content-length",
        "content-type",
        "etag",
        "last-modified",
        "location",
        "retry-after",
        "transfer-encoding",
    }
)
