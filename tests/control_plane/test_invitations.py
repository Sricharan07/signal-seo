import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from uuid import uuid4

import psycopg
import pytest
from signal_core.authorization import AuthorizedSite, authorize_snapshot
from signal_core.database import scoped_transaction
from signal_core.invitations import (
    InvitationConflict,
    InvitationDenied,
    issue_site_invitation,
    normalize_invitation_email,
)


def invitation_principal(admin, identity, scopes, context, role_key="owner") -> AuthorizedSite:
    admin.execute(
        "UPDATE app.memberships SET role_key = %s WHERE tenant_id = %s AND user_id = %s",
        (role_key, scopes[0].tenant_id, context["user_id"]),
    )
    return authorize_snapshot(
        identity,
        session_token=context["session_token"],
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=context["generation"],
    )


def canonical_event_hash(row) -> bytes:
    envelope = {
        "actor_identifier": row[6],
        "actor_kind": row[5],
        "aggregate_id": str(row[3]),
        "aggregate_kind": row[2],
        "aggregate_sequence": row[4],
        "event_id": str(row[0]),
        "event_type": row[7],
        "facts": row[8],
        "occurred_at": row[11].astimezone(UTC).isoformat(timespec="microseconds"),
        "previous_hash": None,
        "site_id": str(row[1]),
        "tenant_id": str(row[12]),
    }
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).digest()


def test_owner_invitation_is_hash_only_and_atomically_audited(
    admin, api, identity, scopes, identity_context
):
    principal = invitation_principal(admin, identity, scopes, identity_context)
    token = "i" * 43
    issued = issue_site_invitation(
        api,
        principal=principal,
        email="Invitee@Example.TEST",
        role_key="admin",
        ttl_seconds=900,
        token_factory=lambda: token,
    )

    assert issued.tenant_id == scopes[0].tenant_id
    assert issued.site_id == scopes[0].site_id
    assert issued.email_normalized == "invitee@example.test"
    assert issued.role_key == "admin"
    assert token not in repr(issued)
    assert "invitee@example.test" not in repr(issued)
    stored = admin.execute(
        "SELECT email_normalized, role_key, token_hash, inviter_user_id, "
        "inviter_membership_epoch, inviter_site_authorization_epoch, "
        "expires_at, created_at FROM app.invitations WHERE tenant_id = %s AND id = %s",
        (issued.tenant_id, issued.id),
    ).fetchone()
    assert stored[:6] == (
        "invitee@example.test",
        "admin",
        hashlib.sha256(token.encode("ascii")).digest(),
        identity_context["user_id"],
        1,
        1,
    )
    assert (stored[6] - stored[7]).total_seconds() == 900
    assert token.encode("ascii") not in stored[2]

    event = admin.execute(
        "SELECT id, site_id, aggregate_kind, aggregate_id, aggregate_sequence, "
        "actor_kind, actor_identifier, event_type, facts, previous_hash, event_hash, "
        "occurred_at, tenant_id FROM app.audit_events WHERE aggregate_id = %s",
        (issued.id,),
    ).fetchone()
    assert event[:10] == (
        issued.audit_event_id,
        issued.site_id,
        "invitation",
        issued.id,
        1,
        "user",
        str(identity_context["user_id"]),
        "invitation.created",
        {"schema_version": 1, "role_key": "admin"},
        None,
    )
    assert event[10] == canonical_event_hash(event)
    assert "invitee@example.test" not in repr(event)


