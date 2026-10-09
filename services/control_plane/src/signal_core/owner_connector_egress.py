"""Human-owner onboarding through the crawler's shared network controls."""

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import UUID, uuid5

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_http import (
    CrawlFetchRejected,
    CrawlFetchUnavailable,
    EgressHttpRequest,
    EgressHttpResult,
)
from signal_core.crawl_robots import ROBOTS_MAX_BODY_BYTES, parse_robots
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.database import Scope, _clean_transaction
from signal_core.egress_profiles import PROFILE_RULES, EgressProfile
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import hash_session_token

_ROBOTS_NAMESPACE = UUID("7469c72b-ab86-46d4-9a4c-320c673fa52a")
_PROFILES = frozenset(profile for profile, rules in PROFILE_RULES.items() if rules.owner_allowed)


@dataclass(frozen=True, repr=False)
class OwnerConnectorContext:
    tenant_id: UUID
    site_id: UUID
    session_token: str
    recovery_generation: str

    def __post_init__(self):
        if not isinstance(self.tenant_id, UUID) or not isinstance(self.site_id, UUID):
            raise ValueError("Typed owner connector scope is required.")
        hash_session_token(self.session_token)
        validate_recovery_generation(self.recovery_generation)


@dataclass(frozen=True, repr=False)
class WeeklyPageSpeedContext:
    tenant_id: UUID
    site_id: UUID
    handle: str
    recovery_generation: str

    def __post_init__(self):
        if not isinstance(self.tenant_id, UUID) or not isinstance(self.site_id, UUID):
            raise ValueError("Typed weekly PageSpeed scope is required.")
        hash_session_token(self.handle)
        validate_recovery_generation(self.recovery_generation)


@dataclass(frozen=True, repr=False)
class WeeklyDataForSeoContext(WeeklyPageSpeedContext):
    """Only the admitted strategy stage's dual-budget paid requests."""


@dataclass(frozen=True)
class ConnectorAuthorityRules:
    context_function: str
    begin_function: str
    finish_function: str


_AUTHORITY_RULES = MappingProxyType(
    {
        WeeklyDataForSeoContext: ConnectorAuthorityRules(
            "strategy_provider_egress_context",
            "weekly_skill_begin_dataforseo_egress",
            "weekly_skill_finish_dataforseo_egress",
        ),
        WeeklyPageSpeedContext: ConnectorAuthorityRules(
            "weekly_skill_pagespeed_context",
            "weekly_skill_begin_pagespeed_egress",
            "weekly_skill_finish_pagespeed_egress",
        ),
        OwnerConnectorContext: ConnectorAuthorityRules(
            "resolve_snapshot_authority",
            "begin_owner_connector_egress",
            "finish_owner_connector_egress",
        ),
    }
)


def execute_weekly_dataforseo_request(provider, request, operation_id):
    if (
        not isinstance(provider.run, WeeklyDataForSeoContext)
        or request.profile != EgressProfile.DATAFORSEO
    ):
        raise ValueError("Only standing-scoped DataForSEO is permitted.")
    return _execute_connector_request(provider, request, operation_id, weekly=True)


def execute_owner_connector_request(provider, request, operation_id):
    if not isinstance(provider.run, OwnerConnectorContext):
        raise ValueError("Current owner context required.")
    return _execute_connector_request(provider, request, operation_id, weekly=False)


def execute_weekly_pagespeed_request(provider, request, operation_id):
    if (
        not isinstance(provider.run, WeeklyPageSpeedContext)
        or request.profile != EgressProfile.PAGESPEED
    ):
        raise ValueError("Only standing-scoped PageSpeed is permitted.")
    return _execute_connector_request(provider, request, operation_id, weekly=True)


