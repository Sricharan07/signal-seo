import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from signal_core.authorization import authorize_snapshot
from signal_core.invitation_acceptance import (
    InvitationAcceptanceDenied,
)
from signal_core.invitation_acceptance import (
    accept_site_invitation as accept_with_proof,
)
from signal_core.invitation_identity_proofs import issue_invitation_identity_proof
from signal_core.invitations import issue_site_invitation
from signal_core.oidc_protocol import VerifiedOidcIdentity
from signal_core.session_issuance import issue_identity_session, issue_tenant_session


def owner_principal(admin, identity, scopes, context):
    admin.execute(
        "UPDATE app.memberships SET role_key = 'owner' WHERE tenant_id = %s AND user_id = %s",
        (scopes[0].tenant_id, context["user_id"]),
    )
    return authorize_snapshot(
        identity,
        session_token=context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=context["generation"],
    )


def verified_identity(
    now: datetime,
    *,
    email: str | None = "invitee@example.test",
    issuer: str = "https://identity.example.test/realms/signal",
    subject: str | None = None,
) -> VerifiedOidcIdentity:
    timestamp = int(now.timestamp())
    resolved_subject = subject if subject is not None else f"invited-{uuid4()}"
    return VerifiedOidcIdentity(
        issuer=issuer,
        subject=resolved_subject,
        client_id="signal-dashboard",
        issued_at=timestamp,
        expires_at=timestamp + 300,
        auth_time=timestamp - 5,
        provider_session_id="provider-session",
        authentication_context="1",
        verified_email=email,
    )


def issue_invitation(admin, api, identity, scopes, context, *, email="invitee@example.test"):
    principal = owner_principal(admin, identity, scopes, context)
    return issue_site_invitation(
        api,
        principal=principal,
        email=email,
        role_key="editor",
    )


def acceptance_proof(connection, oidc_identity, now):
    return issue_invitation_identity_proof(
        connection,
        identity=oidc_identity,
        now=now,
    )


def accept_with_verified_identity(connection, *, identity, now, **arguments):
    proof = acceptance_proof(connection, identity, now)
    return accept_with_proof(
        connection,
        identity_proof_token=proof.token,
        **arguments,
    )


