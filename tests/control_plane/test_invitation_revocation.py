"""Real PostgreSQL restrictive transitions, competing acceptance and replay."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege, ObjectNotInPrerequisiteState, UniqueViolation
from signal_core.authorization import AuthorizedSite, authorize_snapshot
from signal_core.database import scoped_transaction
from signal_core.invitation_acceptance import InvitationAcceptanceDenied, accept_site_invitation
from signal_core.invitation_identity_proofs import issue_invitation_identity_proof
from signal_core.invitations import InvitationDenied, _set_principal_scope, issue_site_invitation
from signal_core.team_invitations import (
    issue_owner_team_invitation,
    read_owner_team,
    revoke_owner_team_invitation,
)
from test_invitation_acceptance import accepted_event_hash, verified_identity
from test_team_invitations import team_owner as owner_fixture

team_owner = owner_fixture


@pytest.fixture
def pending(identity, team_owner):
    return issue_owner_team_invitation(
        identity, **team_owner, email="invitee@example.test", role_key="viewer"
    )


def proof(identity):
    now = datetime.now(UTC)
    return issue_invitation_identity_proof(identity, identity=verified_identity(now), now=now)


def accept(identity, invitation, identity_proof):
    return accept_site_invitation(
        identity,
        identity_proof_token=identity_proof.token,
        invitation_id=invitation.id,
        token=invitation.token,
        display_name="Invited teammate",
    )


def test_revocation_is_atomic_hashed_audit_and_no_membership_grant(
    admin, identity, team_owner, pending, identity_context
):
    result = revoke_owner_team_invitation(identity, **team_owner, invitation_id=pending.id)
    assert result["durability"] == "AUTHORITY_DURABILITY_PENDING"
    with pytest.raises(InvitationAcceptanceDenied):
        accept(identity, pending, proof(identity))
    team = read_owner_team(identity, **team_owner)
    assert team["can_revoke"] is False
    assert team["invitations"][0]["state"] == "revoked"
    assert team["invitations"][0]["revocation_durability"] == "pending"
    assert len(team["members"]) == 1
    event = admin.execute(
        "SELECT id,site_id,aggregate_kind,aggregate_id,aggregate_sequence,actor_kind,"
        "actor_identifier,event_type,facts,previous_hash,event_hash,occurred_at,tenant_id "
        "FROM app.audit_events WHERE aggregate_id=%s AND aggregate_sequence=2",
        (pending.id,),
    ).fetchone()
    assert event[7] == "invitation.revoked"
    assert event[6] == str(identity_context["user_id"])
    assert event[10] == accepted_event_hash(event)
    assert admin.execute(
        "SELECT target_kind,restriction_kind,effective_epoch "
        "FROM control.authority_restriction_outbox "
        "WHERE event_id=%s",
        (event[0],),
    ).fetchone() == ("invitation", "invitation_revoked", 1)
    assert pending.token not in str(event)
    assert pending.email_normalized not in str(event)


@pytest.mark.parametrize(
    "failure",
    ["viewer", "admin", "stale", "primary", "unverified", "session", "generation", "other_site"],
)
def test_revocation_denies_without_current_fresh_verified_owner(
    admin, identity, scopes, identity_context, team_owner, pending, failure
):
    if failure in {"viewer", "admin"}:
        admin.execute(
            "UPDATE app.memberships SET role_key=%s WHERE id=%s",
            (failure, identity_context["membership_id"]),
        )
    elif failure == "stale":
        auth_time = admin.execute("SELECT clock_timestamp()-interval '6 minutes'").fetchone()[0]
        for table, identifier in [
            ("app.sessions", identity_context["tenant_session_id"]),
            ("control.identity_sessions", identity_context["identity_session_id"]),
        ]:
            admin.execute(f"UPDATE {table} SET auth_time=%s WHERE id=%s", (auth_time, identifier))
    elif failure == "primary":
        admin.execute(
            "UPDATE control.identity_sessions SET authentication_level='primary' WHERE id=%s",
            (identity_context["identity_session_id"],),
        )
        admin.execute(
            "UPDATE app.sessions SET mfa_level='primary' WHERE id=%s",
            (identity_context["tenant_session_id"],),
        )
    elif failure == "unverified":
        admin.execute(
            "UPDATE app.sites SET ownership_status='unverified' WHERE id=%s", (pending.site_id,)
        )
    elif failure == "session":
        admin.execute(
            "UPDATE app.sessions SET revoked_at=clock_timestamp() WHERE id=%s",
            (identity_context["tenant_session_id"],),
        )
    elif failure == "generation":
        team_owner["current_recovery_generation"] = "different-generation"
    else:
        team_owner["site_id"] = scopes[1].site_id
    with pytest.raises(InvitationDenied):
        revoke_owner_team_invitation(identity, **team_owner, invitation_id=pending.id)
    assert admin.execute(
        "SELECT revoked_at FROM app.invitations WHERE id=%s", (pending.id,)
    ).fetchone() == (None,)


def test_another_tenants_real_invitation_is_not_revocable(
    admin, api, identity, scopes, identity_context, team_owner
):
    scope = scopes[2]
    admin.execute(
        "INSERT INTO app.memberships(tenant_id,id,user_id,role_key,state,authorization_epoch) "
        "VALUES(%s,%s,%s,'owner','active',1)",
        (scope.tenant_id, uuid4(), identity_context["user_id"]),
    )
    admin.execute(
        "INSERT INTO app.site_memberships(tenant_id,site_id,id,user_id,permission_set,"
        "authorization_epoch,state) VALUES(%s,%s,%s,%s,"
        '\'{"permissions":["site.snapshot.request"],"schema_version":1}\',1,\'active\')',
        (scope.tenant_id, scope.site_id, uuid4(), identity_context["user_id"]),
    )
    principal = AuthorizedSite(scope, identity_context["user_id"], "owner", "mfa", 1, 1)
    invitation = issue_site_invitation(
        api, principal=principal, email="other@example.test", role_key="viewer"
    )
    with pytest.raises(InvitationDenied):
        revoke_owner_team_invitation(identity, **team_owner, invitation_id=invitation.id)
    assert admin.execute(
        "SELECT revoked_at FROM app.invitations WHERE id=%s", (invitation.id,)
    ).fetchone() == (None,)


@pytest.mark.parametrize("state", ["accepted", "revoked"])
def test_terminal_invitation_and_replayed_revocation_are_denied(
    identity, team_owner, pending, state
):
    if state == "accepted":
        accept(identity, pending, proof(identity))
    else:
        revoke_owner_team_invitation(identity, **team_owner, invitation_id=pending.id)
    with pytest.raises(InvitationDenied):
        revoke_owner_team_invitation(identity, **team_owner, invitation_id=pending.id)
    assert read_owner_team(identity, **team_owner)["invitations"][0]["state"] == state


def test_expired_invitation_cannot_be_revoked(api, identity, team_owner, pending):
    principal = authorize_snapshot(
        identity,
        session_token=team_owner["session_token"],
        requested_site_id=team_owner["site_id"],
        current_recovery_generation=team_owner["current_recovery_generation"],
    )
    expired = uuid4()
    with scoped_transaction(api, principal.scope):
        _set_principal_scope(api, principal)
        api.execute(
            "INSERT INTO app.invitations(tenant_id,id,site_id,email_normalized,role_key,token_hash,"
            "inviter_user_id,inviter_membership_epoch,inviter_site_authorization_epoch,created_at,"
            "expires_at) SELECT tenant_id,%s,site_id,'expired@example.test',role_key,%s,"
            "inviter_user_id,inviter_membership_epoch,inviter_site_authorization_epoch,"
            "transaction_timestamp()-interval '2 days',transaction_timestamp()-interval '1 day' "
            "FROM app.invitations WHERE id=%s",
            (expired, sha256(b"expired invitation fixture").digest(), pending.id),
        )
    with pytest.raises(InvitationDenied):
        revoke_owner_team_invitation(identity, **team_owner, invitation_id=expired)
    assert (
        next(
            i
            for i in read_owner_team(identity, **team_owner)["invitations"]
            if i["id"] == str(expired)
        )["state"]
        == "expired"
    )


def test_audit_collision_rolls_back_revocation_and_outbox(admin, identity, team_owner, pending):
    with pytest.raises(UniqueViolation):
        revoke_owner_team_invitation(
            identity, **team_owner, invitation_id=pending.id, event_id=pending.audit_event_id
        )
    assert admin.execute(
        "SELECT revoked_at FROM app.invitations WHERE id=%s", (pending.id,)
    ).fetchone() == (None,)
    assert admin.execute(
        "SELECT count(*) FROM control.authority_restriction_outbox WHERE target_id=%s",
        (pending.id,),
    ).fetchone() == (0,)


@pytest.mark.parametrize("role", ["api", "identity", "scheduler"])
def test_runtime_cannot_update_or_resurrect_invitation(
    admin, api, identity, scheduler, team_owner, pending, role
):
    connection = {"api": api, "identity": identity, "scheduler": scheduler}[role]
    with pytest.raises(InsufficientPrivilege):
        connection.execute(
            "UPDATE app.invitations SET revoked_at=clock_timestamp() WHERE id=%s", (pending.id,)
        )
    if role == "scheduler":
        with pytest.raises(InsufficientPrivilege):
            revoke_owner_team_invitation(scheduler, **team_owner, invitation_id=pending.id)
    revoke_owner_team_invitation(identity, **team_owner, invitation_id=pending.id)
    with pytest.raises(ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.invitations SET revoked_at=NULL,revoker_user_id=NULL WHERE id=%s",
            (pending.id,),
        )


def test_can_revoke_is_false_when_owner_mfa_or_site_proof_is_stale(
    admin, identity, team_owner, pending
):
    assert read_owner_team(identity, **team_owner)["can_revoke"] is True
    admin.execute(
        "UPDATE app.sites SET ownership_status='unverified' WHERE id=%s", (pending.site_id,)
    )
    assert read_owner_team(identity, **team_owner)["can_revoke"] is False


def test_database_unavailable_grants_nothing(admin, team_owner, pending):
    with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as connection:
        admin.execute("SELECT pg_terminate_backend(%s)", (connection.info.backend_pid,))
        with pytest.raises(psycopg.OperationalError):
            revoke_owner_team_invitation(connection, **team_owner, invitation_id=pending.id)
    assert admin.execute(
        "SELECT revoked_at FROM app.invitations WHERE id=%s", (pending.id,)
    ).fetchone() == (None,)


@pytest.mark.parametrize("winner", ["revoke", "accept"])
def test_revoke_accept_race_has_exactly_one_terminal_event(
    admin, identity, team_owner, pending, winner
):
    identity_proof = proof(identity)
    name = "invitation-race-" + uuid4().hex

    def loser():
        with psycopg.connect(
            os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True, application_name=name
        ) as connection:
            if winner == "revoke":
                with pytest.raises(InvitationAcceptanceDenied):
                    accept(connection, pending, identity_proof)
            else:
                with pytest.raises(InvitationDenied):
                    revoke_owner_team_invitation(connection, **team_owner, invitation_id=pending.id)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with identity.transaction():
            if winner == "revoke":
                row = identity.execute(
                    "SELECT control.revoke_owner_team_invitation(%s,%s,%s,%s,%s)",
                    (
                        sha256(team_owner["session_token"].encode()).digest(),
                        pending.site_id,
                        team_owner["current_recovery_generation"],
                        pending.id,
                        uuid4(),
                    ),
                ).fetchone()
                assert row[0]
            else:
                row = identity.execute(
                    "SELECT * FROM control.accept_site_invitation_with_proof("
                    "%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        sha256(identity_proof.token.encode()).digest(),
                        pending.id,
                        sha256(pending.token.encode()).digest(),
                        "Invited teammate",
                        uuid4(),
                        uuid4(),
                        uuid4(),
                        uuid4(),
                    ),
                ).fetchone()
                assert row
            future = pool.submit(loser)
            deadline = time.monotonic() + 10
            while not admin.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=%s "
                "AND wait_event_type='Lock')",
                (name,),
            ).fetchone()[0]:
                assert time.monotonic() < deadline, (
                    "competing transition never waited on the real lock"
                )
                time.sleep(0.01)
        future.result(timeout=10)
    team = read_owner_team(identity, **team_owner)
    assert team["invitations"][0]["state"] == ("revoked" if winner == "revoke" else "accepted")
    assert len(team["members"]) == (1 if winner == "revoke" else 2)
    assert admin.execute(
        "SELECT count(*) FROM app.audit_events WHERE aggregate_id=%s AND aggregate_sequence=2",
        (pending.id,),
    ).fetchone() == (1,)


def test_restore_denial_tombstone_blocks_acceptance_without_granting(identity, team_owner, pending):
    with psycopg.connect(
        os.environ["SIGNAL_TEST_AUTHORITY_DISPATCHER_DSN"], autocommit=True
    ) as dispatcher:
        event, stream = uuid4(), uuid4()
        args = (event, pending.id, 1, stream, 1, "a" * 64)
        dispatcher.execute(
            "SELECT control.apply_invitation_authority_denial(%s,%s,%s,%s,%s,%s)", args
        )
        dispatcher.execute(
            "SELECT control.apply_invitation_authority_denial(%s,%s,%s,%s,%s,%s)", args
        )
        with pytest.raises(UniqueViolation):
            dispatcher.execute(
                "SELECT control.apply_invitation_authority_denial(%s,%s,%s,%s,%s,%s)",
                (*args[:-1], "b" * 64),
            )
    with pytest.raises(InvitationAcceptanceDenied):
        accept(identity, pending, proof(identity))
    with pytest.raises(InvitationDenied):
        revoke_owner_team_invitation(identity, **team_owner, invitation_id=pending.id)
    team = read_owner_team(identity, **team_owner)
    assert team["can_revoke"] is False
    assert team["invitations"][0]["state"] == "revoked"
    assert team["invitations"][0]["revocation_durability"] == "acknowledged"
    assert len(team["members"]) == 1


def test_reference_guard_keeps_every_owner_revocation_exemption(admin):
    """Recreating the shared guard must not drop another migration's exemption."""
    admin.execute(
        "CREATE TEMP TABLE reference_guard_rows AS "
        "SELECT * FROM control.platform_events WITH NO DATA"
    )
    admin.execute(
        "CREATE TRIGGER reference_guard BEFORE INSERT ON pg_temp.reference_guard_rows "
        "FOR EACH ROW EXECUTE FUNCTION control.validate_platform_event_reference()"
    )
    for event_type in ("webflow.binding.revoked", "github.pr.revoked", "invitation.revoked"):
        assert admin.execute(
            "INSERT INTO pg_temp.reference_guard_rows (id,event_type,object_id) "
            "VALUES(%s,%s,%s) RETURNING event_type",
            (uuid4(), event_type, uuid4()),
        ).fetchone() == (event_type,)
