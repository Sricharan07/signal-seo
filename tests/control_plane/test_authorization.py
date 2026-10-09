import hashlib

import psycopg
import pytest
from signal_core.authorization import (
    AuthorizationDenied,
    InvalidSession,
    authorize_snapshot,
)


def authorize(identity, scopes, context, **overrides):
    arguments = {
        "session_token": context["session_token"],
        "requested_site_id": scopes[0].site_id,
        "current_recovery_generation": context["generation"],
        **overrides,
    }
    return authorize_snapshot(identity, **arguments)


def test_valid_session_derives_current_tenant_site_and_actor(identity, scopes, identity_context):
    result = authorize(identity, scopes, identity_context)
    assert result.scope.tenant_id == scopes[0].tenant_id
    assert result.scope.site_id == scopes[0].site_id
    assert result.user_id == identity_context["user_id"]
    assert result.role_key == "analyst"
    assert result.authentication_level == "primary"
    assert result.membership_epoch == 1
    assert result.site_authorization_epoch == 1
    assert identity.execute("SELECT count(*) FROM app.sessions").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("app.sessions", "revoked_at", "now()"),
        ("control.identity_sessions", "revoked_at", "now()"),
        ("control.users", "disabled_at", "now()"),
        ("control.identity_sessions", "recovery_generation", "'stale-generation'"),
        ("app.sessions", "mfa_level", "'mfa'"),
    ],
)
def test_invalid_session_states_are_indistinguishable(
    admin, identity, scopes, identity_context, table, column, value
):
    key = "id" if table.startswith("control") else "id"
    identifier = {
        "app.sessions": identity_context["tenant_session_id"],
        "control.identity_sessions": identity_context["identity_session_id"],
        "control.users": identity_context["user_id"],
    }[table]
    admin.execute(f"UPDATE {table} SET {column} = {value} WHERE {key} = %s", (identifier,))
    with pytest.raises(InvalidSession) as failure:
        authorize(identity, scopes, identity_context)
    assert str(failure.value) == ""


def test_external_generation_mismatch_invalidates_otherwise_live_session(
    identity, scopes, identity_context
):
    with pytest.raises(InvalidSession):
        authorize(identity, scopes, identity_context, current_recovery_generation="new-generation")


@pytest.mark.parametrize(
    ("table", "identifier_key"),
    [
        ("app.sessions", "tenant_session_id"),
        ("control.identity_sessions", "identity_session_id"),
    ],
)
def test_expired_session_layers_are_rejected(
    admin, identity, scopes, identity_context, table, identifier_key
):
    admin.execute(
        f"UPDATE {table} SET expires_at = auth_time + interval '1 microsecond' WHERE id = %s",
        (identity_context[identifier_key],),
    )
    with pytest.raises(InvalidSession):
        authorize(identity, scopes, identity_context)


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("app.memberships", "state", "'suspended'"),
        ("app.site_memberships", "state", "'removed'"),
        ("app.sites", "state", "'archived'"),
        ("app.tenants", "lifecycle", "'suspended'"),
    ],
)
def test_current_membership_and_site_state_are_rechecked(
    admin, identity, scopes, identity_context, table, column, value
):
    predicates = {
        "app.memberships": ("id", identity_context["membership_id"]),
        "app.site_memberships": ("id", identity_context["site_membership_id"]),
        "app.sites": ("id", scopes[0].site_id),
        "app.tenants": ("tenant_id", scopes[0].tenant_id),
    }
    key, identifier = predicates[table]
    admin.execute(f"UPDATE {table} SET {column} = {value} WHERE {key} = %s", (identifier,))
    with pytest.raises(AuthorizationDenied) as failure:
        authorize(identity, scopes, identity_context)
    assert str(failure.value) == ""


def test_wrong_site_is_denied_without_scope_details(identity, scopes, identity_context):
    with pytest.raises(AuthorizationDenied) as failure:
        authorize(identity, scopes, identity_context, requested_site_id=scopes[1].site_id)
    assert str(failure.value) == ""
    assert authorize(identity, scopes, identity_context).scope.site_id == scopes[0].site_id


@pytest.mark.parametrize("token", ["", "short", "x" * 42, "x" * 44, "contains+symbol"])
def test_malformed_token_is_rejected_before_database_access(
    identity, scopes, identity_context, token
):
    identity.close()
    with pytest.raises(InvalidSession):
        authorize(identity, scopes, identity_context, session_token=token)


def test_missing_recovery_anchor_fails_before_database_access(identity, scopes, identity_context):
    identity.close()
    with pytest.raises(RuntimeError, match="external recovery generation"):
        authorize(identity, scopes, identity_context, current_recovery_generation="")


def test_identity_role_cannot_enumerate_or_mutate_authority(identity, scopes, identity_context):
    assert identity.execute("SELECT count(*) FROM app.sessions").fetchone()[0] == 0
    assert identity.execute("SELECT count(*) FROM app.memberships").fetchone()[0] == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege), identity.transaction():
        identity.execute(
            "UPDATE control.identity_sessions SET revoked_at = now() WHERE id = %s",
            (identity_context["identity_session_id"],),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege), identity.transaction():
        identity.execute(
            "INSERT INTO control.users (id, oidc_issuer, oidc_subject, display_name) "
            "VALUES (gen_random_uuid(), 'spoofed', 'spoofed', 'Spoofed')"
        )


def test_raw_session_token_is_never_stored(admin, identity_context):
    expected = hashlib.sha256(identity_context["session_token"].encode("ascii")).digest()
    stored = admin.execute(
        "SELECT session_token_hash FROM app.sessions WHERE id = %s",
        (identity_context["tenant_session_id"],),
    ).fetchone()[0]
    assert stored == expected
    assert identity_context["session_token"].encode("ascii") not in bytes(stored)


def test_email_does_not_link_different_oidc_issuers(admin, identity_context):
    row = admin.execute(
        "INSERT INTO control.users "
        "(id, oidc_issuer, oidc_subject, display_name, contact_email) "
        "SELECT gen_random_uuid(), 'https://other.example.invalid', oidc_subject, "
        "display_name, contact_email FROM control.users WHERE id = %s RETURNING id",
        (identity_context["user_id"],),
    ).fetchone()
    assert row[0] != identity_context["user_id"]