def accepted_event_hash(row) -> bytes:
    payload = "\n".join(
        (
            f"actor_identifier={row[6]}",
            f"actor_kind={row[5]}",
            f"aggregate_id={row[3]}",
            f"aggregate_kind={row[2]}",
            f"aggregate_sequence={row[4]}",
            f"event_id={row[0]}",
            f"event_type={row[7]}",
            f"facts.role_key={row[8]['role_key']}",
            f"facts.schema_version={row[8]['schema_version']}",
            f"occurred_at={row[11].astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}",
            f"previous_hash={row[9].hex()}",
            f"site_id={row[1]}",
            f"tenant_id={row[12]}",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).digest()


def test_verified_identity_accepts_once_and_can_select_tenant(
    admin, api, identity, scopes, identity_context
):
    issued = issue_invitation(admin, api, identity, scopes, identity_context)
    now = datetime.now(UTC).replace(microsecond=0)
    oidc_identity = verified_identity(now)
    proof = acceptance_proof(identity, oidc_identity, now)

    accepted = accept_with_proof(
        identity,
        identity_proof_token=proof.token,
        invitation_id=issued.id,
        token=issued.token,
        display_name="Invited User",
    )

    assert accepted.invitation_id == issued.id
    assert accepted.tenant_id == issued.tenant_id
    assert accepted.site_id == issued.site_id
    assert accepted.role_key == "editor"
    assert admin.execute(
        "SELECT tenant_id, site_id FROM control.invitation_routes WHERE invitation_id = %s",
        (issued.id,),
    ).fetchone() == (issued.tenant_id, issued.site_id)
    assert admin.execute(
        "SELECT oidc_issuer, oidc_subject, display_name, contact_email, disabled_at "
        "FROM control.users WHERE id = %s",
        (accepted.user_id,),
    ).fetchone() == (
        oidc_identity.issuer,
        oidc_identity.subject,
        "Invited User",
        "invitee@example.test",
        None,
    )
    assert admin.execute(
        "SELECT id, role_key, state, authorization_epoch FROM app.memberships "
        "WHERE tenant_id = %s AND user_id = %s",
        (accepted.tenant_id, accepted.user_id),
    ).fetchone() == (accepted.membership_id, "editor", "active", 1)
    assert admin.execute(
        "SELECT id, permission_set, state, authorization_epoch FROM app.site_memberships "
        "WHERE tenant_id = %s AND site_id = %s AND user_id = %s",
        (accepted.tenant_id, accepted.site_id, accepted.user_id),
    ).fetchone() == (
        accepted.site_membership_id,
        {"permissions": ["site.snapshot.request"], "schema_version": 1},
        "active",
        1,
    )
    assert admin.execute(
        "SELECT accepted_user_id, consumed_at FROM app.invitations "
        "WHERE tenant_id = %s AND id = %s",
        (accepted.tenant_id, issued.id),
    ).fetchone() == (accepted.user_id, accepted.accepted_at)
    assert admin.execute(
        "SELECT consumed_at FROM control.invitation_identity_proofs WHERE id = %s",
        (proof.id,),
    ).fetchone() == (accepted.accepted_at,)

    events = admin.execute(
        "SELECT id, site_id, aggregate_kind, aggregate_id, aggregate_sequence, "
        "actor_kind, actor_identifier, event_type, facts, previous_hash, event_hash, "
        "occurred_at, tenant_id FROM app.audit_events "
        "WHERE tenant_id = %s AND aggregate_id = %s ORDER BY aggregate_sequence",
        (accepted.tenant_id, issued.id),
    ).fetchall()
    assert [event[4] for event in events] == [1, 2]
    assert events[1][7:9] == (
        "invitation.accepted",
        {"schema_version": 1, "role_key": "editor"},
    )
    assert events[1][6] == str(accepted.user_id)
    assert events[1][9] == events[0][10]
    assert events[1][10] == accepted_event_hash(events[1])
    assert "invitee@example.test" not in repr(events[1])
    assert issued.token not in repr(accepted)

    global_session = issue_identity_session(
        identity,
        identity=oidc_identity,
        current_recovery_generation=identity_context["generation"],
        now=now,
    )
    tenant_session = issue_tenant_session(
        identity,
        identity_session_token=global_session.token,
        requested_tenant_id=accepted.tenant_id,
        current_recovery_generation=identity_context["generation"],
        now=now,
    )
    assert tenant_session.user_id == accepted.user_id
    assert tenant_session.role_key == "editor"


def test_acceptance_replay_is_denied_without_duplicate_authority(
    admin, api, identity, scopes, identity_context
):
    issued = issue_invitation(admin, api, identity, scopes, identity_context)
    now = datetime.now(UTC).replace(microsecond=0)
    oidc_identity = verified_identity(now)
    accepted = accept_with_verified_identity(
        identity,
        identity=oidc_identity,
        invitation_id=issued.id,
        token=issued.token,
        display_name="Invited User",
        now=now,
    )

    with pytest.raises(InvitationAcceptanceDenied):
        accept_with_verified_identity(
            identity,
            identity=oidc_identity,
            invitation_id=issued.id,
            token=issued.token,
            display_name="Invited User",
            now=now,
        )
    assert admin.execute(
        "SELECT count(*) FROM app.memberships WHERE tenant_id = %s AND user_id = %s",
        (accepted.tenant_id, accepted.user_id),
    ).fetchone() == (1,)
    assert admin.execute(
        "SELECT count(*) FROM app.audit_events WHERE tenant_id = %s AND aggregate_id = %s",
        (accepted.tenant_id, issued.id),
    ).fetchone() == (2,)


@pytest.mark.parametrize("mismatch", ["invitation_token", "verified_email"])
def test_wrong_proof_never_consumes_invitation(
    admin, api, identity, scopes, identity_context, mismatch
):
    issued = issue_invitation(admin, api, identity, scopes, identity_context)
    now = datetime.now(UTC).replace(microsecond=0)
    oidc_identity = verified_identity(
        now,
        email=("other@example.test" if mismatch == "verified_email" else "invitee@example.test"),
    )
    proof = acceptance_proof(identity, oidc_identity, now)

    with pytest.raises(InvitationAcceptanceDenied):
        accept_with_proof(
            identity,
            identity_proof_token=proof.token,
            invitation_id=issued.id,
            token="w" * 43 if mismatch == "invitation_token" else issued.token,
            display_name="Invited User",
        )
    assert admin.execute(
        "SELECT consumed_at, accepted_user_id FROM app.invitations "
        "WHERE tenant_id = %s AND id = %s",
        (issued.tenant_id, issued.id),
    ).fetchone() == (None, None)
    assert admin.execute(
        "SELECT consumed_at FROM control.invitation_identity_proofs WHERE id = %s",
        (proof.id,),
    ).fetchone() == (None,)
    if mismatch == "invitation_token":
        accepted = accept_with_proof(
            identity,
            identity_proof_token=proof.token,
            invitation_id=issued.id,
            token=issued.token,
            display_name="Invited User",
        )
        assert accepted.invitation_id == issued.id
        with pytest.raises(InvitationAcceptanceDenied):
            accept_with_proof(
                identity,
                identity_proof_token=proof.token,
                invitation_id=issued.id,
                token=issued.token,
                display_name="Invited User",
            )


def test_expired_identity_proof_cannot_consume_invitation(
    admin, api, identity, scopes, identity_context
):
    issued = issue_invitation(admin, api, identity, scopes, identity_context)
    proof_token = "e" * 43
    now = datetime.now(UTC).replace(microsecond=0)
    created_at = now - timedelta(minutes=20)
    oidc_identity = verified_identity(created_at)
    admin.execute(
        "INSERT INTO control.invitation_identity_proofs "
        "(id, token_hash, oidc_issuer, oidc_subject, verified_email, "
        "identity_issued_at, identity_expires_at, expires_at, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            uuid4(),
            hashlib.sha256(proof_token.encode("ascii")).digest(),
            oidc_identity.issuer,
            oidc_identity.subject,
            oidc_identity.verified_email,
            created_at,
            created_at + timedelta(minutes=5),
            created_at + timedelta(minutes=5),
            created_at,
        ),
    )

    with pytest.raises(InvitationAcceptanceDenied):
        accept_with_proof(
            identity,
            identity_proof_token=proof_token,
            invitation_id=issued.id,
            token=issued.token,
            display_name="Invited User",
        )
    assert admin.execute(
        "SELECT consumed_at, accepted_user_id FROM app.invitations "
        "WHERE tenant_id = %s AND id = %s",
        (issued.tenant_id, issued.id),
    ).fetchone() == (None, None)