def test_api_role_cannot_read_invitation_tokens_or_audit_stream(
    admin, api, identity, scopes, identity_context
):
    principal = invitation_principal(admin, identity, scopes, identity_context)
    issued = issue_site_invitation(
        api,
        principal=principal,
        email="reader@example.test",
        role_key="viewer",
    )

    with scoped_transaction(api, scopes[0]):
        assert api.execute(
            "SELECT id, email_normalized FROM app.invitations WHERE id = %s",
            (issued.id,),
        ).fetchone() == (issued.id, "reader@example.test")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), api.transaction():
            api.execute("SELECT token_hash FROM app.invitations WHERE id = %s", (issued.id,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("SELECT * FROM app.audit_events")


def test_invitation_and_audit_records_are_immutable(admin, api, identity, scopes, identity_context):
    principal = invitation_principal(admin, identity, scopes, identity_context)
    issued = issue_site_invitation(
        api,
        principal=principal,
        email="immutable@example.test",
        role_key="viewer",
    )

    for statement, identifier in [
        ("DELETE FROM app.invitations WHERE tenant_id = %s AND id = %s", issued.id),
        (
            "UPDATE app.audit_events SET event_type = event_type WHERE tenant_id = %s AND id = %s",
            issued.audit_event_id,
        ),
    ]:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(statement, (issued.tenant_id, identifier))


def test_current_authority_and_grant_ceiling_are_rechecked(
    admin, api, identity, scopes, identity_context
):
    stale = invitation_principal(admin, identity, scopes, identity_context)
    admin.execute(
        "UPDATE app.memberships SET authorization_epoch = 2 WHERE tenant_id = %s AND user_id = %s",
        (stale.scope.tenant_id, stale.user_id),
    )
    with pytest.raises(InvitationDenied):
        issue_site_invitation(
            api,
            principal=stale,
            email="stale@example.test",
            role_key="viewer",
        )

    current_admin = invitation_principal(
        admin, identity, scopes, identity_context, role_key="admin"
    )
    with pytest.raises(InvitationDenied):
        issue_site_invitation(
            api,
            principal=current_admin,
            email="admin@example.test",
            role_key="admin",
        )
    analyst = invitation_principal(admin, identity, scopes, identity_context, role_key="analyst")
    api.close()
    with pytest.raises(InvitationDenied):
        issue_site_invitation(
            api,
            principal=analyst,
            email="denied@example.test",
            role_key="viewer",
        )


def test_duplicate_recipient_is_serialized_to_one_live_invitation(
    admin, identity, scopes, identity_context
):
    principal = invitation_principal(admin, identity, scopes, identity_context)

    def issue_once(index):
        with psycopg.connect(os.environ["SIGNAL_TEST_API_DSN"], autocommit=True) as connection:
            try:
                return issue_site_invitation(
                    connection,
                    principal=principal,
                    email="duplicate@example.test",
                    role_key="viewer",
                    token_factory=lambda: ("a" if index == 0 else "b") * 43,
                ).id
            except InvitationConflict:
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(issue_once, range(2)))
    assert sum(result is not None for result in results) == 1
    assert admin.execute(
        "SELECT count(*) FROM app.invitations WHERE tenant_id = %s "
        "AND email_normalized = 'duplicate@example.test'",
        (principal.scope.tenant_id,),
    ).fetchone() == (1,)
    assert admin.execute(
        "SELECT count(*) FROM app.audit_events WHERE tenant_id = %s",
        (principal.scope.tenant_id,),
    ).fetchone() == (1,)


def test_token_collisions_retry_without_partial_invitation(
    admin, api, identity, scopes, identity_context
):
    principal = invitation_principal(admin, identity, scopes, identity_context)
    token = "c" * 43
    first = issue_site_invitation(
        api,
        principal=principal,
        email="first@example.test",
        role_key="viewer",
        token_factory=lambda: token,
    )

    with pytest.raises(RuntimeError, match="allocation failed"):
        issue_site_invitation(
            api,
            principal=principal,
            email="second@example.test",
            role_key="viewer",
            token_factory=lambda: token,
        )
    assert admin.execute(
        "SELECT array_agg(id ORDER BY id), count(*) FROM app.invitations WHERE tenant_id = %s",
        (principal.scope.tenant_id,),
    ).fetchone() == ([first.id], 1)


def test_audit_failure_rolls_back_invitation(admin, api, identity, scopes, identity_context):
    principal = invitation_principal(admin, identity, scopes, identity_context)
    first = issue_site_invitation(
        api,
        principal=principal,
        email="event-one@example.test",
        role_key="viewer",
    )
    candidate_id = uuid4()

    with pytest.raises(RuntimeError, match="allocation failed"):
        issue_site_invitation(
            api,
            principal=principal,
            email="event-two@example.test",
            role_key="viewer",
            invitation_id_factory=lambda: candidate_id,
            event_id_factory=lambda: first.audit_event_id,
        )
    assert admin.execute(
        "SELECT count(*) FROM app.invitations WHERE tenant_id = %s AND id = %s",
        (principal.scope.tenant_id, candidate_id),
    ).fetchone() == (0,)


@pytest.mark.parametrize(
    "email",
    [None, "", " user@example.test", "a..b@example.test", "a@localhost", "caf\u00e9@example.test"],
)
def test_invalid_email_fails_before_database(api, email):
    api.close()
    with pytest.raises(ValueError):
        normalize_invitation_email(email)


def test_database_rejects_local_parts_the_product_parser_rejects(
    api, admin, identity, scopes, identity_context
):
    principal = invitation_principal(admin, identity, scopes, identity_context)
    for email in (".leading@example.test", "trailing.@example.test"):
        with pytest.raises(psycopg.errors.CheckViolation), api.transaction():
            for name, value in {
                "signal.tenant_id": principal.scope.tenant_id,
                "signal.site_id": principal.scope.site_id,
                "signal.actor_user_id": principal.user_id,
                "signal.membership_epoch": principal.membership_epoch,
                "signal.site_authorization_epoch": principal.site_authorization_epoch,
            }.items():
                api.execute("SELECT set_config(%s, %s, true)", (name, str(value)))
            assert api.execute("SELECT app.lock_invitation_authority()").fetchone() == (True,)
            api.execute(
                "INSERT INTO app.invitations "
                "(tenant_id, id, site_id, email_normalized, role_key, token_hash, "
                "inviter_user_id, inviter_membership_epoch, "
                "inviter_site_authorization_epoch, expires_at, created_at) "
                "VALUES (%s, %s, %s, %s, 'viewer', %s, %s, %s, %s, "
                "transaction_timestamp() + interval '1 day', transaction_timestamp())",
                (
                    principal.scope.tenant_id,
                    uuid4(),
                    principal.scope.site_id,
                    email,
                    hashlib.sha256(email.encode("ascii")).digest(),
                    principal.user_id,
                    principal.membership_epoch,
                    principal.site_authorization_epoch,
                ),
            )


@pytest.mark.parametrize("ttl", [True, 899, 604801])
def test_invalid_ttl_fails_before_database(admin, api, identity, scopes, identity_context, ttl):
    principal = invitation_principal(admin, identity, scopes, identity_context)
    api.close()
    with pytest.raises(ValueError):
        issue_site_invitation(
            api,
            principal=principal,
            email="valid@example.test",
            role_key="viewer",
            ttl_seconds=ttl,
        )


@pytest.mark.parametrize("token", [None, "short", "x" * 44, "contains+symbol"])
def test_invalid_token_source_fails_before_database(
    admin, api, identity, scopes, identity_context, token
):
    principal = invitation_principal(admin, identity, scopes, identity_context)
    api.close()
    with pytest.raises(RuntimeError, match="token source"):
        issue_site_invitation(
            api,
            principal=principal,
            email="valid@example.test",
            role_key="viewer",
            token_factory=lambda: token,
        )


def test_invitation_role_has_exact_column_privileges(admin):
    invitation_select = {
        column
        for (column,) in admin.execute(
            "SELECT column_name FROM information_schema.column_privileges "
            "WHERE grantee = 'signal_api' AND table_schema = 'app' "
            "AND table_name = 'invitations' AND privilege_type = 'SELECT'"
        )
    }
    assert "token_hash" not in invitation_select
    assert invitation_select == {
        "tenant_id",
        "id",
        "site_id",
        "email_normalized",
        "role_key",
        "inviter_user_id",
        "inviter_membership_epoch",
        "inviter_site_authorization_epoch",
        "expires_at",
        "consumed_at",
        "revoked_at",
        "created_at",
    }
    audit_insert = {
        column
        for (column,) in admin.execute(
            "SELECT column_name FROM information_schema.column_privileges "
            "WHERE grantee = 'signal_api' AND table_schema = 'app' "
            "AND table_name = 'audit_events' AND privilege_type = 'INSERT'"
        )
    }
    assert len(audit_insert) == 13
    assert not admin.execute(
        "SELECT has_table_privilege('signal_api', 'app.audit_events', 'SELECT')"
    ).fetchone()[0]


def test_invitation_context_functions_are_api_only(admin):
    for function in [
        "app.current_actor_user_id()",
        "app.current_membership_epoch()",
        "app.current_site_authorization_epoch()",
        "app.lock_invitation_authority()",
    ]:
        assert admin.execute(
            "SELECT has_function_privilege('signal_api', %s, 'EXECUTE')",
            (function,),
        ).fetchone()[0]
        for role in ["public", "signal_identity", "signal_bootstrap", "signal_scheduler"]:
            assert not admin.execute(
                "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                (role, function),
            ).fetchone()[0]


def test_authority_lock_blocks_concurrent_membership_change(
    admin, api, identity, scopes, identity_context
):
    principal = invitation_principal(admin, identity, scopes, identity_context)
    with api.transaction():
        for name, value in {
            "signal.tenant_id": principal.scope.tenant_id,
            "signal.site_id": principal.scope.site_id,
            "signal.actor_user_id": principal.user_id,
            "signal.membership_epoch": principal.membership_epoch,
            "signal.site_authorization_epoch": principal.site_authorization_epoch,
        }.items():
            api.execute("SELECT set_config(%s, %s, true)", (name, str(value)))
        assert api.execute("SELECT app.lock_invitation_authority()").fetchone() == (True,)
        with psycopg.connect(os.environ["SIGNAL_TEST_ADMIN_DSN"], autocommit=True) as changer:
            changer.execute("SET lock_timeout = '100ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                changer.execute(
                    "UPDATE app.memberships SET role_key = 'analyst' "
                    "WHERE tenant_id = %s AND user_id = %s",
                    (principal.scope.tenant_id, principal.user_id),
                )

    admin.execute(
        "UPDATE app.memberships SET role_key = 'analyst' WHERE tenant_id = %s AND user_id = %s",
        (principal.scope.tenant_id, principal.user_id),
    )


def test_residual_invitation_scope_contaminates_connection(
    admin, api, identity, scopes, identity_context
):
    principal = invitation_principal(admin, identity, scopes, identity_context)
    api.execute("SELECT set_config('signal.actor_user_id', %s, false)", (str(uuid4()),))
    with pytest.raises(ValueError, match="residual session scope"):
        issue_site_invitation(
            api,
            principal=principal,
            email="residual@example.test",
            role_key="viewer",
        )
