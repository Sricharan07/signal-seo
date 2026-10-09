import hashlib
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.authorization import InvalidSession
from signal_core.session_management import inspect_tenant_session, revoke_browser_session


def opaque() -> str:
    return secrets.token_urlsafe(32)


def seed_issued_event(admin, identity_context):
    event_id = uuid4()
    admin.execute(
        "INSERT INTO control.platform_events "
        "(id, event_type, actor_user_id, object_kind, object_id, facts, reason) "
        "VALUES (%s, 'identity.session.issued', %s, 'identity_session', %s, %s, NULL)",
        (
            event_id,
            identity_context["user_id"],
            identity_context["identity_session_id"],
            Jsonb({"schema_version": 1, "authentication_level": "primary"}),
        ),
    )
    return event_id


def test_current_tenant_session_rechecks_parent_and_membership(identity, scopes, identity_context):
    current = inspect_tenant_session(
        identity,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
    )
    assert current.tenant_id == scopes[0].tenant_id
    assert current.user_id == identity_context["user_id"]
    assert current.role_key == "analyst"
    assert current.authentication_level == "primary"
    assert current.expires_at > datetime.now(UTC)
    assert current.session_version == 2
    assert current.active_site_id == scopes[0].site_id
    assert identity.execute("SELECT count(*) FROM app.sessions").fetchone() == (0,)


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("app.sessions", "revoked_at", "now()"),
        ("control.identity_sessions", "revoked_at", "now()"),
        ("control.users", "disabled_at", "now()"),
        ("app.memberships", "state", "'suspended'"),
        ("app.tenants", "lifecycle", "'suspended'"),
    ],
)
def test_current_session_invalid_states_are_indistinguishable(
    admin, identity, scopes, identity_context, table, column, value
):
    identifiers = {
        "app.sessions": ("id", identity_context["tenant_session_id"]),
        "control.identity_sessions": ("id", identity_context["identity_session_id"]),
        "control.users": ("id", identity_context["user_id"]),
        "app.memberships": ("id", identity_context["membership_id"]),
        "app.tenants": ("tenant_id", scopes[0].tenant_id),
    }
    key, identifier = identifiers[table]
    admin.execute(f"UPDATE {table} SET {column} = {value} WHERE {key} = %s", (identifier,))
    with pytest.raises(InvalidSession) as error:
        inspect_tenant_session(
            identity,
            session_token=identity_context["session_token"],
            current_recovery_generation=identity_context["generation"],
        )
    assert str(error.value) == ""


def test_current_session_rejects_wrong_generation_and_token(identity, identity_context):
    for token, generation in [
        (opaque(), identity_context["generation"]),
        (identity_context["session_token"], "other-generation"),
    ]:
        with pytest.raises(InvalidSession):
            inspect_tenant_session(
                identity,
                session_token=token,
                current_recovery_generation=generation,
            )


def test_tenant_logout_revokes_parent_and_exact_child_with_one_event(
    admin, identity, identity_context
):
    assert revoke_browser_session(
        identity,
        session_token=identity_context["session_token"],
        presented_session_kind="tenant",
    )
    parent = admin.execute(
        "SELECT revoked_at FROM control.identity_sessions WHERE id = %s",
        (identity_context["identity_session_id"],),
    ).fetchone()
    child = admin.execute(
        "SELECT revoked_at FROM app.sessions WHERE id = %s",
        (identity_context["tenant_session_id"],),
    ).fetchone()
    assert parent[0] is not None
    assert child[0] == parent[0]
    event = admin.execute(
        "SELECT event_type, actor_user_id, object_kind, object_id, facts, reason "
        "FROM control.platform_events WHERE event_type = 'identity.session.revoked'"
    ).fetchone()
    assert event == (
        "identity.session.revoked",
        identity_context["user_id"],
        "identity_session",
        identity_context["identity_session_id"],
        {"schema_version": 1, "presented_session_kind": "tenant"},
        "user_logout",
    )
    assert not revoke_browser_session(
        identity,
        session_token=identity_context["session_token"],
        presented_session_kind="tenant",
    )
    assert admin.execute(
        "SELECT count(*) FROM control.platform_events WHERE event_type = 'identity.session.revoked'"
    ).fetchone() == (1,)


def test_identity_logout_revokes_parent_without_requiring_tenant_context(
    admin, identity, identity_context
):
    identity_token = opaque()
    admin.execute(
        "UPDATE control.identity_sessions SET token_hash = %s WHERE id = %s",
        (
            hashlib.sha256(identity_token.encode("ascii")).digest(),
            identity_context["identity_session_id"],
        ),
    )
    assert revoke_browser_session(
        identity,
        session_token=identity_token,
        presented_session_kind="identity",
    )
    assert admin.execute(
        "SELECT revoked_at IS NOT NULL FROM control.identity_sessions WHERE id = %s",
        (identity_context["identity_session_id"],),
    ).fetchone() == (True,)
    assert admin.execute(
        "SELECT revoked_at FROM app.sessions WHERE id = %s",
        (identity_context["tenant_session_id"],),
    ).fetchone() == (None,)
    assert admin.execute(
        "SELECT facts FROM control.platform_events "
        "WHERE event_type = 'identity.session.revoked' AND object_id = %s",
        (identity_context["identity_session_id"],),
    ).fetchone() == ({"schema_version": 1, "presented_session_kind": "identity"},)


