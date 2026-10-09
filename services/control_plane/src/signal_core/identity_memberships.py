"""List only memberships reachable from one valid pre-tenant identity session."""

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

from signal_core.database import _clean_transaction
from signal_core.session_issuance import InvalidIdentitySession, validate_recovery_generation
from signal_core.session_tokens import InvalidOpaqueSessionToken, hash_session_token

_ROLES = frozenset({"viewer", "analyst", "editor", "approver", "admin", "owner"})


@dataclass(frozen=True)
class IdentityMembership:
    tenant_id: UUID
    tenant_name: str
    role_key: str


def list_identity_memberships(
    connection: Connection,
    *,
    identity_session_token: object,
    current_recovery_generation: object,
) -> tuple[IdentityMembership, ...]:
    """Return a deterministic active-membership snapshot for one global session."""
    try:
        token_hash = hash_session_token(identity_session_token)
    except InvalidOpaqueSessionToken:
        raise InvalidIdentitySession() from None
    generation = validate_recovery_generation(current_recovery_generation)

    with _clean_transaction(connection):
        connection.execute(
            "SELECT set_config('signal.identity_session_hash', %s, true)",
            (token_hash.hex(),),
        )
        rows = connection.execute(
            "SELECT identity_user_id, member_tenant_id, tenant_name, member_role_key "
            "FROM control.list_identity_memberships(%s, %s)",
            (token_hash, generation),
        ).fetchall()
        if not rows:
            raise InvalidIdentitySession()

        user_id = rows[0][0]
        if not isinstance(user_id, UUID) or any(row[0] != user_id for row in rows):
            raise RuntimeError("Identity membership discovery failed.")
        if rows == [(user_id, None, None, None)]:
            return ()

        memberships = tuple(_membership_from_row(row) for row in rows)
        if len({membership.tenant_id for membership in memberships}) != len(memberships):
            raise RuntimeError("Identity membership discovery failed.")
        return tuple(sorted(memberships, key=lambda item: (item.tenant_name, str(item.tenant_id))))


def _membership_from_row(row: tuple[object, ...]) -> IdentityMembership:
    if len(row) != 4:
        raise RuntimeError("Identity membership discovery failed.")
    _, tenant_id, tenant_name, role_key = row
    if (
        not isinstance(tenant_id, UUID)
        or not isinstance(tenant_name, str)
        or tenant_name != tenant_name.strip()
        or not 1 <= len(tenant_name) <= 200
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in tenant_name)
        or not isinstance(role_key, str)
        or role_key not in _ROLES
    ):
        raise RuntimeError("Identity membership discovery failed.")
    return IdentityMembership(tenant_id=tenant_id, tenant_name=tenant_name, role_key=role_key)
