"""Exact-origin HTTP ownership challenges under current owner authority."""

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation

from signal_core.authorization import InvalidSession
from signal_core.crawl_http import (
    CrawlFetchRejected,
    CrawlFetchResult,
    CrawlFetchUnavailable,
    PinnedHttpFetcher,
)
from signal_core.crawl_urls import CrawlScopePolicy
from signal_core.database import _clean_transaction
from signal_core.session_issuance import validate_recovery_generation
from signal_core.session_tokens import session_token_hasher
from signal_core.site_onboarding import InvalidSiteOnboarding, normalize_public_site_origin

CHALLENGE_PATH = "/.well-known/signal-site-verification.txt"
CHALLENGE_TTL_SECONDS = 30 * 60
VERIFICATION_RECHECK_SECONDS = 30 * 24 * 60 * 60
_MAX_OPEN_CHALLENGES = 10
_MAX_CHALLENGE_ATTEMPTS = 10
_ALLOCATION_ATTEMPTS = 3
_COLLISION_CONSTRAINTS = frozenset(
    {
        "site_origin_challenges_pkey",
        "site_origin_challenges_request_key",
        "site_origin_verification_attempts_pkey",
        "site_origin_verification_attempts_request_key",
    }
)


class InvalidOriginVerification(ValueError):
    """The requested proof does not match the exact verification contract."""


class OriginVerificationDenied(Exception):
    """The current principal cannot verify this site's public origin."""


class OriginVerificationConflict(Exception):
    """The request identity, site state, or bounded attempt state conflicts."""


class OriginChallengeNotFound(Exception):
    """No accessible challenge matches the requested exact site and origin."""


class OriginChallengeExpired(Exception):
    """The requested challenge is no longer current."""


class OriginProofNotFound(Exception):
    """The exact challenge resource was not found."""


class OriginProofMismatch(Exception):
    """The challenge resource did not contain the exact expected proof."""


class OriginProofRejected(Exception):
    """The proof response or destination violated the verification policy."""


class OriginProofUnavailable(Exception):
    """The proof resource could not be reached through the controlled boundary."""


class OriginClaimConflict(Exception):
    """The origin is already protected by another control claim."""


@dataclass(frozen=True, repr=False)
class OriginChallenge:
    tenant_id: UUID
    user_id: UUID
    site_id: UUID
    challenge_id: UUID
    origin: str
    proof_url: str
    proof_content: str
    issued_at: datetime
    expires_at: datetime
    replayed: bool


@dataclass(frozen=True)
class VerifiedOrigin:
    tenant_id: UUID
    user_id: UUID
    site_id: UUID
    challenge_id: UUID
    origin: str
    proof_method: str
    verified_at: datetime
    recheck_at: datetime
    replayed: bool


@dataclass(frozen=True, repr=False)
class PreparedOriginVerification:
    tenant_id: UUID
    user_id: UUID
    site_id: UUID
    challenge_id: UUID
    origin: str
    proof_url: str
    proof_content: str
    proof_sha256: bytes
    expires_at: datetime


@dataclass(frozen=True, repr=False)
class OriginProofObservation:
    outcome: str
    http_status: int | None = None
    media_type: str | None = None
    response_sha256: bytes | None = None
    final_url: str | None = None
    resolved_address: str | None = None
    elapsed_ms: int | None = None


