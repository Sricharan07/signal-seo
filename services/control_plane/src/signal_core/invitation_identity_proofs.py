"""Issue a short-lived opaque proof from one freshly verified OIDC identity."""

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

_TOKEN_ATTEMPTS = 3
_PROOF_TTL = timedelta(minutes=10)


class InvitationIdentityProofDenied(Exception):
    """The provider identity cannot become an invitation-acceptance proof."""


@dataclass(frozen=True)
class IssuedInvitationIdentityProof:
    id: UUID
    token: str = field(repr=False)
    expires_at: datetime


def _proof_token() -> str:
    return secrets.token_urlsafe(32)


def issue_invitation_identity_proof(
    connection: Connection,
    *,
    identity: object,
    now: datetime | None = None,
    token_factory: Callable[[], str] = _proof_token,
    proof_id_factory: Callable[[], UUID] = uuid4,
) -> IssuedInvitationIdentityProof:
    """Persist a hash-only proof that grants no user, tenant, or site authority."""
    current = _validate_now(now)
    issuer, subject, email, issued_at, identity_expires_at = _verified_projection(identity, current)
    if not callable(token_factory) or not callable(proof_id_factory):
        raise ValueError("Invitation identity proof factories must be callable.")
    expires_at = min(identity_expires_at, current + _PROOF_TTL)
    if expires_at <= current:
        raise InvitationIdentityProofDenied()

    for attempt_number in range(_TOKEN_ATTEMPTS):
        proof_id = _new_uuid(proof_id_factory)
        raw_token, token_hash = _new_token(token_factory)
        try:
            with _clean_transaction(connection):
                connection.execute(
                    "SELECT set_config('signal.invitation_identity_proof_hash', %s, true)",
                    (token_hash.hex(),),
                )
                connection.execute(
                    "INSERT INTO control.invitation_identity_proofs "
                    "(id, token_hash, oidc_issuer, oidc_subject, verified_email, "
                    "identity_issued_at, identity_expires_at, expires_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        proof_id,
                        token_hash,
                        issuer,
                        subject,
                        email,
                        issued_at,
                        identity_expires_at,
                        expires_at,
                    ),
                )
        except UniqueViolation as error:
            if (
                error.diag.constraint_name
                not in {
                    "invitation_identity_proofs_pkey",
                    "invitation_identity_proofs_token_hash_key",
                }
                or attempt_number == _TOKEN_ATTEMPTS - 1
            ):
                raise RuntimeError("Invitation identity proof allocation failed.") from None
            continue
        return IssuedInvitationIdentityProof(
            id=proof_id,
            token=raw_token,
            expires_at=expires_at,
        )
    raise RuntimeError("Invitation identity proof allocation failed.")


def cleanup_invitation_identity_proofs(
    connection: Connection,
    *,
    batch_size: object = 500,
) -> int:
    """Delete at most one bounded batch after proof authority has expired."""
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or not 1 <= batch_size <= 1000
    ):
        raise ValueError("Invitation identity proof cleanup batch must be between 1 and 1000.")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT control.cleanup_invitation_identity_proofs(%s)",
            (batch_size,),
        ).fetchone()
        if row is None or isinstance(row[0], bool) or not isinstance(row[0], int):
            raise RuntimeError("Invitation identity proof cleanup failed.")
        return row[0]


def _verified_projection(
    identity: object, current: datetime
) -> tuple[str, str, str, datetime, datetime]:
    if (
        not isinstance(identity, VerifiedOidcIdentity)
        or not _bounded(identity.issuer, 2048)
        or not _bounded(identity.subject, 512)
        or not isinstance(identity.verified_email, str)
    ):
        raise InvitationIdentityProofDenied()
    try:
        email = normalize_ascii_mailbox(identity.verified_email)
        if email != identity.verified_email:
            raise ValueError
        issued_at = datetime.fromtimestamp(identity.issued_at, UTC)
        expires_at = datetime.fromtimestamp(identity.expires_at, UTC)
    except (OSError, OverflowError, TypeError, ValueError):
        raise InvitationIdentityProofDenied() from None
    if (
        isinstance(identity.issued_at, bool)
        or isinstance(identity.expires_at, bool)
        or not isinstance(identity.issued_at, int)
        or not isinstance(identity.expires_at, int)
        or issued_at > current + timedelta(seconds=30)
        or issued_at < current - timedelta(minutes=11)
        or expires_at <= current
        or expires_at <= issued_at
    ):
        raise InvitationIdentityProofDenied()
    return identity.issuer, identity.subject, email, issued_at, expires_at


def _validate_now(value: datetime | None) -> datetime:
    current = value if value is not None else datetime.now(UTC)
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Invitation identity proof requires an aware current time.")
    return current.astimezone(UTC)


def _bounded(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and not any(ord(character) < 0x20 for character in value)
    )


def _new_token(factory: Callable[[], str]) -> tuple[str, bytes]:
    try:
        token = factory()
        return token, hash_session_token(token)
    except (InvalidOpaqueSessionToken, RuntimeError, StopIteration, TypeError):
        raise RuntimeError("Invitation identity proof token source failed.") from None


def _new_uuid(factory: Callable[[], UUID]) -> UUID:
    try:
        value = factory()
    except (RuntimeError, StopIteration, TypeError):
        raise RuntimeError("Invitation identity proof identifier source failed.") from None
    if not isinstance(value, UUID) or value.version != 4:
        raise RuntimeError("Invitation identity proof identifier source failed.")
    return value
