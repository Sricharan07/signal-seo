import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from signal_core.identity_memberships import list_identity_memberships
from signal_core.oidc_protocol import VerifiedOidcIdentity
from signal_core.session_issuance import (
    InvalidIdentitySession,
    issue_identity_session,
)


def _verified(user_id, now: datetime) -> VerifiedOidcIdentity:
    return VerifiedOidcIdentity(
        issuer="https://identity.example.test/realms/signal",
        subject=f"membership-directory-{user_id}",
        client_id="signal-dashboard",
        issued_at=int(now.timestamp()),
        expires_at=int((now + timedelta(minutes=5)).timestamp()),
        auth_time=int((now - timedelta(seconds=5)).timestamp()),
        provider_session_id=f"provider-{user_id}",
        authentication_context="1",
    )


def _provision_identity(identity, admin, *, generation: str, memberships=()):
    user_id = uuid4()
    now = datetime.now(UTC).replace(microsecond=0)
    admin.execute(
        "INSERT INTO control.users "
        "(id, oidc_issuer, oidc_subject, display_name, contact_email) "
        "VALUES (%s, %s, %s, 'Directory user', %s)",
        (
            user_id,
            "https://identity.example.test/realms/signal",
            f"membership-directory-{user_id}",
            f"{user_id}@example.invalid",
        ),
    )
    membership_ids = {}
    for tenant_id, role_key in memberships:
        membership_id = uuid4()
        admin.execute(
            "INSERT INTO app.memberships "
            "(tenant_id, id, user_id, role_key, state, authorization_epoch) "
            "VALUES (%s, %s, %s, %s, 'active', 1)",
            (tenant_id, membership_id, user_id, role_key),
        )
        membership_ids[tenant_id] = membership_id
    issued = issue_identity_session(
        identity,
        identity=_verified(user_id, now),
        current_recovery_generation=generation,
        now=now,
    )
    return {
        "user_id": user_id,
        "session": issued,
        "generation": generation,
        "membership_ids": membership_ids,
    }


@pytest.fixture
def directory_context(identity, admin, scopes):
    admin.execute(
        "UPDATE app.tenants SET name = 'Zulu organization' WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    )
    admin.execute(
        "UPDATE app.tenants SET name = 'Alpha organization' WHERE tenant_id = %s",
        (scopes[2].tenant_id,),
    )
    context = _provision_identity(
        identity,
        admin,
        generation="membership-directory-generation-1",
        memberships=((scopes[0].tenant_id, "editor"), (scopes[2].tenant_id, "viewer")),
    )
    context["tenant_ids"] = (scopes[0].tenant_id, scopes[2].tenant_id)
    return context


def _list(identity, context, **overrides):
    arguments = {
        "identity_session_token": context["session"].token,
        "current_recovery_generation": context["generation"],
        **overrides,
    }
    return list_identity_memberships(identity, **arguments)


def test_valid_identity_session_lists_only_its_active_memberships(identity, directory_context):
    memberships = _list(identity, directory_context)
    assert [(item.tenant_name, item.role_key) for item in memberships] == [
        ("Alpha organization", "viewer"),
        ("Zulu organization", "editor"),
    ]
    assert directory_context["session"].token not in repr(memberships)


def test_valid_identity_without_membership_is_distinct_from_invalid_session(identity, admin):
    context = _provision_identity(
        identity,
        admin,
        generation="membership-directory-empty-1",
    )
    assert _list(identity, context) == ()


def test_suspended_membership_and_tenant_are_not_listed(identity, admin, directory_context):
    first, second = directory_context["tenant_ids"]
    admin.execute(
        "UPDATE app.memberships SET state = 'suspended' WHERE tenant_id = %s AND user_id = %s",
        (first, directory_context["user_id"]),
    )
    admin.execute(
        "UPDATE app.tenants SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (second,),
    )
    admin.execute(
        "UPDATE control.tenant_directory SET lifecycle = 'suspended' WHERE tenant_id = %s",
        (second,),
    )
    assert _list(identity, directory_context) == ()


@pytest.mark.parametrize(
    "invalid_state",
    ["wrong_token", "wrong_generation", "revoked", "expired", "disabled"],
)
def test_invalid_identity_session_states_are_indistinguishable(
    identity, admin, directory_context, invalid_state: str
):
    overrides = {}
    if invalid_state == "wrong_token":
        overrides["identity_session_token"] = secrets.token_urlsafe(32)
    elif invalid_state == "wrong_generation":
        overrides["current_recovery_generation"] = "another-valid-generation"
    elif invalid_state == "revoked":
        admin.execute(
            "UPDATE control.identity_sessions SET revoked_at = now() WHERE id = %s",
            (directory_context["session"].id,),
        )
    elif invalid_state == "expired":
        admin.execute(
            "UPDATE control.identity_sessions SET expires_at = now() WHERE id = %s",
            (directory_context["session"].id,),
        )
    else:
        admin.execute(
            "UPDATE control.users SET disabled_at = now() WHERE id = %s",
            (directory_context["user_id"],),
        )
    with pytest.raises(InvalidIdentitySession) as error:
        _list(identity, directory_context, **overrides)
    assert str(error.value) == ""


def test_membership_route_tracks_identifier_changes_and_deletion(admin, directory_context):
    tenant_id = directory_context["tenant_ids"][0]
    old_membership_id = directory_context["membership_ids"][tenant_id]
    new_membership_id = uuid4()
    admin.execute(
        "UPDATE app.memberships SET id = %s WHERE tenant_id = %s AND id = %s",
        (new_membership_id, tenant_id, old_membership_id),
    )
    assert admin.execute(
        "SELECT membership_id, user_id FROM control.user_membership_routes WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone() == (new_membership_id, directory_context["user_id"])
    admin.execute(
        "DELETE FROM app.memberships WHERE tenant_id = %s AND id = %s",
        (tenant_id, new_membership_id),
    )
    assert admin.execute(
        "SELECT count(*) FROM control.user_membership_routes WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone() == (0,)


def test_directory_function_and_route_have_exact_identity_privileges(
    identity, api, admin, directory_context
):
    assert admin.execute(
        "SELECT has_function_privilege("
        "'signal_identity', 'control.list_identity_memberships(bytea,text)', 'EXECUTE')"
    ).fetchone() == (True,)
    assert admin.execute(
        "SELECT has_function_privilege("
        "'signal_api', 'control.list_identity_memberships(bytea,text)', 'EXECUTE')"
    ).fetchone() == (False,)
    for role_connection in (identity, api):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), role_connection.transaction():
            role_connection.execute("SELECT * FROM control.user_membership_routes")
    assert len(_list(identity, directory_context)) == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"identity_session_token": "short"},
        {"current_recovery_generation": "bad generation"},
    ],
)
def test_malformed_discovery_input_fails_before_database(identity, directory_context, overrides):
    identity.close()
    with pytest.raises((InvalidIdentitySession, RuntimeError)):
        _list(identity, directory_context, **overrides)


def test_residual_scope_contaminates_identity_connection(identity, directory_context):
    identity.execute("SELECT set_config('signal.tenant_id', %s, false)", (str(uuid4()),))
    with pytest.raises(ValueError, match="residual session scope"):
        _list(identity, directory_context)


def test_malformed_tenant_name_is_not_returned(identity, admin, directory_context):
    admin.execute(
        "UPDATE app.tenants SET name = %s WHERE tenant_id = %s",
        ("bad\nname", directory_context["tenant_ids"][0]),
    )
    with pytest.raises(RuntimeError, match="discovery failed"):
        _list(identity, directory_context)