def issue_origin_challenge(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    requested_site_id: object,
    origin: object,
    idempotency_key: object,
    challenge_id_factory: Callable[[], UUID] = uuid4,
) -> OriginChallenge:
    """Issue one reconstructable, expiring proof for the selected exact site."""
    token_hash = _session_hash(session_token)
    generation = validate_recovery_generation(current_recovery_generation)
    site_id = _uuid4(requested_site_id, "Site identity")
    canonical_origin = _origin(origin)
    request_id = _uuid4(idempotency_key, "Challenge idempotency key")
    request_hash = _issue_request_hash(site_id, canonical_origin)
    if not callable(challenge_id_factory):
        raise InvalidOriginVerification("Challenge identity factory is invalid.")

    for attempt_number in range(_ALLOCATION_ATTEMPTS):
        challenge_id = _new_uuid(challenge_id_factory)
        try:
            with _clean_transaction(connection):
                row = connection.execute(
                    "SELECT outcome, challenge_tenant_id, challenge_user_id, "
                    "challenge_site_id, challenge_id, challenge_origin, issued_at, "
                    "expires_at, request_replayed "
                    "FROM control.issue_site_origin_challenge(%s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s)",
                    (
                        token_hash,
                        generation,
                        site_id,
                        challenge_id,
                        request_id,
                        request_hash,
                        canonical_origin,
                        CHALLENGE_TTL_SECONDS,
                        _MAX_OPEN_CHALLENGES,
                    ),
                ).fetchone()
                result = _challenge_result(row)
                _validate_challenge(result)
                return result
        except UniqueViolation as error:
            if (
                error.diag.constraint_name not in _COLLISION_CONSTRAINTS
                or attempt_number == _ALLOCATION_ATTEMPTS - 1
            ):
                raise RuntimeError("Origin challenge identity allocation failed.") from None
    raise RuntimeError("Origin challenge identity allocation failed.")


def prepare_origin_verification(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    requested_site_id: object,
    challenge_id: object,
    origin: object,
    idempotency_key: object,
) -> PreparedOriginVerification | VerifiedOrigin:
    """Recheck authority and idempotency before any public network request."""
    token_hash = _session_hash(session_token)
    generation = validate_recovery_generation(current_recovery_generation)
    site_id = _uuid4(requested_site_id, "Site identity")
    proof_id = _uuid4(challenge_id, "Challenge identity")
    canonical_origin = _origin(origin)
    request_id = _uuid4(idempotency_key, "Verification idempotency key")
    request_hash = _verification_request_hash(site_id, proof_id, canonical_origin)
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT outcome, verification_tenant_id, verification_user_id, "
            "verification_site_id, verification_challenge_id, verification_origin, "
            "proof_sha256, expires_at, verified_at, recheck_at "
            "FROM control.prepare_site_origin_verification(%s, %s, %s, %s, %s, "
            "%s, %s, %s)",
            (
                token_hash,
                generation,
                site_id,
                proof_id,
                request_id,
                request_hash,
                canonical_origin,
                _MAX_CHALLENGE_ATTEMPTS,
            ),
        ).fetchone()
    return _prepared_result(row)


def observe_origin_proof(
    fetcher: PinnedHttpFetcher,
    prepared: PreparedOriginVerification,
) -> OriginProofObservation:
    """Fetch one exact plaintext proof without following any redirect."""
    if not isinstance(fetcher, PinnedHttpFetcher):
        raise ValueError("A pinned origin proof fetcher is required.")
    if not isinstance(prepared, PreparedOriginVerification):
        raise ValueError("A prepared origin proof is required.")
    policy = CrawlScopePolicy(
        schema_version=1,
        allowed_origins=(prepared.origin,),
        user_agent="SignalBot/1.0 (+https://signal.example/bot)",
        max_redirects=0,
        max_body_bytes=1024,
        request_timeout_seconds=5,
        total_timeout_seconds=10,
    )
    try:
        result = fetcher.fetch_text(prepared.proof_url, policy=policy)
    except CrawlFetchRejected:
        return OriginProofObservation(outcome="policy_rejected")
    except CrawlFetchUnavailable:
        return OriginProofObservation(outcome="transport_unavailable")
    observation = _observation_from_fetch(result)
    if observation.outcome != "candidate":
        return observation
    expected = prepared.proof_content.encode("ascii")
    outcome = "matched" if hmac.compare_digest(result.body, expected) else "proof_mismatch"
    return OriginProofObservation(
        outcome=outcome,
        http_status=result.http_status,
        media_type=result.media_type,
        response_sha256=(
            bytes.fromhex(result.body_sha256) if result.body_sha256 is not None else None
        ),
        final_url=result.final_url,
        resolved_address=result.resolved_address,
        elapsed_ms=result.elapsed_ms,
    )


