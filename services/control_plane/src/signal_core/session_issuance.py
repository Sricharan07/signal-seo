"""Issue hash-only sessions after verified OIDC and current membership checks."""

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation

from signal_core.database import _clean_transaction
from signal_core.identity_conditions import normalize_ascii_mailbox
from signal_core.oidc_protocol import VerifiedOidcIdentity
from signal_core.session_tokens import InvalidOpaqueSessionToken, hash_session_token

_GENERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_ACR = re.compile(r"[\x21-\x7e]{1,255}")
_TOKEN_ATTEMPTS = 3


class SessionIssuanceDenied(Exception):
    """The verified identity is not eligible for an application session."""


class InvalidIdentitySession(Exception):
    """The global identity session is invalid or cannot enter the tenant."""


@dataclass(frozen=True)
class SessionPolicy:
    primary_acr_values: frozenset[str]
    mfa_acr_values: frozenset[str]
    identity_ttl_seconds: int = 8 * 60 * 60
    tenant_ttl_seconds: int = 8 * 60 * 60
    maximum_auth_age_seconds: int = 12 * 60 * 60
    required_mfa_methods: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.primary_acr_values, frozenset)
            or not isinstance(self.mfa_acr_values, frozenset)
            or not self.primary_acr_values
        ):
            raise ValueError("Session ACR policy is invalid.")
        if not isinstance(self.required_mfa_methods, frozenset) or any(
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", value) is None
            for value in self.required_mfa_methods
        ):
            raise ValueError("Session MFA method policy is invalid.")
        values = self.primary_acr_values | self.mfa_acr_values
        if self.primary_acr_values & self.mfa_acr_values or any(
            not isinstance(value, str) or _ACR.fullmatch(value) is None for value in values
        ):
            raise ValueError("Session ACR policy is invalid.")
        for value in (
            self.identity_ttl_seconds,
            self.tenant_ttl_seconds,
            self.maximum_auth_age_seconds,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 300 <= value <= 86400:
                raise ValueError("Session time policy must be between 300 and 86400 seconds.")

    def authentication_level(self, acr: str | None) -> str:
        if not isinstance(acr, str):
            raise SessionIssuanceDenied()
        if acr in self.mfa_acr_values:
            return "mfa"
        if acr in self.primary_acr_values:
            return "primary"
        raise SessionIssuanceDenied()


@dataclass(frozen=True)
class IssuedIdentitySession:
    id: UUID
    audit_event_id: UUID
    user_id: UUID
    token: str = field(repr=False)
    authentication_level: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedTenantSession:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    token: str = field(repr=False)
    role_key: str
    authentication_level: str
    expires_at: datetime


DEFAULT_SESSION_POLICY = SessionPolicy(
    primary_acr_values=frozenset({"1"}),
    mfa_acr_values=frozenset({"2", "urn:signal:acr:mfa"}),
)


def _generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def issue_identity_session(
    connection: Connection,
    *,
    identity: VerifiedOidcIdentity,
    current_recovery_generation: object,
    policy: SessionPolicy = DEFAULT_SESSION_POLICY,
    now: datetime | None = None,
    token_factory: Callable[[], str] = _generate_session_token,
) -> IssuedIdentitySession:
    """Issue one global session for an existing enabled, exact issuer/subject user."""
    generation = validate_recovery_generation(current_recovery_generation)
    current = _validate_now(now)
    level, auth_time, expires_at = _validate_verified_identity(identity, policy, current)
    verified_email = (
        normalize_ascii_mailbox(identity.verified_email)
        if identity.verified_email is not None
        else None
    )

    for attempt_number in range(_TOKEN_ATTEMPTS):
        raw_token, token_hash = _new_token(token_factory)
        session_id = uuid4()
        audit_event_id = uuid4()
        try:
            with _clean_transaction(connection):
                _set_identity_session_hash(connection, token_hash)
                row = connection.execute(
                    "INSERT INTO control.identity_sessions "
                    "(id, user_id, token_hash, auth_time, authentication_level, "
                    "recovery_generation, expires_at, last_seen_at) "
                    "SELECT %s, u.id, %s, %s, %s, %s, %s, GREATEST(%s, %s) "
                    "FROM control.users u "
                    "WHERE u.oidc_issuer = %s AND u.oidc_subject = %s "
                    "AND u.disabled_at IS NULL "
                    "RETURNING user_id",
                    (
                        session_id,
                        token_hash,
                        auth_time,
                        level,
                        generation,
                        expires_at,
                        current,
                        auth_time,
                        identity.issuer,
                        identity.subject,
                    ),
                ).fetchone()
                if row is None:
                    raise SessionIssuanceDenied()
                connection.execute(
                    "SELECT control.record_email_identity_claim(%s,%s,%s,%s,%s)",
                    (
                        session_id,
                        identity.issuer,
                        identity.subject,
                        verified_email,
                        datetime.fromtimestamp(identity.issued_at, UTC),
                    ),
                )
                connection.execute(
                    "INSERT INTO control.platform_events "
                    "(id, event_type, actor_user_id, object_kind, object_id, facts, reason) "
                    "VALUES (%s, 'identity.session.issued', %s, 'identity_session', %s, "
                    "jsonb_build_object('schema_version', 1, "
                    "'authentication_level', %s::text), NULL)",
                    (audit_event_id, row[0], session_id, level),
                )
        except UniqueViolation as error:
            if (
                error.diag.constraint_name != "identity_sessions_token_hash_key"
                or attempt_number == _TOKEN_ATTEMPTS - 1
            ):
                raise RuntimeError("Identity session token allocation failed.") from None
            continue
        return IssuedIdentitySession(
            id=session_id,
            audit_event_id=audit_event_id,
            user_id=row[0],
            token=raw_token,
            authentication_level=level,
            expires_at=expires_at,
        )
    raise RuntimeError("Identity session token allocation failed.")


def issue_tenant_session(
    connection: Connection,
    *,
    identity_session_token: object,
    requested_tenant_id: object,
    current_recovery_generation: object,
    policy: SessionPolicy = DEFAULT_SESSION_POLICY,
    now: datetime | None = None,
    token_factory: Callable[[], str] = _generate_session_token,
) -> IssuedTenantSession:
    """Select one current membership and mint its tenant-scoped browser session."""
    try:
        identity_token_hash = hash_session_token(identity_session_token)
    except InvalidOpaqueSessionToken:
        raise InvalidIdentitySession() from None
    if not isinstance(requested_tenant_id, UUID):
        raise InvalidIdentitySession()
    generation = validate_recovery_generation(current_recovery_generation)
    current = _validate_now(now)
    if not isinstance(policy, SessionPolicy):
        raise ValueError("A validated session policy is required.")

    for attempt_number in range(_TOKEN_ATTEMPTS):
        raw_token, tenant_token_hash = _new_token(token_factory)
        session_id = uuid4()
        try:
            with _clean_transaction(connection):
                _set_identity_session_hash(connection, identity_token_hash)
                connection.execute(
                    "SELECT set_config('signal.tenant_id', %s, true)",
                    (str(requested_tenant_id),),
                )
                parent = connection.execute(
                    "SELECT i.id, i.user_id, i.auth_time, i.authentication_level, "
                    "i.expires_at, m.role_key "
                    "FROM control.identity_sessions i "
                    "JOIN control.users u ON u.id = i.user_id "
                    "JOIN app.memberships m ON m.user_id = i.user_id "
                    "AND m.tenant_id = %s "
                    "JOIN app.tenants t ON t.tenant_id = m.tenant_id "
                    "WHERE i.token_hash = %s AND i.revoked_at IS NULL "
                    "AND i.expires_at > %s AND i.recovery_generation = %s "
                    "AND u.disabled_at IS NULL AND m.state = 'active' "
                    "AND t.lifecycle = 'active'",
                    (
                        requested_tenant_id,
                        identity_token_hash,
                        current,
                        generation,
                    ),
                ).fetchone()
                if parent is None:
                    raise InvalidIdentitySession()
                expires_at = min(parent[4], current + timedelta(seconds=policy.tenant_ttl_seconds))
                if expires_at <= current:
                    raise InvalidIdentitySession()
                connection.execute(
                    "INSERT INTO app.sessions "
                    "(tenant_id, id, identity_session_id, user_id, session_token_hash, "
                    "auth_time, mfa_level, expires_at, last_seen_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, GREATEST(%s, %s))",
                    (
                        requested_tenant_id,
                        session_id,
                        parent[0],
                        parent[1],
                        tenant_token_hash,
                        parent[2],
                        parent[3],
                        expires_at,
                        current,
                        parent[2],
                    ),
                )
        except UniqueViolation as error:
            if (
                error.diag.constraint_name != "sessions_session_token_hash_key"
                or attempt_number == _TOKEN_ATTEMPTS - 1
            ):
                raise RuntimeError("Tenant session token allocation failed.") from None
            continue
        return IssuedTenantSession(
            id=session_id,
            tenant_id=requested_tenant_id,
            user_id=parent[1],
            token=raw_token,
            role_key=parent[5],
            authentication_level=parent[3],
            expires_at=expires_at,
        )
    raise RuntimeError("Tenant session token allocation failed.")


def _validate_verified_identity(
    identity: object, policy: SessionPolicy, current: datetime
) -> tuple[str, datetime, datetime]:
    if not isinstance(identity, VerifiedOidcIdentity) or not isinstance(policy, SessionPolicy):
        raise ValueError("A verified identity and validated session policy are required.")
    if not _valid_identity_lookup(identity.issuer, 2048) or not _valid_identity_lookup(
        identity.subject, 512
    ):
        raise SessionIssuanceDenied()
    level = policy.authentication_level(identity.authentication_context)
    if policy.required_mfa_methods and (
        level != "mfa"
        or not isinstance(identity.authentication_methods, frozenset)
        or not policy.required_mfa_methods <= identity.authentication_methods
    ):
        raise SessionIssuanceDenied()
    numeric_dates = (identity.issued_at, identity.expires_at, identity.auth_time)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in numeric_dates):
        raise SessionIssuanceDenied()
    try:
        auth_time = datetime.fromtimestamp(identity.auth_time, UTC)
        issued_at = datetime.fromtimestamp(identity.issued_at, UTC)
        token_expires_at = datetime.fromtimestamp(identity.expires_at, UTC)
    except (OSError, OverflowError, TypeError, ValueError):
        raise SessionIssuanceDenied() from None
    if (
        issued_at > current + timedelta(seconds=30)
        or issued_at < current - timedelta(minutes=11)
        or token_expires_at <= current
        or token_expires_at <= issued_at
        or auth_time > current + timedelta(seconds=30)
        or auth_time > issued_at + timedelta(seconds=30)
        or auth_time < current - timedelta(seconds=policy.maximum_auth_age_seconds)
    ):
        raise SessionIssuanceDenied()
    expires_at = min(
        current + timedelta(seconds=policy.identity_ttl_seconds),
        auth_time + timedelta(seconds=policy.maximum_auth_age_seconds),
    )
    if expires_at <= current or expires_at <= auth_time:
        raise SessionIssuanceDenied()
    return level, auth_time, expires_at


def validate_recovery_generation(value: object) -> str:
    """Validate a generation obtained from the independent recovery authority."""
    if not isinstance(value, str) or _GENERATION.fullmatch(value) is None:
        raise RuntimeError("A validated external recovery generation is required.")
    return value


def _valid_identity_lookup(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and not any(ord(character) < 0x20 for character in value)
    )


def _validate_now(value: datetime | None) -> datetime:
    current = value if value is not None else datetime.now(UTC)
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Session issuance requires an aware current time.")
    return current.astimezone(UTC)


def _new_token(factory: Callable[[], str]) -> tuple[str, bytes]:
    if not callable(factory):
        raise ValueError("Session token factory must be callable.")
    try:
        token = factory()
        return token, hash_session_token(token)
    except (InvalidOpaqueSessionToken, TypeError):
        raise RuntimeError("Session token source returned an invalid value.") from None


def _set_identity_session_hash(connection: Connection, token_hash: bytes) -> None:
    connection.execute(
        "SELECT set_config('signal.identity_session_hash', %s, true)",
        (token_hash.hex(),),
    )