def test_unknown_logout_is_idempotent_and_creates_no_event(identity, admin):
    before = admin.execute(
        "SELECT count(*) FROM control.platform_events WHERE event_type = 'identity.session.revoked'"
    ).fetchone()[0]
    assert not revoke_browser_session(
        identity,
        session_token=opaque(),
        presented_session_kind="tenant",
    )
    after = admin.execute(
        "SELECT count(*) FROM control.platform_events WHERE event_type = 'identity.session.revoked'"
    ).fetchone()[0]
    assert after == before


def test_revocation_event_collision_retries_atomically(admin, identity, identity_context):
    existing_id = seed_issued_event(admin, identity_context)
    replacement_id = uuid4()
    identifiers = iter((existing_id, replacement_id))
    assert revoke_browser_session(
        identity,
        session_token=identity_context["session_token"],
        presented_session_kind="tenant",
        event_id_factory=lambda: next(identifiers),
    )
    assert admin.execute(
        "SELECT count(*) FROM control.platform_events WHERE id = %s",
        (replacement_id,),
    ).fetchone() == (1,)


def test_terminal_event_collision_rolls_back_revocation(admin, identity, identity_context):
    existing_id = seed_issued_event(admin, identity_context)
    with pytest.raises(RuntimeError, match="event allocation failed"):
        revoke_browser_session(
            identity,
            session_token=identity_context["session_token"],
            presented_session_kind="tenant",
            event_id_factory=lambda: existing_id,
        )
    assert admin.execute(
        "SELECT revoked_at FROM control.identity_sessions WHERE id = %s",
        (identity_context["identity_session_id"],),
    ).fetchone() == (None,)


def test_concurrent_logout_produces_one_transition(admin, identity_context):
    def revoke_once() -> bool:
        with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as connection:
            return revoke_browser_session(
                connection,
                session_token=identity_context["session_token"],
                presented_session_kind="tenant",
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: revoke_once(), range(2)))
    assert sorted(results) == [False, True]
    assert admin.execute(
        "SELECT count(*) FROM control.platform_events "
        "WHERE event_type = 'identity.session.revoked' AND object_id = %s",
        (identity_context["identity_session_id"],),
    ).fetchone() == (1,)


def test_identity_role_has_only_exact_revocation_function(identity, admin, identity_context):
    assert admin.execute(
        "SELECT has_function_privilege("
        "'signal_identity', 'control.revoke_browser_session(bytea,text,uuid)', 'EXECUTE')"
    ).fetchone() == (True,)
    for table in ("control.identity_sessions", "app.sessions"):
        assert admin.execute(
            "SELECT has_table_privilege('signal_identity', %s, 'UPDATE')",
            (table,),
        ).fetchone() == (False,)
    assert identity.execute(
        "SELECT control.revoke_browser_session(%s, NULL, %s)",
        (
            hashlib.sha256(identity_context["session_token"].encode("ascii")).digest(),
            uuid4(),
        ),
    ).fetchone() == (False,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), identity.transaction():
        identity.execute(
            "UPDATE control.identity_sessions SET revoked_at = now() WHERE id = %s",
            (identity_context["identity_session_id"],),
        )


def test_revocation_event_is_strict_and_immutable(admin, identity, identity_context):
    revoke_browser_session(
        identity,
        session_token=identity_context["session_token"],
        presented_session_kind="tenant",
    )
    event_id = admin.execute(
        "SELECT id FROM control.platform_events WHERE event_type = 'identity.session.revoked'"
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "INSERT INTO control.platform_events "
            "(id, event_type, actor_user_id, object_kind, object_id, facts, reason) "
            "VALUES (%s, 'identity.session.revoked', %s, 'identity_session', %s, %s, "
            "'user_logout')",
            (
                uuid4(),
                identity_context["user_id"],
                identity_context["identity_session_id"],
                Jsonb({"schema_version": 1, "presented_session_kind": "tenant", "raw": "bad"}),
            ),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute("DELETE FROM control.platform_events WHERE id = %s", (event_id,))


def test_malformed_management_inputs_fail_before_database(identity, identity_context):
    identity.close()
    with pytest.raises(InvalidSession):
        inspect_tenant_session(
            identity,
            session_token="short",
            current_recovery_generation=identity_context["generation"],
        )
    with pytest.raises(InvalidSession):
        revoke_browser_session(
            identity,
            session_token="short",
            presented_session_kind="tenant",
        )
    with pytest.raises(ValueError, match="kind"):
        revoke_browser_session(
            identity,
            session_token=identity_context["session_token"],
            presented_session_kind="site",
        )