def record_origin_verification(
    connection: Connection,
    *,
    session_token: object,
    current_recovery_generation: object,
    requested_site_id: object,
    challenge_id: object,
    origin: object,
    idempotency_key: object,
    observation: OriginProofObservation,
    attempt_id_factory: Callable[[], UUID] = uuid4,
) -> VerifiedOrigin:
    """Atomically retain the observation and promote only an exact current proof."""
    token_hash = _session_hash(session_token)
    generation = validate_recovery_generation(current_recovery_generation)
    site_id = _uuid4(requested_site_id, "Site identity")
    proof_id = _uuid4(challenge_id, "Challenge identity")
    canonical_origin = _origin(origin)
    request_id = _uuid4(idempotency_key, "Verification idempotency key")
    request_hash = _verification_request_hash(site_id, proof_id, canonical_origin)
    _validate_observation(observation)
    if not callable(attempt_id_factory):
        raise InvalidOriginVerification("Verification identity factory is invalid.")

    for attempt_number in range(_ALLOCATION_ATTEMPTS):
        attempt_id = _new_uuid(attempt_id_factory)
        try:
            verified_result: VerifiedOrigin | None = None
            with _clean_transaction(connection):
                row = connection.execute(
                    "SELECT outcome, verification_tenant_id, verification_user_id, "
                    "verification_site_id, verification_challenge_id, "
                    "verification_origin, proof_method, verified_at, recheck_at, "
                    "request_replayed "
                    "FROM control.record_site_origin_verification(%s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        token_hash,
                        generation,
                        site_id,
                        proof_id,
                        attempt_id,
                        request_id,
                        request_hash,
                        canonical_origin,
                        observation.outcome,
                        observation.http_status,
                        observation.media_type,
                        observation.response_sha256,
                        observation.final_url,
                        observation.resolved_address,
                        observation.elapsed_ms,
                        VERIFICATION_RECHECK_SECONDS,
                    ),
                ).fetchone()
                if row is not None and row[0] == "verified":
                    verified_result = _verification_result(row)
                    _validate_verification(verified_result)
        except UniqueViolation as error:
            if (
                error.diag.constraint_name not in _COLLISION_CONSTRAINTS
                or attempt_number == _ALLOCATION_ATTEMPTS - 1
            ):
                raise RuntimeError("Origin verification identity allocation failed.") from None
        else:
            if verified_result is not None:
                return verified_result
            _verification_result(row)
            raise RuntimeError("Origin verification recording failed.")
    raise RuntimeError("Origin verification identity allocation failed.")


def _challenge_result(row: object) -> OriginChallenge:
    if row is None or row[0] == "invalid_session":
        raise InvalidSession()
    if row[0] == "denied":
        raise OriginVerificationDenied()
    if row[0] == "conflict":
        raise OriginVerificationConflict()
    if row[0] == "limit_reached":
        raise OriginVerificationConflict()
    if row[0] != "issued":
        raise RuntimeError("Origin challenge issuance failed.")
    origin = row[5]
    challenge_id = row[4]
    return OriginChallenge(
        tenant_id=row[1],
        user_id=row[2],
        site_id=row[3],
        challenge_id=challenge_id,
        origin=origin,
        proof_url=_proof_url(origin),
        proof_content=_proof_content(challenge_id),
        issued_at=row[6],
        expires_at=row[7],
        replayed=row[8],
    )


