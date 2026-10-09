"""Validate an opaque tenant session and derive one currently granted site scope."""

import re
from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

from signal_core.database import Scope, _clean_transaction
from signal_core.session_tokens import InvalidOpaqueSessionToken, hash_session_token

_GENERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


class InvalidSession(Exception):
    """The opaque session is malformed, expired, revoked, stale, or disabled."""


class AuthorizationDenied(Exception):
    """The current identity lacks the requested site grant; details stay private."""


def validated_human_inputs(
    *, session_token: object, requested_site_id: object, current_recovery_generation: object
) -> bytes:
    """Validate input shapes only; each operation still resolves current authority."""
    try:
        token_hash = hash_session_token(session_token)
    except (InvalidOpaqueSessionToken, TypeError):
        raise InvalidSession() from None
    if not isinstance(requested_site_id, UUID):
        raise AuthorizationDenied()
    if (
        not isinstance(current_recovery_generation, str)
        or _GENERATION.fullmatch(current_recovery_generation) is None
    ):
        raise RuntimeError("A validated external recovery generation is required.")
    return token_hash


@dataclass(frozen=True)
class AuthorizedSite:
    scope: Scope
    user_id: UUID
    role_key: str
    authentication_level: str
    membership_epoch: int
    site_authorization_epoch: int


def authorize_snapshot(
    connection: Connection,
    *,
    session_token: str,
    requested_site_id: UUID,
    current_recovery_generation: str,
) -> AuthorizedSite:
    """Resolve trusted scope from server-side state; never accept a tenant ID from the client."""
    try:
        token_hash = hash_session_token(session_token)
    except InvalidOpaqueSessionToken:
        raise InvalidSession() from None
    if not isinstance(requested_site_id, UUID):
        raise AuthorizationDenied()
    if (
        not isinstance(current_recovery_generation, str)
        or _GENERATION.fullmatch(current_recovery_generation) is None
    ):
        raise RuntimeError("A validated external recovery generation is required.")

    with _clean_transaction(connection):
        connection.execute(
            "SELECT set_config('signal.session_hash', %s, true)", (token_hash.hex(),)
        )
        authority = connection.execute(
            "SELECT outcome, tenant_id, user_id, role_key, authentication_level, "
            "membership_epoch, site_authorization_epoch "
            "FROM control.resolve_snapshot_authority(%s, %s, %s)",
            (token_hash, requested_site_id, current_recovery_generation),
        ).fetchone()
        if authority is None or authority[0] == "invalid_session":
            raise InvalidSession()
        if authority[0] != "authorized":
            raise AuthorizationDenied()
        return AuthorizedSite(
            scope=Scope(authority[1], requested_site_id),
            user_id=authority[2],
            role_key=authority[3],
            authentication_level=authority[4],
            membership_epoch=authority[5],
            site_authorization_epoch=authority[6],
        )
