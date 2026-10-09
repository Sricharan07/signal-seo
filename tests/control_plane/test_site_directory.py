import hashlib
from uuid import uuid4

import psycopg
import pytest
from signal_core.authorization import InvalidSession
from signal_core.session_management import list_tenant_sites


def grant_site(admin, *, scope, user_id):
    membership_id = uuid4()
    admin.execute(
        "INSERT INTO app.site_memberships "
        "(tenant_id, site_id, id, user_id, permission_set, authorization_epoch, state) "
        "VALUES (%s, %s, %s, %s, %s, 1, 'active')",
        (
            scope.tenant_id,
            scope.site_id,
            membership_id,
            user_id,
            '{"permissions":["site.snapshot.request"],"schema_version":1}',
        ),
    )
    return membership_id


def directory(identity, identity_context):
    return list_tenant_sites(
        identity,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
    )


def test_lists_only_current_tenant_active_site_grants(admin, identity, scopes, identity_context):
    admin.execute(
        "UPDATE app.sites SET name = 'Second site', primary_origin = "
        "'https://second.example.invalid' WHERE tenant_id = %s AND id = %s",
        (scopes[1].tenant_id, scopes[1].site_id),
    )
    grant_site(admin, scope=scopes[1], user_id=identity_context["user_id"])

    result = directory(identity, identity_context)

    assert result.tenant_id == scopes[0].tenant_id
    assert result.tenant_name == "Synthetic tenant"
    assert [(site.name, site.primary_origin) for site in result.sites] == [
        ("Second site", "https://second.example.invalid"),
        ("Synthetic site", "https://example.invalid"),
    ]
    assert all(site.state == "onboarding" for site in result.sites)
    assert identity.execute("SELECT count(*) FROM app.sites").fetchone() == (0,)


def test_valid_session_with_no_site_grants_returns_empty_directory(
    admin, identity, scopes, identity_context
):
    admin.execute(
        "DELETE FROM app.site_memberships WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, identity_context["site_membership_id"]),
    )

    result = directory(identity, identity_context)

    assert result.sites == ()


@pytest.mark.parametrize(
    ("table", "key", "value"),
    [
        ("app.site_memberships", "id", "UPDATE app.site_memberships SET state = 'suspended'"),
        ("app.sites", "id", "UPDATE app.sites SET state = 'archived'"),
    ],
)
def test_inactive_site_authority_is_omitted(
    admin, identity, scopes, identity_context, table, key, value
):
    identifier = (
        identity_context["site_membership_id"]
        if table == "app.site_memberships"
        else scopes[0].site_id
    )
    admin.execute(f"{value} WHERE tenant_id = %s AND {key} = %s", (scopes[0].tenant_id, identifier))

    assert directory(identity, identity_context).sites == ()


def test_other_tenant_route_cannot_cross_tenant_session(admin, identity, scopes, identity_context):
    cross_membership_id = uuid4()
    admin.execute(
        "INSERT INTO app.memberships "
        "(tenant_id, id, user_id, role_key, state, authorization_epoch) "
        "VALUES (%s, %s, %s, 'viewer', 'active', 1)",
        (scopes[2].tenant_id, cross_membership_id, identity_context["user_id"]),
    )
    grant_site(admin, scope=scopes[2], user_id=identity_context["user_id"])

    result = directory(identity, identity_context)

    assert [site.id for site in result.sites] == [scopes[0].site_id]
    assert scopes[2].site_id not in {site.id for site in result.sites}


@pytest.mark.parametrize(
    ("table", "column", "identifier_key"),
    [
        ("app.sessions", "revoked_at", "tenant_session_id"),
        ("control.identity_sessions", "revoked_at", "identity_session_id"),
        ("control.users", "disabled_at", "user_id"),
        ("app.memberships", "state", "membership_id"),
        ("app.tenants", "lifecycle", None),
    ],
)
def test_directory_rejects_reduced_session_authority(
    admin, identity, scopes, identity_context, table, column, identifier_key
):
    identifier = scopes[0].tenant_id if identifier_key is None else identity_context[identifier_key]
    replacement = "'suspended'" if column in {"state", "lifecycle"} else "now()"
    key = "tenant_id" if table == "app.tenants" else "id"
    admin.execute(f"UPDATE {table} SET {column} = {replacement} WHERE {key} = %s", (identifier,))

    with pytest.raises(InvalidSession):
        directory(identity, identity_context)


def test_route_tracks_site_membership_delete(admin, identity, identity_context):
    assert admin.execute(
        "SELECT count(*) FROM control.user_site_membership_routes WHERE site_membership_id = %s",
        (identity_context["site_membership_id"],),
    ).fetchone() == (1,)
    admin.execute(
        "DELETE FROM app.site_memberships WHERE id = %s",
        (identity_context["site_membership_id"],),
    )
    assert admin.execute(
        "SELECT count(*) FROM control.user_site_membership_routes WHERE site_membership_id = %s",
        (identity_context["site_membership_id"],),
    ).fetchone() == (0,)
    assert directory(identity, identity_context).sites == ()


def test_identity_role_has_only_function_access(identity, admin, identity_context):
    assert admin.execute(
        "SELECT has_function_privilege("
        "'signal_identity', 'control.list_tenant_sites(bytea,text)', 'EXECUTE')"
    ).fetchone() == (True,)
    assert admin.execute(
        "SELECT has_table_privilege("
        "'signal_identity', 'control.user_site_membership_routes', 'SELECT')"
    ).fetchone() == (False,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), identity.transaction():
        identity.execute("SELECT * FROM control.user_site_membership_routes")
    assert identity.execute(
        "SELECT outcome FROM control.list_tenant_sites(%s, %s)",
        (hashlib.sha256(b"x" * 43).digest(), identity_context["generation"]),
    ).fetchone() == ("invalid_session",)


def test_invalid_projection_and_inputs_fail_closed(admin, identity, scopes, identity_context):
    admin.execute(
        "UPDATE app.sites SET primary_origin = 'javascript:unsafe' "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    )
    with pytest.raises(RuntimeError, match="origin"):
        directory(identity, identity_context)

    identity.close()
    with pytest.raises(InvalidSession):
        list_tenant_sites(
            identity,
            session_token="short",
            current_recovery_generation=identity_context["generation"],
        )