def _prepared_result(row: object) -> PreparedOriginVerification | VerifiedOrigin:
    if row is None or row[0] == "invalid_session":
        raise InvalidSession()
    _raise_outcome(row[0])
    if row[0] == "verified":
        result = VerifiedOrigin(
            tenant_id=row[1],
            user_id=row[2],
            site_id=row[3],
            challenge_id=row[4],
            origin=row[5],
            proof_method="http_well_known",
            verified_at=row[8],
            recheck_at=row[9],
            replayed=True,
        )
        _validate_verification(result)
        return result
    if row[0] != "prepared":
        raise RuntimeError("Origin verification preparation failed.")
    result = PreparedOriginVerification(
        tenant_id=row[1],
        user_id=row[2],
        site_id=row[3],
        challenge_id=row[4],
        origin=row[5],
        proof_url=_proof_url(row[5]),
        proof_content=_proof_content(row[4]),
        proof_sha256=row[6],
        expires_at=row[7],
    )
    if hashlib.sha256(result.proof_content.encode("ascii")).digest() != result.proof_sha256:
        raise RuntimeError("Prepared origin proof projection is invalid.")
    return result


def _verification_result(row: object) -> VerifiedOrigin:
    if row is None or row[0] == "invalid_session":
        raise InvalidSession()
    _raise_outcome(row[0])
    if row[0] != "verified":
        raise RuntimeError("Origin verification recording failed.")
    return VerifiedOrigin(
        tenant_id=row[1],
        user_id=row[2],
        site_id=row[3],
        challenge_id=row[4],
        origin=row[5],
        proof_method=row[6],
        verified_at=row[7],
        recheck_at=row[8],
        replayed=row[9],
    )


def _raise_outcome(outcome: object) -> None:
    errors = {
        "denied": OriginVerificationDenied,
        "conflict": OriginVerificationConflict,
        "challenge_not_found": OriginChallengeNotFound,
        "challenge_expired": OriginChallengeExpired,
        "proof_not_found": OriginProofNotFound,
        "proof_mismatch": OriginProofMismatch,
        "policy_rejected": OriginProofRejected,
        "transport_unavailable": OriginProofUnavailable,
        "invalid_response": OriginProofRejected,
        "claim_conflict": OriginClaimConflict,
    }
    error = errors.get(outcome)
    if error is not None:
        raise error()


def _observation_from_fetch(result: CrawlFetchResult) -> OriginProofObservation:
    values = {
        "http_status": result.http_status,
        "media_type": result.media_type,
        "response_sha256": (
            bytes.fromhex(result.body_sha256) if result.body_sha256 is not None else None
        ),
        "final_url": result.final_url,
        "resolved_address": result.resolved_address,
        "elapsed_ms": result.elapsed_ms,
    }
    if result.http_status == 404:
        return OriginProofObservation(outcome="proof_not_found", **values)
    if (
        result.outcome != "fetched"
        or result.http_status != 200
        or result.media_type != "text/plain"
        or result.redirect_chain
    ):
        return OriginProofObservation(outcome="invalid_response", **values)
    return OriginProofObservation(outcome="candidate", **values)


def _validate_observation(value: object) -> None:
    outcomes = {
        "matched",
        "proof_not_found",
        "proof_mismatch",
        "policy_rejected",
        "transport_unavailable",
        "invalid_response",
    }
    if not isinstance(value, OriginProofObservation) or value.outcome not in outcomes:
        raise InvalidOriginVerification("Origin proof observation is invalid.")
    if value.http_status is not None and (
        isinstance(value.http_status, bool)
        or not isinstance(value.http_status, int)
        or not 100 <= value.http_status <= 599
    ):
        raise InvalidOriginVerification("Origin proof observation is invalid.")
    if value.response_sha256 is not None and (
        not isinstance(value.response_sha256, bytes) or len(value.response_sha256) != 32
    ):
        raise InvalidOriginVerification("Origin proof observation is invalid.")
    for text, maximum in (
        (value.media_type, 100),
        (value.final_url, 2048),
        (value.resolved_address, 64),
    ):
        if text is not None and (
            not isinstance(text, str)
            or not text
            or len(text) > maximum
            or any(ord(character) < 32 or ord(character) == 127 for character in text)
        ):
            raise InvalidOriginVerification("Origin proof observation is invalid.")
    if value.elapsed_ms is not None and (
        isinstance(value.elapsed_ms, bool)
        or not isinstance(value.elapsed_ms, int)
        or not 0 <= value.elapsed_ms <= 2**31 - 1
    ):
        raise InvalidOriginVerification("Origin proof observation is invalid.")
    if value.outcome in {"policy_rejected", "transport_unavailable"} and any(
        item is not None
        for item in (
            value.http_status,
            value.media_type,
            value.response_sha256,
            value.final_url,
            value.resolved_address,
            value.elapsed_ms,
        )
    ):
        raise InvalidOriginVerification("Origin proof observation is invalid.")
    if value.outcome == "matched" and (
        value.http_status != 200
        or value.media_type != "text/plain"
        or value.response_sha256 is None
        or value.final_url is None
        or value.resolved_address is None
        or value.elapsed_ms is None
    ):
        raise InvalidOriginVerification("Origin proof observation is invalid.")


