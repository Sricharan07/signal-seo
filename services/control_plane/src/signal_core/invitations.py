"""Issue one hash-only, site-scoped invitation from current account authority."""

import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from signal_core.authorization import AuthorizedSite
from signal_core.database import Scope, _clean_transaction
from signal_core.identity_conditions import normalize_ascii_mailbox
from signal_core.session_tokens import InvalidOpaqueSessionToken, hash_session_token

_INVITATION_TOKEN_ATTEMPTS = 3
_MINIMUM_TTL_SECONDS = 15 * 60
_MAXIMUM_TTL_SECONDS = 7 * 24 * 60 * 60
_GRANTABLE_ROLES = {
    "admin": frozenset({"viewer", "analyst", "editor", "approver"}),
    "owner": frozenset({"viewer", "analyst", "editor", "approver", "admin"}),
}
_RETRYABLE_ALLOCATION_CONSTRAINTS = frozenset(
    {
        "audit_events_pkey",
        "invitation_routes_pkey",
        "invitations_pkey",
        "invitations_token_hash_key",
    }
)


def _generate_invitation_token() -> str:
    return secrets.token_urlsafe(32)


class InvitationDenied(Exception):
    """Current authority cannot issue the requested invitation."""


class InvitationConflict(Exception):
    """A live invitation already exists for this recipient and site."""


@dataclass(frozen=True)
class IssuedInvitation:
    id: UUID
    audit_event_id: UUID
    tenant_id: UUID
    site_id: UUID
    email_normalized: str = field(repr=False)
    role_key: str
    token: str = field(repr=False)
    expires_at: datetime