def _execute_connector_request(provider, request, operation_id, *, weekly):
    from signal_core.shared_egress import (
        ProviderEgressResponse,
        ProviderEgressUnavailable,
        SharedEgressRequest,
        _completion,
        _failed_response,
        _valid_response,
    )

    context = provider.run
    if (
        not isinstance(context, WeeklyPageSpeedContext if weekly else OwnerConnectorContext)
        or provider.purpose != "connector"
        or request.profile not in _PROFILES
        or not isinstance(operation_id, UUID)
        or operation_id.version != 4
        or not isinstance(provider.policy, CrawlScopePolicy)
        or provider.policy.max_redirects != 0
        or not isinstance(provider.store, EncryptedLocalArtifactStore)
        or not isinstance(provider.artifact_key, ArtifactEncryptionKey)
        or not callable(getattr(provider.fetcher, "request", None))
        or provider.admission_connection is provider.ingest_connection
    ):
        raise ValueError("Validated owner connector dependencies are required.")
    for connection in (provider.admission_connection, provider.ingest_connection):
        if (
            not isinstance(connection, Connection)
            or not connection.autocommit
            or connection.info.transaction_status != TransactionStatus.IDLE
        ):
            raise ValueError("Owner connector egress requires idle autocommit connections.")
    token_hash = hash_session_token(context.handle if weekly else context.session_token)
    common = (token_hash, context.recovery_generation, context.site_id)
    authority = next(
        rules
        for context_type, rules in _AUTHORITY_RULES.items()
        if isinstance(context, context_type)
    )
    with _clean_transaction(provider.admission_connection):
        current = provider.admission_connection.execute(
            "SELECT "
            + ("tenant_id" if weekly else "tenant_id, role_key, authentication_level, outcome")
            + f" FROM control.{authority.context_function}(%s,%s,%s)",
            common if weekly else (token_hash, context.site_id, context.recovery_generation),
        ).fetchone()
    if current != (
        (context.tenant_id,) if weekly else (context.tenant_id, "owner", "mfa", "authorized")
    ):
        raise ProviderEgressUnavailable("EGRESS_AUTHORITY_UNAVAILABLE", retryable=False)
    target = provider.policy.admit(request.http.url)
    robots_request = SharedEgressRequest(
        "crawl",
        EgressHttpRequest(
            "GET",
            target.origin + "/robots.txt",
            headers=(("accept", "text/plain,text/html;q=0.5"),),
            accepted_media_types=(
                "text/plain",
                "text/html",
                "application/octet-stream",
                "application/json",
            ),
            max_response_bytes=min(ROBOTS_MAX_BODY_BYTES, provider.policy.max_body_bytes),
            timeout_seconds=min(5, provider.policy.request_timeout_seconds),
        ),
        PROFILE_RULES[request.profile].robots_profile,
    )
    robots_id = uuid5(_ROBOTS_NAMESPACE, str(operation_id))

    def begin(kind, identity, candidate, delay, robots_identity):
        with _clean_transaction(provider.admission_connection):
            row = provider.admission_connection.execute(
                "SELECT * FROM control."
                + authority.begin_function
                + "("
                + ",".join(["%s"] * 17)
                + ")",
                (
                    *common,
                    identity,
                    kind,
                    request.profile.value,
                    candidate.http.method,
                    candidate.evidence_url,
                    request.evidence_url,
                    target.origin,
                    candidate.request_sha256,
                    candidate.body_sha256,
                    len(candidate.http.body),
                    candidate.http.max_response_bytes,
                    candidate.credentialed,
                    robots_identity,
                    delay,
                ),
            ).fetchone()
        if row is None or row[0] not in {"admitted", "robots_replayed"}:
            outcome = row[0] if row is not None else "denied"
            code = {
                "deferred": "EGRESS_DEFERRED",
                "unknown": "EGRESS_DISPATCH_UNCERTAIN",
                "replayed": "EGRESS_BODY_NOT_RETAINED",
                "conflict": "EGRESS_OPERATION_CONFLICT",
                "robots_denied": "EGRESS_ROBOTS_DENIED",
            }.get(outcome, "EGRESS_AUTHORITY_UNAVAILABLE")
            raise ProviderEgressUnavailable(code, retryable=outcome == "deferred")
        return row

    def send(identity, candidate, kind):
        started = datetime.now(UTC)
        try:
            result = provider.fetcher.request(candidate.http, policy=provider.policy)
        except CrawlFetchRejected:
            result = _failed_response(
                candidate.http, "policy_rejected", started, lambda: datetime.now(UTC)
            )
        except CrawlFetchUnavailable:
            result = _failed_response(
                candidate.http, "transport_error", started, lambda: datetime.now(UTC)
            )
        if not isinstance(result, EgressHttpResult) or not _valid_response(
            candidate.http, provider.policy.admit(candidate.http.url), result
        ):
            result = _failed_response(
                candidate.http, "policy_rejected", started, lambda: datetime.now(UTC)
            )
        allowed = rules_hash = crawl_delay = artifact = None
        if kind == "robots":
            allowed = result.outcome == "fetched" and result.http_status == 404
            if (
                result.outcome == "fetched"
                and result.http_status == 200
                and result.media_type == "text/plain"
            ):
                with provider.store.stage_verified(
                    Scope(context.tenant_id, context.site_id),
                    identity,
                    result.body,
                    media_type="text/plain",
                    key=provider.artifact_key,
                ) as stored:
                    artifact = {
                        "artifact_id": str(stored.artifact_id),
                        "object_key": stored.object_key,
                        "object_version": stored.object_version,
                        "sha256": stored.sha256,
                        "encryption_key_ref": stored.encryption_key_ref,
                    }
                try:
                    rules = parse_robots(
                        result.body, origin=target.origin, user_agent=provider.policy.user_agent
                    )
                    allowed = rules.can_fetch(target)
                    rules_hash = bytes.fromhex(rules.rules_sha256)
                    crawl_delay = rules.crawl_delay_ms
                except ValueError:
                    allowed = False
        completion, retry_after = _completion(result, observed_at=datetime.now(UTC))
        evidence = {
            key: getattr(result, key)
            for key in (
                "outcome",
                "http_status",
                "media_type",
                "resolved_address",
                "body_sha256",
                "decoded_bytes",
                "elapsed_ms",
            )
        }
        evidence["robots_artifact"] = artifact
        with _clean_transaction(provider.ingest_connection):
            row = provider.ingest_connection.execute(
                "SELECT control." + authority.finish_function + "(" + ",".join(["%s"] * 10) + ")",
                (
                    *common,
                    identity,
                    Jsonb(evidence),
                    completion,
                    retry_after,
                    allowed,
                    rules_hash,
                    crawl_delay,
                ),
            ).fetchone()
        if row != ("finished",):
            raise ProviderEgressUnavailable("EGRESS_COMPLETION_UNCERTAIN", retryable=False)
        return result, allowed, crawl_delay

    robots_row = begin("robots", robots_id, robots_request, 1000, None)
    if robots_row[0] == "robots_replayed":
        allowed, crawl_delay = robots_row[3:5]
    else:
        _, allowed, crawl_delay = send(robots_id, robots_request, "robots")
    if allowed is not True:
        raise ProviderEgressUnavailable("EGRESS_ROBOTS_DENIED", retryable=False)
    begin("provider", operation_id, request, max(1000, crawl_delay or 1000), robots_id)
    result, _, _ = send(operation_id, request, "provider")
    if result.outcome != "fetched" or result.http_status is None or result.media_type is None:
        raise ProviderEgressUnavailable("EGRESS_RESPONSE_REJECTED", retryable=False)
    return ProviderEgressResponse(result.http_status, result.media_type, result.body)