def test_matching_email_from_another_issuer_does_not_link_users(
    admin, api, identity, scopes, identity_context
):
    existing_user_id = uuid4()
    admin.execute(
        "INSERT INTO control.users "
        "(id, oidc_issuer, oidc_subject, display_name, contact_email) "
        "VALUES (%s, 'https://other-issuer.example.test', 'other-subject', "
        "'Other user', 'invitee@example.test')",
        (existing_user_id,),
    )
    issued = issue_invitation(admin, api, identity, scopes, identity_context)
    now = datetime.now(UTC).replace(microsecond=0)

    accepted = accept_with_verified_identity(
        identity,
        identity=verified_identity(now),
        invitation_id=issued.id,
        token=issued.token,
        display_name="Invited User",
        now=now,
    )

    assert accepted.user_id != existing_user_id
    assert set(
        admin.execute(
            "SELECT id, oidc_issuer FROM control.users "
            "WHERE id IN (%s, %s) AND contact_email = 'invitee@example.test'",
            (existing_user_id, accepted.user_id),
        ).fetchall()
    ) == {
        (existing_user_id, "https://other-issuer.example.test"),
        (accepted.user_id, "https://identity.example.test/realms/signal"),
    }


def test_exact_existing_identity_is_reused_without_rewriting_profile_email(
    admin, api, identity, scopes, identity_context
):
    existing_user_id = uuid4()
    oidc_issuer = "https://identity.example.test/realms/signal"
    admin.execute(
        "INSERT INTO control.users "
        "(id, oidc_issuer, oidc_subject, display_name, contact_email) "
        "VALUES (%s, %s, 'existing-subject', 'Existing User', 'old@example.test')",
        (existing_user_id, oidc_issuer),
    )
    issued = issue_invitation(admin, api, identity, scopes, identity_context)
    now = datetime.now(UTC).replace(microsecond=0)

    accepted = accept_with_verified_identity(
        identity,
        identity=verified_identity(now, issuer=oidc_issuer, subject="existing-subject"),
        invitation_id=issued.id,
        token=issued.token,
        display_name="Replacement Name",
        now=now,
    )

    assert accepted.user_id == existing_user_id
    assert admin.execute(
        "SELECT display_name, contact_email FROM control.users WHERE id = %s",
        (existing_user_id,),
    ).fetchone() == ("Existing User", "old@example.test")


