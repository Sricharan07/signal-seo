"""Owner product path over the existing hash-only invitation foundations."""

from uuid import UUID, uuid4

from psycopg import Connection

from signal_core.authorization import AuthorizedSite
from signal_core.database import Scope, _clean_transaction
from signal_core.invitations import InvitationDenied, IssuedInvitation, issue_site_invitation
from signal_core.session_tokens import hash_session_token


def issue_owner_team_invitation(
    connection: Connection,
    *,
    session_token: str,
    site_id: UUID,
    current_recovery_generation: str,
    email: str,
    role_key: str,
) -> IssuedInvitation:
    with _clean_transaction(connection):
        authority = connection.execute(
            "SELECT control.team_owner(%s,%s,%s,true)",
            (hash_session_token(session_token), site_id, current_recovery_generation),
        ).fetchone()[0]
        if authority is None:
            raise InvitationDenied()
        principal = AuthorizedSite(
            scope=Scope(UUID(authority["tenant_id"]), site_id),
            user_id=UUID(authority["user_id"]),
            role_key=authority["role_key"],
            authentication_level=authority["authentication_level"],
            membership_epoch=authority["membership_epoch"],
            site_authorization_epoch=authority["site_authorization_epoch"],
        )
    return issue_site_invitation(
        connection,
        principal=principal,
        email=email,
        role_key=role_key,
        session_token=session_token,
        current_recovery_generation=current_recovery_generation,
    )


def read_owner_team(
    connection: Connection,
    *,
    session_token: str,
    site_id: UUID,
    current_recovery_generation: str,
) -> dict:
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT control.read_owner_team(%s,%s,%s)",
            (hash_session_token(session_token), site_id, current_recovery_generation),
        ).fetchone()
        if not row or row[0] is None:
            raise InvitationDenied()
        return row[0]


def revoke_owner_team_invitation(
    connection: Connection,
    *,
    session_token: str,
    site_id: UUID,
    current_recovery_generation: str,
    invitation_id: UUID,
    event_id: UUID | None = None,
) -> dict:
    """Commit only a current pending invitation's restrictive transition and outbox."""
    if not isinstance(invitation_id, UUID) or invitation_id.version != 4:
        raise InvitationDenied()
    event_id = uuid4() if event_id is None else event_id
    if not isinstance(event_id, UUID) or event_id.version != 4:
        raise InvitationDenied()
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT control.revoke_owner_team_invitation(%s,%s,%s,%s,%s)",
            (
                hash_session_token(session_token),
                site_id,
                current_recovery_generation,
                invitation_id,
                event_id,
            ),
        ).fetchone()
        if not row or row[0] is None:
            raise InvitationDenied()
        return row[0]