def issue_site_invitation(
    connection: Connection,
    *,
    principal: AuthorizedSite,
    email: object,
    role_key: object,
    ttl_seconds: object = 24 * 60 * 60,
    session_token: str | None = None,
    current_recovery_generation: str | None = None,
    token_factory: Callable[[], str] = _generate_invitation_token,
    invitation_id_factory: Callable[[], UUID] = uuid4,
    event_id_factory: Callable[[], UUID] = uuid4,
) -> IssuedInvitation:
    """Atomically create one invitation and its first immutable tenant audit event."""
    _validate_principal(principal)
    normalized_email = normalize_invitation_email(email)
    validated_role = _validate_grant(principal.role_key, role_key)
    validated_ttl = _validate_ttl(ttl_seconds)
    if (session_token is None) != (current_recovery_generation is None):
        raise InvitationDenied()
    if not all(
        callable(factory) for factory in (token_factory, invitation_id_factory, event_id_factory)
    ):
        raise ValueError("Invitation factories must be callable.")

    if session_token is not None:
        return _issue_browser_invitation(
            connection,
            principal=principal,
            email=normalized_email,
            role_key=validated_role,
            ttl_seconds=validated_ttl,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            token_factory=token_factory,
            invitation_id_factory=invitation_id_factory,
            event_id_factory=event_id_factory,
        )

    for attempt_number in range(_INVITATION_TOKEN_ATTEMPTS):
        token, token_hash = _new_invitation_token(token_factory)
        invitation_id = _new_uuid(invitation_id_factory)
        event_id = _new_uuid(event_id_factory)
        try:
            with _clean_transaction(connection):
                _set_principal_scope(connection, principal)
                if connection.execute("SELECT app.lock_invitation_authority()").fetchone() != (
                    True,
                ):
                    raise InvitationDenied()
                occurred_at = connection.execute("SELECT transaction_timestamp()").fetchone()[0]
                expires_at = occurred_at + timedelta(seconds=validated_ttl)
                lock_key = ":".join(
                    (
                        str(principal.scope.tenant_id),
                        str(principal.scope.site_id),
                        normalized_email,
                    )
                )
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (lock_key,),
                )
                if (
                    connection.execute(
                        "SELECT 1 FROM app.invitations "
                        "WHERE tenant_id = %s AND site_id = %s AND email_normalized = %s "
                        "AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at > %s "
                        "LIMIT 1",
                        (
                            principal.scope.tenant_id,
                            principal.scope.site_id,
                            normalized_email,
                            occurred_at,
                        ),
                    ).fetchone()
                    is not None
                ):
                    raise InvitationConflict()

                inserted = connection.execute(
                    "INSERT INTO app.invitations "
                    "(tenant_id, id, site_id, email_normalized, role_key, token_hash, "
                    "inviter_user_id, inviter_membership_epoch, "
                    "inviter_site_authorization_epoch, expires_at, created_at) "
                    "SELECT m.tenant_id, %s, sm.site_id, %s, %s, %s, m.user_id, "
                    "m.authorization_epoch, sm.authorization_epoch, %s, %s "
                    "FROM app.memberships m "
                    "JOIN app.site_memberships sm "
                    "ON sm.tenant_id = m.tenant_id AND sm.user_id = m.user_id "
                    "JOIN app.tenants t ON t.tenant_id = m.tenant_id "
                    "JOIN app.sites s ON s.tenant_id = sm.tenant_id AND s.id = sm.site_id "
                    "WHERE m.tenant_id = %s AND m.user_id = %s AND m.role_key = %s "
                    "AND m.authorization_epoch = %s AND m.state = 'active' "
                    "AND sm.site_id = %s AND sm.authorization_epoch = %s "
                    "AND sm.state = 'active' AND t.lifecycle = 'active' "
                    "AND s.state != 'archived' RETURNING id",
                    (
                        invitation_id,
                        normalized_email,
                        validated_role,
                        token_hash,
                        expires_at,
                        occurred_at,
                        principal.scope.tenant_id,
                        principal.user_id,
                        principal.role_key,
                        principal.membership_epoch,
                        principal.scope.site_id,
                        principal.site_authorization_epoch,
                    ),
                ).fetchone()
                if inserted != (invitation_id,):
                    raise InvitationDenied()
                facts = {"schema_version": 1, "role_key": validated_role}
                event_hash = _created_event_hash(
                    event_id=event_id,
                    principal=principal,
                    invitation_id=invitation_id,
                    facts=facts,
                    occurred_at=occurred_at,
                )
                connection.execute(
                    "INSERT INTO app.audit_events "
                    "(tenant_id, id, site_id, aggregate_kind, aggregate_id, "
                    "aggregate_sequence, actor_kind, actor_identifier, event_type, facts, "
                    "previous_hash, event_hash, occurred_at) "
                    "VALUES (%s, %s, %s, 'invitation', %s, 1, 'user', %s, "
                    "'invitation.created', %s, NULL, %s, %s)",
                    (
                        principal.scope.tenant_id,
                        event_id,
                        principal.scope.site_id,
                        invitation_id,
                        str(principal.user_id),
                        Jsonb(facts),
                        event_hash,
                        occurred_at,
                    ),
                )
        except UniqueViolation as error:
            if (
                error.diag.constraint_name not in _RETRYABLE_ALLOCATION_CONSTRAINTS
                or attempt_number == _INVITATION_TOKEN_ATTEMPTS - 1
            ):
                raise RuntimeError("Invitation allocation failed.") from None
            continue
        return IssuedInvitation(
            id=invitation_id,
            audit_event_id=event_id,
            tenant_id=principal.scope.tenant_id,
            site_id=principal.scope.site_id,
            email_normalized=normalized_email,
            role_key=validated_role,
            token=token,
            expires_at=expires_at,
        )
    raise RuntimeError("Invitation allocation failed.")


def normalize_invitation_email(value: object) -> str:
    """Return the deliberately restricted ASCII mailbox identity condition."""
    try:
        return normalize_ascii_mailbox(value)
    except ValueError:
        raise ValueError("Invitation email must be a bounded ASCII mailbox.") from None