def _validate_challenge(value: OriginChallenge) -> None:
    if (
        not all(
            isinstance(item, UUID)
            for item in (value.tenant_id, value.user_id, value.site_id, value.challenge_id)
        )
        or _origin(value.origin) != value.origin
        or value.proof_url != _proof_url(value.origin)
        or value.proof_content != _proof_content(value.challenge_id)
        or not isinstance(value.issued_at, datetime)
        or value.issued_at.tzinfo is None
        or value.issued_at.utcoffset() is None
        or not isinstance(value.expires_at, datetime)
        or value.expires_at.tzinfo is None
        or value.expires_at.utcoffset() is None
        or value.expires_at <= value.issued_at
        or not isinstance(value.replayed, bool)
    ):
        raise RuntimeError("Origin challenge projection is invalid.")


def _validate_verification(value: VerifiedOrigin) -> None:
    if (
        not all(
            isinstance(item, UUID)
            for item in (value.tenant_id, value.user_id, value.site_id, value.challenge_id)
        )
        or _origin(value.origin) != value.origin
        or value.proof_method != "http_well_known"
        or not isinstance(value.verified_at, datetime)
        or value.verified_at.tzinfo is None
        or value.verified_at.utcoffset() is None
        or not isinstance(value.recheck_at, datetime)
        or value.recheck_at.tzinfo is None
        or value.recheck_at.utcoffset() is None
        or value.recheck_at <= value.verified_at
        or not isinstance(value.replayed, bool)
    ):
        raise RuntimeError("Origin verification projection is invalid.")


def _origin(value: object) -> str:
    try:
        return normalize_public_site_origin(value)
    except InvalidSiteOnboarding:
        raise InvalidOriginVerification("Origin must be one canonical HTTPS DNS origin.") from None


def _proof_url(origin: str) -> str:
    return f"{origin}{CHALLENGE_PATH}"


def _proof_content(challenge_id: UUID) -> str:
    return f"signal-site-verification={challenge_id}\n"


def _issue_request_hash(site_id: UUID, origin: str) -> bytes:
    return hashlib.sha256(f"origin={origin}\nsite_id={site_id}".encode("ascii")).digest()


def _verification_request_hash(site_id: UUID, challenge_id: UUID, origin: str) -> bytes:
    payload = f"challenge_id={challenge_id}\norigin={origin}\nsite_id={site_id}"
    return hashlib.sha256(payload.encode("ascii")).digest()


_session_hash = session_token_hasher(InvalidSession, include_type_error=True)


def _uuid4(value: object, name: str) -> UUID:
    if not isinstance(value, UUID) or value.version != 4:
        raise InvalidOriginVerification(f"{name} is invalid.")
    return value


def _new_uuid(factory: Callable[[], UUID]) -> UUID:
    try:
        value = factory()
    except (RuntimeError, StopIteration, TypeError):
        raise RuntimeError("Origin verification identity allocation failed.") from None
    if not isinstance(value, UUID) or value.version != 4:
        raise RuntimeError("Origin verification identity allocation failed.")
    return value