@pytest.mark.parametrize("blocked_state", ["disabled", "existing_membership"])
def test_disabled_identity_or_existing_membership_does_not_consume(
    admin, api, identity, scopes, identity_context, blocked_state
):
    existing_user_id = uuid4()
    issuer = "https://identity.example.test/realms/signal"
    subject = f"blocked-{blocked_state}"
    admin.execute(
        "INSERT INTO control.users "
        "(id, oidc_issuer, oidc_subject, display_name, contact_email, disabled_at) "
        "VALUES (%s, %s, %s, 'Blocked User', 'invitee@example.test', %s)",
        (
            existing_user_id,
            issuer,
            subject,
            datetime.now(UTC) if blocked_state == "disabled" else None,
        ),
    )
    issued = issue_invitation(admin, api, identity, scopes, identity_context)
    if blocked_state == "existing_membership":
        admin.execute(
            "INSERT INTO app.memberships "
            "(tenant_id, id, user_id, role_key, state, authorization_epoch) "
            "VALUES (%s, %s, %s, 'viewer', 'active', 1)",
            (issued.tenant_id, uuid4(), existing_user_id),
        )
    now = datetime.now(UTC).replace(microsecond=0)

    with pytest.raises(InvitationAcceptanceDenied):
        accept_with_verified_identity(
            identity,
            identity=verified_identity(now, issuer=issuer, subject=subject),
            invitation_id=issued.id,
            token=issued.token,
            display_name="Blocked User",
            now=now,
        )
    assert admin.execute(
        "SELECT consumed_at FROM app.invitations WHERE tenant_id = %s AND id = %s",
        (issued.tenant_id, issued.id),
    ).fetchone() == (None,)


def test_concurrent_replay_has_exactly_one_winner(admin, api, identity, scopes, identity_context):
    issued = issue_invitation(admin, api, identity, scopes, identity_context)
    now = datetime.now(UTC).replace(microsecond=0)
    oidc_identity = verified_identity(now)

    def accept_once(index):
        with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as connection:
            try:
                return accept_with_verified_identity(
                    connection,
                    identity=oidc_identity,
                    invitation_id=issued.id,
                    token=issued.token,
                    display_name=f"Invited User {index}",
                    now=now,
                ).user_id
            except InvitationAcceptanceDenied:
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(accept_once, range(2)))
    assert sum(result is not None for result in results) == 1
    accepted_user_id = next(result for result in results if result is not None)
    assert admin.execute(
        "SELECT count(*) FROM app.memberships WHERE tenant_id = %s AND user_id = %s",
        (issued.tenant_id, accepted_user_id),
    ).fetchone() == (1,)


def test_identifier_collision_is_bounded_without_partial_authority(
    admin, api, identity, scopes, identity_context
):
    collision_id = uuid4()
    admin.execute(
        "INSERT INTO control.users "
        "(id, oidc_issuer, oidc_subject, display_name) "
        "VALUES (%s, 'https://existing.example.test', 'existing', 'Existing')",
        (collision_id,),
    )
    issued = issue_invitation(admin, api, identity, scopes, identity_context)
    now = datetime.now(UTC).replace(microsecond=0)

    with pytest.raises(RuntimeError, match="allocation failed"):
        accept_with_verified_identity(
            identity,
            identity=verified_identity(now),
            invitation_id=issued.id,
            token=issued.token,
            display_name="Invited User",
            now=now,
            user_id_factory=lambda: collision_id,
        )
    assert admin.execute(
        "SELECT consumed_at FROM app.invitations WHERE tenant_id = %s AND id = %s",
        (issued.tenant_id, issued.id),
    ).fetchone() == (None,)
    assert admin.execute(
        "SELECT count(*) FROM app.memberships WHERE tenant_id = %s",
        (issued.tenant_id,),
    ).fetchone() == (1,)


