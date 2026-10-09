"""Real PostgreSQL owner issuance, scope isolation and atomic acceptance."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from psycopg.errors import InsufficientPrivilege, LockNotAvailable
from psycopg.types.json import Jsonb
from signal_core.authorization import AuthorizationDenied, InvalidSession, authorize_snapshot
from signal_core.database import _clean_transaction, scoped_transaction
from signal_core.invitation_acceptance import InvitationAcceptanceDenied, accept_site_invitation
from signal_core.invitation_identity_proofs import issue_invitation_identity_proof
from signal_core.invitations import (
    InvitationConflict,
    InvitationDenied,
    _created_event_hash,
    _set_principal_scope,
    issue_site_invitation,
)
from signal_core.team_invitations import issue_owner_team_invitation, read_owner_team
from test_invitation_acceptance import verified_identity


@pytest.fixture
def team_owner(admin, scopes, identity_context):
    scope = scopes[0]
    admin.execute(
        "UPDATE app.memberships SET role_key='owner' WHERE tenant_id=%s AND user_id=%s",
        (scope.tenant_id, identity_context["user_id"]),
    )
    admin.execute(
        "UPDATE app.sites SET ownership_status='verified' WHERE tenant_id=%s AND id=%s",
        (scope.tenant_id, scope.site_id),
    )
    admin.execute(
        "UPDATE control.identity_sessions SET authentication_level='mfa' WHERE id=%s",
        (identity_context["identity_session_id"],),
    )
    admin.execute(
        "UPDATE app.sessions SET mfa_level='mfa' WHERE id=%s",
        (identity_context["tenant_session_id"],),
    )
    return dict(
        session_token=identity_context["session_token"],
        site_id=scope.site_id,
        current_recovery_generation=identity_context["generation"],
    )


@pytest.mark.parametrize("role", ["viewer", "analyst", "editor", "approver", "admin"])
def test_owner_issues_supported_role_and_reads_hash_free_team(api, team_owner, role):
    issued = issue_owner_team_invitation(
        api, **team_owner, email="Invitee@example.test", role_key=role
    )
    team = read_owner_team(api, **team_owner)
    assert team["site_id"] == str(issued.site_id)
    assert team["can_revoke"] is True
    assert team["truncated"] is False
    assert team["members"][0]["role_key"] == "owner"
    assert team["invitations"][0]["state"] == "pending"
    assert issued.token not in str(team)
    assert "token_hash" not in str(team)
    with pytest.raises(InvitationConflict):
        issue_owner_team_invitation(api, **team_owner, email="invitee@example.test", role_key=role)


@pytest.mark.parametrize(
    "failure",
    [
        "viewer",
        "admin",
        "primary",
        "stale",
        "future",
        "unverified",
        "other_tenant",
        "other_site",
        "recovery",
        "revoked_session",
        "membership",
        "site_grant",
    ],
)
def test_issuance_denies_without_current_owner_mfa_verified_scope(
    admin, api, scopes, identity_context, team_owner, failure
):
    if failure in {"viewer", "admin"}:
        admin.execute(
            "UPDATE app.memberships SET role_key=%s WHERE id=%s",
            (failure, identity_context["membership_id"]),
        )
    elif failure == "primary":
        admin.execute(
            "UPDATE control.identity_sessions SET authentication_level='primary' WHERE id=%s",
            (identity_context["identity_session_id"],),
        )
        admin.execute(
            "UPDATE app.sessions SET mfa_level='primary' WHERE id=%s",
            (identity_context["tenant_session_id"],),
        )
    elif failure in {"stale", "future"}:
        now = admin.execute("SELECT transaction_timestamp()").fetchone()[0]
        auth_time = now + timedelta(minutes=-6 if failure == "stale" else 1)
        for table, identifier in [
            ("control.identity_sessions", identity_context["identity_session_id"]),
            ("app.sessions", identity_context["tenant_session_id"]),
        ]:
            admin.execute(
                f"UPDATE {table} SET auth_time=%s,last_seen_at=%s WHERE id=%s",
                (auth_time, max(now, auth_time), identifier),
            )
    elif failure == "unverified":
        admin.execute(
            "UPDATE app.sites SET ownership_status='unverified' WHERE id=%s",
            (team_owner["site_id"],),
        )
    elif failure in {"other_tenant", "other_site"}:
        team_owner["site_id"] = scopes[2 if failure == "other_tenant" else 1].site_id
    elif failure == "recovery":
        team_owner["current_recovery_generation"] = "other-generation"
    elif failure == "revoked_session":
        admin.execute(
            "UPDATE app.sessions SET revoked_at=transaction_timestamp() WHERE id=%s",
            (identity_context["tenant_session_id"],),
        )
    elif failure == "membership":
        admin.execute(
            "UPDATE app.memberships SET authorization_epoch=authorization_epoch+1 WHERE id=%s",
            (identity_context["membership_id"],),
        )
        # Changing authority does not turn an owner into a non-owner; revoke it explicitly.
        admin.execute(
            "UPDATE app.memberships SET state='removed' WHERE id=%s",
            (identity_context["membership_id"],),
        )
    else:
        admin.execute(
            "UPDATE app.site_memberships SET state='removed' WHERE id=%s",
            (identity_context["site_membership_id"],),
        )
    with pytest.raises((InvitationDenied, AuthorizationDenied, InvalidSession)):
        issue_owner_team_invitation(
            api, **team_owner, email="invitee@example.test", role_key="viewer"
        )
    assert admin.execute(
        "SELECT count(*) FROM app.invitations WHERE inviter_user_id=%s",
        (identity_context["user_id"],),
    ).fetchone() == (0,)


@pytest.mark.parametrize("role", ["owner", "service", "Owner", "", "viewer,owner"])
def test_issuance_role_field_cannot_escalate(api, team_owner, role):
    with pytest.raises(InvitationDenied):
        issue_owner_team_invitation(api, **team_owner, email="invitee@example.test", role_key=role)


def test_in_transaction_guard_rejects_stale_principal(
    admin, api, identity, identity_context, team_owner
):
    principal = authorize_snapshot(
        identity,
        session_token=team_owner["session_token"],
        requested_site_id=team_owner["site_id"],
        current_recovery_generation=team_owner["current_recovery_generation"],
    )
    admin.execute(
        "UPDATE app.memberships SET role_key='admin' WHERE id=%s",
        (identity_context["membership_id"],),
    )
    with pytest.raises(InvitationDenied):
        issue_site_invitation(
            api,
            principal=principal,
            email="invitee@example.test",
            role_key="viewer",
            session_token=team_owner["session_token"],
            current_recovery_generation=team_owner["current_recovery_generation"],
        )


@pytest.mark.parametrize("failure", ["viewer", "other_tenant"])
def test_team_read_is_owner_only_and_tenant_isolated(
    admin, api, scopes, identity_context, team_owner, failure
):
    if failure == "viewer":
        admin.execute(
            "UPDATE app.memberships SET role_key='viewer' WHERE id=%s",
            (identity_context["membership_id"],),
        )
    else:
        team_owner["site_id"] = scopes[2].site_id
    with pytest.raises(InvitationDenied):
        read_owner_team(api, **team_owner)


def test_team_acceptance_lists_member_and_rejects_replay(api, identity, team_owner):
    issued = issue_owner_team_invitation(
        api, **team_owner, email="invitee@example.test", role_key="editor"
    )
    now = datetime.now(UTC)
    proof = issue_invitation_identity_proof(identity, identity=verified_identity(now), now=now)
    arguments = dict(
        identity_proof_token=proof.token,
        invitation_id=issued.id,
        token=issued.token,
        display_name="Invited teammate",
    )
    accepted = accept_site_invitation(identity, **arguments)
    team = read_owner_team(api, **team_owner)
    assert team["invitations"][0]["state"] == "accepted"
    assert any(
        m["user_id"] == str(accepted.user_id) and m["role_key"] == "editor" for m in team["members"]
    )
    with pytest.raises(InvitationAcceptanceDenied):
        accept_site_invitation(identity, **arguments)


@pytest.mark.parametrize("failure", ["different_identity", "forged_token", "wrong_invitation"])
def test_acceptance_grants_nothing_to_mismatched_credentials(api, identity, team_owner, failure):
    issued = issue_owner_team_invitation(
        api, **team_owner, email="invitee@example.test", role_key="viewer"
    )
    now = datetime.now(UTC)
    proof = issue_invitation_identity_proof(
        identity,
        identity=verified_identity(
            now,
            email="different@example.test"
            if failure == "different_identity"
            else "invitee@example.test",
        ),
        now=now,
    )
    with pytest.raises(InvitationAcceptanceDenied):
        accept_site_invitation(
            identity,
            identity_proof_token=proof.token,
            invitation_id=uuid4() if failure == "wrong_invitation" else issued.id,
            token="f" * 43 if failure == "forged_token" else issued.token,
            display_name="Invited teammate",
        )
    assert len(read_owner_team(api, **team_owner)["members"]) == 1


def test_team_functions_have_no_scheduler_privilege(scheduler, team_owner):
    with pytest.raises(InsufficientPrivilege):
        read_owner_team(scheduler, **team_owner)


def test_owner_guard_locks_session_identity_and_user_against_concurrent_revocation(
    admin, identity, identity_context, team_owner
):
    with _clean_transaction(identity):
        assert identity.execute(
            "SELECT control.team_owner(%s,%s,%s,true)",
            (
                sha256(team_owner["session_token"].encode()).digest(),
                team_owner["site_id"],
                team_owner["current_recovery_generation"],
            ),
        ).fetchone()[0]
        for table, identifier in [
            ("app.sessions", identity_context["tenant_session_id"]),
            ("control.identity_sessions", identity_context["identity_session_id"]),
            ("control.users", identity_context["user_id"]),
        ]:
            with pytest.raises(LockNotAvailable), _clean_transaction(admin):
                admin.execute(
                    f"SELECT id FROM {table} WHERE id=%s FOR UPDATE NOWAIT", (identifier,)
                )


def test_composed_identity_role_issues_without_direct_table_write_privileges(identity, team_owner):
    issued = issue_owner_team_invitation(
        identity, **team_owner, email="invitee@example.test", role_key="analyst"
    )
    assert read_owner_team(identity, **team_owner)["invitations"][0]["id"] == str(issued.id)
    with pytest.raises(InsufficientPrivilege):
        identity.execute("INSERT INTO app.invitations DEFAULT VALUES")
    with pytest.raises(InsufficientPrivilege):
        identity.execute("SELECT token_hash FROM app.invitations")


def test_browser_issuance_audit_failure_rolls_back_invitation(identity, team_owner):
    first = issue_owner_team_invitation(
        identity, **team_owner, email="first@example.test", role_key="viewer"
    )
    principal = authorize_snapshot(
        identity,
        session_token=team_owner["session_token"],
        requested_site_id=team_owner["site_id"],
        current_recovery_generation=team_owner["current_recovery_generation"],
    )
    with pytest.raises(RuntimeError, match="allocation failed"):
        issue_site_invitation(
            identity,
            principal=principal,
            email="second@example.test",
            role_key="viewer",
            session_token=team_owner["session_token"],
            current_recovery_generation=team_owner["current_recovery_generation"],
            event_id_factory=lambda: first.audit_event_id,
        )
    assert [i["id"] for i in read_owner_team(identity, **team_owner)["invitations"]] == [
        str(first.id)
    ]


def test_expired_audited_invitation_grants_nothing(admin, api, identity, team_owner):
    principal = authorize_snapshot(
        identity,
        session_token=team_owner["session_token"],
        requested_site_id=team_owner["site_id"],
        current_recovery_generation=team_owner["current_recovery_generation"],
    )
    now = admin.execute("SELECT transaction_timestamp()").fetchone()[0]
    created = now - timedelta(days=2)
    invitation_id, event_id, token = uuid4(), uuid4(), "e" * 43
    facts = {"schema_version": 1, "role_key": "viewer"}
    with scoped_transaction(api, principal.scope):
        _set_principal_scope(api, principal)
        api.execute(
            "INSERT INTO app.invitations (tenant_id,id,site_id,email_normalized,role_key,"
            "token_hash,inviter_user_id,inviter_membership_epoch,inviter_site_authorization_epoch,"
            "expires_at,created_at) VALUES (%s,%s,%s,'invitee@example.test','viewer',"
            "%s,%s,%s,%s,%s,%s)",
            (
                principal.scope.tenant_id,
                invitation_id,
                principal.scope.site_id,
                sha256(token.encode()).digest(),
                principal.user_id,
                principal.membership_epoch,
                principal.site_authorization_epoch,
                created + timedelta(days=1),
                created,
            ),
        )
        api.execute(
            "INSERT INTO app.audit_events (tenant_id,id,site_id,aggregate_kind,aggregate_id,"
            "aggregate_sequence,actor_kind,actor_identifier,event_type,facts,"
            "event_hash,occurred_at) "
            "VALUES (%s,%s,%s,'invitation',%s,1,'user',%s,'invitation.created',%s,%s,%s)",
            (
                principal.scope.tenant_id,
                event_id,
                principal.scope.site_id,
                invitation_id,
                str(principal.user_id),
                Jsonb(facts),
                _created_event_hash(
                    event_id=event_id,
                    principal=principal,
                    invitation_id=invitation_id,
                    facts=facts,
                    occurred_at=created,
                ),
                created,
            ),
        )
    proof = issue_invitation_identity_proof(identity, identity=verified_identity(now), now=now)
    with pytest.raises(InvitationAcceptanceDenied):
        accept_site_invitation(
            identity,
            identity_proof_token=proof.token,
            invitation_id=invitation_id,
            token=token,
            display_name="Invited teammate",
        )
    team = read_owner_team(api, **team_owner)
    assert team["invitations"][0]["state"] == "expired"
    assert len(team["members"]) == 1
