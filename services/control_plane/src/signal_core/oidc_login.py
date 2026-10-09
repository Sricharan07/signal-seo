"""Durable, one-time pre-tenant OIDC login-attempt persistence."""

import hashlib
import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg import Error as PsycopgError
from psycopg.errors import UniqueViolation

from signal_core.database import _clean_transaction

_OPAQUE_VALUE = re.compile(r"[A-Za-z0-9_-]{43}")
_CLIENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SECRET_REFERENCE = re.compile(r"secret://[A-Za-z0-9][A-Za-z0-9._:/@-]{0,501}")
_URL_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
OidcLoginPurpose = Literal["login", "invitation_acceptance"]
_LOGIN_PURPOSES = frozenset({"login", "invitation_acceptance"})


class InvalidOidcLoginAttempt(Exception):
    """The state, browser binding, nonce, expiry, or stored attempt is invalid."""


class OidcLoginStateConflict(Exception):
    """A cryptographically random state hash unexpectedly already exists."""


class OidcLoginAuditError(Exception):
    """A consumed login failure could not be recorded without sensitive detail."""


@dataclass(frozen=True)
class OidcClientRegistration:
    issuer: str
    client_id: str
    redirect_uri: str

    def __post_init__(self) -> None:
        if not _valid_oidc_url(self.issuer):
            raise ValueError("OIDC issuer must be an exact HTTPS or loopback URL.")
        if not isinstance(self.client_id, str) or _CLIENT_ID.fullmatch(self.client_id) is None:
            raise ValueError("OIDC client_id is invalid.")
        if not _valid_oidc_url(self.redirect_uri):
            raise ValueError("OIDC redirect_uri must be an exact HTTPS or loopback URL.")


@dataclass(frozen=True)
class ConsumedOidcLoginAttempt:
    id: UUID
    registration: OidcClientRegistration
    nonce_hash: bytes = field(repr=False)
    pkce_secret_reference: str = field(repr=False)
    return_path: str
    purpose: OidcLoginPurpose = "login"

    def validate_nonce(self, nonce: object) -> None:
        try:
            supplied_hash = _hash_opaque_value(nonce)
        except ValueError:
            raise InvalidOidcLoginAttempt() from None
        if not hmac.compare_digest(self.nonce_hash, supplied_hash):
            raise InvalidOidcLoginAttempt()


def create_oidc_login_attempt(
    connection: Connection,
    *,
    attempt_id: UUID | None = None,
    state: object,
    nonce: object,
    browser_binding: object,
    registration: OidcClientRegistration,
    pkce_secret_reference: object,
    return_path: object,
    purpose: object = "login",
    ttl_seconds: int = 300,
) -> UUID:
    """Persist only hashes and a secret reference for a bounded pre-login attempt."""
    identifier = _validate_attempt_id(attempt_id)
    state_hash = _hash_opaque_value(state)
    nonce_hash = _hash_opaque_value(nonce)
    browser_binding_hash = _hash_opaque_value(browser_binding)
    if not isinstance(registration, OidcClientRegistration):
        raise ValueError("A validated OIDC client registration is required.")
    secret_reference = _validate_secret_reference(pkce_secret_reference)
    validated_return_path = validate_oidc_return_path(return_path)
    validated_purpose = validate_oidc_login_purpose(purpose)
    validated_ttl = validate_oidc_attempt_ttl(ttl_seconds)

    try:
        with _clean_transaction(connection):
            _set_login_scope(connection, state_hash, browser_binding_hash)
            connection.execute(
                "INSERT INTO control.oidc_login_attempts "
                "(id, state_hash, nonce_hash, browser_binding_hash, oidc_issuer, client_id, "
                "redirect_uri, pkce_secret_reference, return_path, purpose, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "now() + make_interval(secs => %s))",
                (
                    identifier,
                    state_hash,
                    nonce_hash,
                    browser_binding_hash,
                    registration.issuer,
                    registration.client_id,
                    registration.redirect_uri,
                    secret_reference,
                    validated_return_path,
                    validated_purpose,
                    validated_ttl,
                ),
            )
    except UniqueViolation:
        raise OidcLoginStateConflict() from None
    return identifier


def consume_oidc_login_attempt(
    connection: Connection, *, state: object, browser_binding: object
) -> ConsumedOidcLoginAttempt:
    """Atomically consume one unexpired attempt selected by both browser proofs."""
    try:
        state_hash = _hash_opaque_value(state)
        browser_binding_hash = _hash_opaque_value(browser_binding)
    except ValueError:
        raise InvalidOidcLoginAttempt() from None

    with _clean_transaction(connection):
        _set_login_scope(connection, state_hash, browser_binding_hash)
        row = connection.execute(
            "UPDATE control.oidc_login_attempts SET consumed_at = now() "
            "WHERE state_hash = %s AND browser_binding_hash = %s "
            "AND consumed_at IS NULL AND expires_at > now() "
            "RETURNING id, oidc_issuer, client_id, redirect_uri, nonce_hash, "
            "pkce_secret_reference, return_path, purpose",
            (state_hash, browser_binding_hash),
        ).fetchone()
        if row is None:
            raise InvalidOidcLoginAttempt()
        return ConsumedOidcLoginAttempt(
            id=row[0],
            registration=OidcClientRegistration(
                issuer=row[1], client_id=row[2], redirect_uri=row[3]
            ),
            nonce_hash=bytes(row[4]),
            pkce_secret_reference=row[5],
            return_path=row[6],
            purpose=validate_oidc_login_purpose(row[7]),
        )


