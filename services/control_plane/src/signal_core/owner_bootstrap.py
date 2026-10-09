"""Create one first owner from a fresh, already verified OIDC/OTP login proof."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import Connection

from signal_core.database import _clean_transaction
from signal_core.oidc_login import OidcClientRegistration
from signal_core.oidc_protocol import VerifiedOidcIdentity
from signal_core.self_host_config import CLIENT
from signal_core.session_issuance import SessionPolicy, _validate_verified_identity

BOOTSTRAP_POLICY = SessionPolicy(
    primary_acr_values=frozenset({"0"}),
    mfa_acr_values=frozenset({"1", "2"}),
    required_mfa_methods=frozenset({"otp"}),
    maximum_auth_age_seconds=300,
)


class OwnerBootstrapRejected(Exception):
    """Bootstrap is create-only; an unknown or different installation is denied."""


@dataclass(frozen=True)
class OwnerBootstrapIntent:
    user_id: UUID
    tenant_id: UUID
    membership_id: UUID
    subject: str
    registration: OidcClientRegistration
    workspace_name: str
    home_region: str
    approved_at: int
    expires_at: int


def bootstrap_owner(
    connection: Connection,
    *,
    intent: OwnerBootstrapIntent,
    identity: VerifiedOidcIdentity,
    attempt_id: UUID,
    nonce_hash: bytes,
    now: datetime | None = None,
) -> bool:
    """Return False only for an exact committed retry, never repair existing authority."""
    current = now or datetime.now(UTC)
    if (
        not isinstance(intent, OwnerBootstrapIntent)
        or any(
            not isinstance(value, UUID)
            for value in (intent.user_id, intent.tenant_id, intent.membership_id, attempt_id)
        )
        or not isinstance(nonce_hash, bytes)
        or len(nonce_hash) != 32
    ):
        raise OwnerBootstrapRejected()
    if (
        not isinstance(identity, VerifiedOidcIdentity)
        or identity.issuer != intent.registration.issuer
        or identity.client_id != CLIENT
        or intent.registration.client_id != CLIENT
        or identity.subject != intent.subject
    ):
        raise OwnerBootstrapRejected()
    with _clean_transaction(connection):
        connection.execute(
            "LOCK TABLE control.users, app.tenants, app.memberships, "
            "control.tenant_directory, control.oidc_login_attempts "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM control.users), "
            "(SELECT count(*) FROM app.tenants), (SELECT count(*) FROM app.memberships), "
            "(SELECT count(*) FROM control.tenant_directory)"
        ).fetchone()
        if counts != (0, 0, 0, 0):
            row = connection.execute(
                "SELECT u.oidc_issuer, u.oidc_subject, u.disabled_at, t.name, t.home_region, "
                "t.lifecycle, m.role_key, m.state, d.lifecycle "
                "FROM control.users u JOIN app.memberships m ON m.user_id=u.id "
                "JOIN app.tenants t ON t.tenant_id=m.tenant_id "
                "JOIN control.tenant_directory d ON d.tenant_id=t.tenant_id "
                "WHERE u.id=%s AND t.tenant_id=%s AND m.id=%s",
                (intent.user_id, intent.tenant_id, intent.membership_id),
            ).fetchone()
            if counts == (1, 1, 1, 1) and row == (
                intent.registration.issuer,
                intent.subject,
                None,
                intent.workspace_name,
                intent.home_region,
                "active",
                "owner",
                "active",
                "active",
            ):
                return False
            raise OwnerBootstrapRejected()
        if (
            type(intent.approved_at) is not int
            or type(intent.expires_at) is not int
            or not intent.approved_at <= int(current.timestamp()) < intent.expires_at
            or not 0 < intent.expires_at - intent.approved_at <= 3600
            or identity.issued_at < intent.approved_at
        ):
            raise OwnerBootstrapRejected()
        _validate_verified_identity(identity, BOOTSTRAP_POLICY, current)
        proof = connection.execute(
            "SELECT oidc_issuer, client_id, redirect_uri, purpose, nonce_hash, "
            "created_at, consumed_at, expires_at "
            "FROM control.oidc_login_attempts WHERE id=%s FOR UPDATE",
            (attempt_id,),
        ).fetchone()
        if (
            proof is None
            or proof[:4]
            != (intent.registration.issuer, CLIENT, intent.registration.redirect_uri, "login")
            or bytes(proof[4]) != nonce_hash
            or proof[6] is None
            or not datetime.fromtimestamp(intent.approved_at, UTC)
            <= proof[5]
            <= proof[6]
            <= current
            < proof[7]
        ):
            raise OwnerBootstrapRejected()
        connection.execute(
            "INSERT INTO control.users(id, oidc_issuer, oidc_subject, display_name, contact_email) "
            "VALUES(%s,%s,%s,'Owner',%s)",
            (intent.user_id, identity.issuer, identity.subject, identity.verified_email),
        )
        connection.execute("SET LOCAL ROLE signal_bootstrap")
        connection.execute(
            "SELECT set_config('signal.tenant_id', %s, true)", (str(intent.tenant_id),)
        )
        connection.execute(
            "INSERT INTO app.tenants(tenant_id,name,home_region) VALUES(%s,%s,%s)",
            (intent.tenant_id, intent.workspace_name, intent.home_region),
        )
        connection.execute(
            "INSERT INTO control.tenant_directory VALUES(%s,'active',1)", (intent.tenant_id,)
        )
        connection.execute(
            "INSERT INTO app.memberships(tenant_id,id,user_id,role_key,state,authorization_epoch) "
            "VALUES(%s,%s,%s,'owner','active',1)",
            (intent.tenant_id, intent.membership_id, intent.user_id),
        )
        connection.execute("RESET ROLE")
    return True
