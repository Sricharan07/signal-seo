"""Consume one invitation together with a short-lived verified-identity proof."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation

from signal_core.database import _clean_transaction
from signal_core.session_tokens import InvalidOpaqueSessionToken, hash_session_token

_ALLOCATION_ATTEMPTS = 3
_RETRYABLE_ID_CONSTRAINTS = frozenset(
    {
        "audit_events_pkey",
        "memberships_pkey",
        "site_memberships_pkey",
        "users_pkey",
    }
)
_AUTHORITY_CONFLICT_CONSTRAINTS = frozenset(
    {
        "memberships_tenant_id_user_id_key",
        "site_memberships_tenant_id_site_id_user_id_key",
    }
)


class InvitationAcceptanceDenied(Exception):
    """The invitation and verified identity cannot create authority."""


@dataclass(frozen=True)
class AcceptedInvitation:
    invitation_id: UUID
    user_id: UUID
    membership_id: UUID
    site_membership_id: UUID
    audit_event_id: UUID
    tenant_id: UUID
    site_id: UUID
    role_key: str
    accepted_at: datetime


def accept_site_invitation(
    connection: Connection,
    *,
    identity_proof_token: object,
    invitation_id: object,
    token: object,
    display_name: object,
    user_id_factory: Callable[[], UUID] = uuid4,
    membership_id_factory: Callable[[], UUID] = uuid4,
    site_membership_id_factory: Callable[[], UUID] = uuid4,
    event_id_factory: Callable[[], UUID] = uuid4,
) -> AcceptedInvitation:
    """Consume both browser proof and invitation in the authority transaction."""
    validated_invitation_id = _validate_uuid(invitation_id, "Invitation ID")
    validated_name = _validate_display_name(display_name)
    identity_proof_hash = _hash_opaque_token(identity_proof_token, "Identity proof")
    invitation_token_hash = _hash_opaque_token(token, "Invitation token")
    factories = (
        user_id_factory,
        membership_id_factory,
        site_membership_id_factory,
        event_id_factory,
    )
    if not all(callable(factory) for factory in factories):
        raise ValueError("Invitation acceptance factories must be callable.")

    for attempt_number in range(_ALLOCATION_ATTEMPTS):
        candidate_user_id, membership_id, site_membership_id, event_id = tuple(
            _new_uuid(factory) for factory in factories
        )
        try:
            with _clean_transaction(connection):
                row = connection.execute(
                    "SELECT accepted_user_id, accepted_tenant_id, accepted_site_id, "
                    "accepted_role_key, accepted_at, accepted_event_hash "
                    "FROM control.accept_site_invitation_with_proof(%s, %s, %s, %s, "
                    "%s, %s, %s, %s)",
                    (
                        identity_proof_hash,
                        validated_invitation_id,
                        invitation_token_hash,
                        validated_name,
                        candidate_user_id,
                        membership_id,
                        site_membership_id,
                        event_id,
                    ),
                ).fetchone()
                if row is None:
                    raise InvitationAcceptanceDenied()
                event_hash = row[5]
                if not isinstance(event_hash, bytes) or len(event_hash) != 32:
                    raise RuntimeError("Invitation acceptance failed.")
        except UniqueViolation as error:
            constraint = error.diag.constraint_name
            if constraint in _AUTHORITY_CONFLICT_CONSTRAINTS:
                raise InvitationAcceptanceDenied() from None
            if constraint not in _RETRYABLE_ID_CONSTRAINTS:
                raise RuntimeError("Invitation acceptance failed.") from None
            if attempt_number == _ALLOCATION_ATTEMPTS - 1:
                raise RuntimeError("Invitation acceptance allocation failed.") from None
            continue
        accepted_user_id, tenant_id, site_id, role_key, accepted_at, _ = row
        return AcceptedInvitation(
            invitation_id=validated_invitation_id,
            user_id=accepted_user_id,
            membership_id=membership_id,
            site_membership_id=site_membership_id,
            audit_event_id=event_id,
            tenant_id=tenant_id,
            site_id=site_id,
            role_key=role_key,
            accepted_at=accepted_at,
        )
    raise RuntimeError("Invitation acceptance allocation failed.")


def _validate_display_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not 1 <= len(value) <= 200
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ValueError("Display name must be a bounded printable value.")
    return value


def _validate_uuid(value: object, label: str) -> UUID:
    if not isinstance(value, UUID) or value.version != 4:
        raise ValueError(f"{label} must be a UUIDv4.")
    return value


def _hash_opaque_token(value: object, label: str) -> bytes:
    try:
        return hash_session_token(value)
    except (InvalidOpaqueSessionToken, TypeError):
        raise ValueError(f"{label} is invalid.") from None


def _new_uuid(factory: Callable[[], UUID]) -> UUID:
    try:
        return _validate_uuid(factory(), "Allocated identifier")
    except (RuntimeError, StopIteration, TypeError, ValueError):
        raise RuntimeError("Invitation acceptance identifier allocation failed.") from None