def read_oidc_login_purpose(
    connection: Connection, *, state: object, browser_binding: object
) -> OidcLoginPurpose:
    """Read one live attempt purpose without consuming its callback proofs."""
    try:
        state_hash = _hash_opaque_value(state)
        browser_binding_hash = _hash_opaque_value(browser_binding)
    except ValueError:
        raise InvalidOidcLoginAttempt() from None

    with _clean_transaction(connection):
        _set_login_scope(connection, state_hash, browser_binding_hash)
        row = connection.execute(
            "SELECT purpose FROM control.oidc_login_attempts "
            "WHERE state_hash = %s AND browser_binding_hash = %s "
            "AND consumed_at IS NULL AND expires_at > now()",
            (state_hash, browser_binding_hash),
        ).fetchone()
        if row is None:
            raise InvalidOidcLoginAttempt()
        try:
            return validate_oidc_login_purpose(row[0])
        except ValueError:
            raise InvalidOidcLoginAttempt() from None


_LOGIN_FAILURE_REASONS = frozenset(
    {
        "provider_configuration_failed",
        "pkce_unavailable",
        "pkce_consume_failed",
        "provider_assertion_failed",
        "identity_not_authorized",
        "session_persistence_failed",
        "invitation_identity_not_verified",
        "invitation_proof_persistence_failed",
    }
)


def record_consumed_login_failure(
    connection: Connection,
    *,
    attempt: ConsumedOidcLoginAttempt,
    state: object,
    browser_binding: object,
    reason: object,
    event_id_factory: Callable[[], UUID] = uuid4,
) -> UUID:
    """Append one typed failure event for an already consumed, proof-matched attempt."""
    if (
        not isinstance(attempt, ConsumedOidcLoginAttempt)
        or not isinstance(reason, str)
        or reason not in _LOGIN_FAILURE_REASONS
    ):
        raise OidcLoginAuditError()
    if not callable(event_id_factory):
        raise OidcLoginAuditError()
    try:
        state_hash = _hash_opaque_value(state)
        browser_binding_hash = _hash_opaque_value(browser_binding)
        event_id = event_id_factory()
        if not isinstance(event_id, UUID) or event_id.version != 4:
            raise ValueError
        with _clean_transaction(connection):
            _set_login_scope(connection, state_hash, browser_binding_hash)
            cursor = connection.execute(
                "INSERT INTO control.platform_events "
                "(id, event_type, actor_user_id, object_kind, object_id, facts, reason) "
                "SELECT %s, 'identity.login.failed', NULL, 'oidc_login_attempt', id, "
                "'{\"schema_version\":1}'::jsonb, %s "
                "FROM control.oidc_login_attempts "
                "WHERE id = %s AND consumed_at IS NOT NULL",
                (event_id, reason, attempt.id),
            )
            if cursor.rowcount != 1:
                raise OidcLoginAuditError()
    except (PsycopgError, RuntimeError, TypeError, ValueError):
        raise OidcLoginAuditError() from None
    return event_id


def _set_login_scope(
    connection: Connection, state_hash: bytes, browser_binding_hash: bytes
) -> None:
    connection.execute("SELECT set_config('signal.oidc_state_hash', %s, true)", (state_hash.hex(),))
    connection.execute(
        "SELECT set_config('signal.oidc_browser_binding_hash', %s, true)",
        (browser_binding_hash.hex(),),
    )


def _hash_opaque_value(value: object) -> bytes:
    if not isinstance(value, str) or _OPAQUE_VALUE.fullmatch(value) is None:
        raise ValueError("OIDC browser values must be 256-bit unpadded base64url strings.")
    return hashlib.sha256(value.encode("ascii")).digest()


def _validate_secret_reference(value: object) -> str:
    if not isinstance(value, str) or _SECRET_REFERENCE.fullmatch(value) is None:
        raise ValueError("PKCE verifier must be represented by a valid secret reference.")
    return value


def _validate_attempt_id(value: UUID | None) -> UUID:
    identifier = uuid4() if value is None else value
    if not isinstance(identifier, UUID) or identifier.version != 4:
        raise ValueError("OIDC login attempt ID must be a UUIDv4 value.")
    return identifier


def validate_oidc_return_path(value: object) -> str:
    """Validate a local post-login destination before creating external state."""
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or len(value) > 1024
        or not value.isascii()
        or "\\" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError("OIDC return_path must be a bounded local absolute path.")
    return value


def validate_oidc_attempt_ttl(value: object) -> int:
    """Validate the bounded durable login-attempt lifetime."""
    if isinstance(value, bool) or not isinstance(value, int) or not 60 <= value <= 600:
        raise ValueError("OIDC login attempt TTL must be between 60 and 600 seconds.")
    return value


def validate_oidc_login_purpose(value: object) -> OidcLoginPurpose:
    """Keep login and authority-provisioning callbacks cryptographically distinct."""
    if not isinstance(value, str) or value not in _LOGIN_PURPOSES:
        raise ValueError("OIDC login purpose is invalid.")
    return value


def _valid_oidc_url(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or not value.isascii()
        or any(ord(character) < 0x20 for character in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "\\" in parsed.netloc
    ):
        return False
    host = parsed.hostname.lower()
    if host != "::1" and _URL_HOST.fullmatch(host) is None:
        return False
    return parsed.scheme == "https" or host in _LOOPBACK_HOSTS