def _issue_browser_invitation(
    connection,
    *,
    principal,
    email,
    role_key,
    ttl_seconds,
    session_token,
    current_recovery_generation,
    token_factory,
    invitation_id_factory,
    event_id_factory,
) -> IssuedInvitation:
    expected = {
        "outcome": "authorized",
        "tenant_id": str(principal.scope.tenant_id),
        "user_id": str(principal.user_id),
        "role_key": principal.role_key,
        "authentication_level": principal.authentication_level,
        "membership_epoch": principal.membership_epoch,
        "site_authorization_epoch": principal.site_authorization_epoch,
    }
    for attempt in range(_INVITATION_TOKEN_ATTEMPTS):
        token, token_hash = _new_invitation_token(token_factory)
        invitation_id, event_id = _new_uuid(invitation_id_factory), _new_uuid(event_id_factory)
        try:
            with _clean_transaction(connection):
                created_at = connection.execute("SELECT transaction_timestamp()").fetchone()[0]
                expires_at = created_at + timedelta(seconds=ttl_seconds)
                event_hash = _created_event_hash(
                    event_id=event_id,
                    principal=principal,
                    invitation_id=invitation_id,
                    facts={"schema_version": 1, "role_key": role_key},
                    occurred_at=created_at,
                )
                inserted = connection.execute(
                    "SELECT control.insert_owner_team_invitation(" + ",".join(["%s"] * 12) + ")",
                    (
                        hash_session_token(session_token),
                        principal.scope.site_id,
                        current_recovery_generation,
                        Jsonb(expected),
                        invitation_id,
                        email,
                        role_key,
                        token_hash,
                        expires_at,
                        event_id,
                        event_hash,
                        created_at,
                    ),
                ).fetchone()[0]
                if inserted is not True:
                    raise InvitationDenied()
        except UniqueViolation as error:
            if error.diag.constraint_name == "team_invitation_pending":
                raise InvitationConflict() from None
            if (
                error.diag.constraint_name not in _RETRYABLE_ALLOCATION_CONSTRAINTS
                or attempt == _INVITATION_TOKEN_ATTEMPTS - 1
            ):
                raise RuntimeError("Invitation allocation failed.") from None
            continue
        return IssuedInvitation(
            id=invitation_id,
            audit_event_id=event_id,
            tenant_id=principal.scope.tenant_id,
            site_id=principal.scope.site_id,
            email_normalized=email,
            role_key=role_key,
            token=token,
            expires_at=expires_at,
        )
    raise RuntimeError("Invitation allocation failed.")


def _validate_principal(principal: object) -> None:
    if (
        not isinstance(principal, AuthorizedSite)
        or not isinstance(principal.scope, Scope)
        or not isinstance(principal.user_id, UUID)
        or principal.role_key not in _GRANTABLE_ROLES
        or principal.authentication_level not in {"primary", "mfa"}
        or isinstance(principal.membership_epoch, bool)
        or not isinstance(principal.membership_epoch, int)
        or principal.membership_epoch < 1
        or isinstance(principal.site_authorization_epoch, bool)
        or not isinstance(principal.site_authorization_epoch, int)
        or principal.site_authorization_epoch < 1
    ):
        raise InvitationDenied()


def _validate_grant(inviter_role: str, role_key: object) -> str:
    if not isinstance(role_key, str) or role_key not in _GRANTABLE_ROLES[inviter_role]:
        raise InvitationDenied()
    return role_key


def _validate_ttl(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not _MINIMUM_TTL_SECONDS <= value <= _MAXIMUM_TTL_SECONDS
    ):
        raise ValueError("Invitation lifetime must be between 15 minutes and 7 days.")
    return value


def _new_invitation_token(factory: Callable[[], str]) -> tuple[str, bytes]:
    try:
        token = factory()
        return token, hash_session_token(token)
    except (InvalidOpaqueSessionToken, RuntimeError, TypeError, ValueError):
        raise RuntimeError("Invitation token source returned an invalid value.") from None


def _new_uuid(factory: Callable[[], UUID]) -> UUID:
    try:
        identifier = factory()
    except (RuntimeError, StopIteration, TypeError, ValueError):
        raise RuntimeError("Invitation identifier allocation failed.") from None
    if not isinstance(identifier, UUID) or identifier.version != 4:
        raise RuntimeError("Invitation identifier allocation failed.")
    return identifier


def _set_principal_scope(connection: Connection, principal: AuthorizedSite) -> None:
    settings = {
        "signal.tenant_id": principal.scope.tenant_id,
        "signal.site_id": principal.scope.site_id,
        "signal.actor_user_id": principal.user_id,
        "signal.membership_epoch": principal.membership_epoch,
        "signal.site_authorization_epoch": principal.site_authorization_epoch,
    }
    for name, value in settings.items():
        connection.execute("SELECT set_config(%s, %s, true)", (name, str(value)))


def _created_event_hash(
    *,
    event_id: UUID,
    principal: AuthorizedSite,
    invitation_id: UUID,
    facts: dict,
    occurred_at: datetime,
) -> bytes:
    envelope = {
        "actor_identifier": str(principal.user_id),
        "actor_kind": "user",
        "aggregate_id": str(invitation_id),
        "aggregate_kind": "invitation",
        "aggregate_sequence": 1,
        "event_id": str(event_id),
        "event_type": "invitation.created",
        "facts": facts,
        "occurred_at": occurred_at.astimezone(UTC).isoformat(timespec="microseconds"),
        "previous_hash": None,
        "site_id": str(principal.scope.site_id),
        "tenant_id": str(principal.scope.tenant_id),
    }
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).digest()