def test_audit_collision_rolls_back_user_membership_and_consumption(
    admin, api, identity, scopes, identity_context
):
    issued = issue_invitation(admin, api, identity, scopes, identity_context)
    now = datetime.now(UTC).replace(microsecond=0)
    oidc_identity = verified_identity(now, subject="audit-collision")

    with pytest.raises(RuntimeError, match="allocation failed"):
        accept_with_verified_identity(
            identity,
            identity=oidc_identity,
            invitation_id=issued.id,
            token=issued.token,
            display_name="Invited User",
            now=now,
            event_id_factory=lambda: issued.audit_event_id,
        )
    assert admin.execute(
        "SELECT count(*) FROM control.users WHERE oidc_issuer = %s AND oidc_subject = %s",
        (oidc_identity.issuer, oidc_identity.subject),
    ).fetchone() == (0,)
    assert admin.execute(
        "SELECT consumed_at, accepted_user_id FROM app.invitations "
        "WHERE tenant_id = %s AND id = %s",
        (issued.tenant_id, issued.id),
    ).fetchone() == (None, None)


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("identity_proof_token", "short"),
        ("invitation_id", "not-a-uuid"),
        ("token", "short"),
        ("display_name", " padded "),
        ("display_name", "bad\u0000name"),
    ],
)
def test_malformed_acceptance_input_fails_before_database(identity, argument, value):
    parameters = {
        "identity_proof_token": "p" * 43,
        "invitation_id": uuid4(),
        "token": "i" * 43,
        "display_name": "Invited User",
    }
    parameters[argument] = value
    identity.close()
    with pytest.raises(ValueError):
        accept_with_proof(identity, **parameters)


def test_acceptance_function_and_route_are_identity_only(admin, identity):
    signature = (
        "control.accept_site_invitation_with_proof(bytea,uuid,bytea,text,uuid,uuid,uuid,uuid)"
    )
    assert admin.execute(
        "SELECT has_function_privilege('signal_identity', %s, 'EXECUTE')",
        (signature,),
    ).fetchone() == (True,)
    for role in ["public", "signal_api", "signal_bootstrap", "signal_scheduler"]:
        assert admin.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
            (role, signature),
        ).fetchone() == (False,)
    legacy = "control.accept_site_invitation(uuid,bytea,text,text,text,text,uuid,uuid,uuid,uuid)"
    assert admin.execute(
        "SELECT has_function_privilege('signal_identity', %s, 'EXECUTE')",
        (legacy,),
    ).fetchone() == (False,)
    assert not admin.execute(
        "SELECT has_table_privilege('signal_identity', 'control.invitation_routes', 'SELECT')"
    ).fetchone()[0]
    assert not admin.execute(
        "SELECT has_table_privilege('signal_identity', 'app.invitations', 'UPDATE')"
    ).fetchone()[0]
    assert not admin.execute(
        "SELECT has_table_privilege('signal_identity', 'control.users', 'INSERT')"
    ).fetchone()[0]
    assert {
        column
        for (column,) in admin.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'control' AND table_name = 'invitation_routes'"
        )
    } == {"invitation_id", "tenant_id", "site_id", "created_at"}
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        identity.execute("SELECT * FROM control.invitation_routes")


def test_consumed_invitation_remains_immutable(admin, api, identity, scopes, identity_context):
    issued = issue_invitation(admin, api, identity, scopes, identity_context)
    now = datetime.now(UTC).replace(microsecond=0)
    accepted = accept_with_verified_identity(
        identity,
        identity=verified_identity(now),
        invitation_id=issued.id,
        token=issued.token,
        display_name="Invited User",
        now=now,
    )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.invitations SET consumed_at = consumed_at WHERE tenant_id = %s AND id = %s",
            (accepted.tenant_id, issued.id),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "DELETE FROM app.invitations WHERE tenant_id = %s AND id = %s",
            (accepted.tenant_id, issued.id),
        )
