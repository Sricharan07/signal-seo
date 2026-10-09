import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from signal_core.authorization import AuthorizationDenied, InvalidSession, authorize_snapshot
from signal_core.session_management import (
    SessionContextConflict,
    SiteSelectionDenied,
    inspect_tenant_session,
    select_session_site,
)


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


def select(identity, identity_context, site_id, *, expected_version, event_id_factory=uuid4):
    return select_session_site(
        identity,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
        requested_site_id=site_id,
        expected_session_version=expected_version,
        event_id_factory=event_id_factory,
    )


def test_selects_current_site_and_appends_hash_chained_context_evidence(
    admin, identity, scopes, identity_context
):
    second_membership_id = grant_site(
        admin,
        scope=scopes[1],
        user_id=identity_context["user_id"],
    )

    selected = select(identity, identity_context, scopes[1].site_id, expected_version=2)

    assert selected.tenant_id == scopes[0].tenant_id
    assert selected.user_id == identity_context["user_id"]
    assert selected.site_id == scopes[1].site_id
    assert selected.session_version == 3
    assert selected.changed is True
    current = inspect_tenant_session(
        identity,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
    )
    assert current.active_site_id == scopes[1].site_id
    assert current.session_version == 3

    event = admin.execute(
        "SELECT site_id, session_id, user_id, session_version, role_key, "
        "authentication_level, membership_authorization_epoch, "
        "site_authorization_epoch, recovery_generation, previous_hash, "
        "octet_length(event_hash), occurred_at "
        "FROM app.session_site_context_events WHERE session_id = %s",
        (identity_context["tenant_session_id"],),
    ).fetchone()
    assert event[:-1] == (
        scopes[1].site_id,
        identity_context["tenant_session_id"],
        identity_context["user_id"],
        3,
        "analyst",
        "primary",
        1,
        1,
        identity_context["generation"],
        None,
        32,
    )
    assert event[-1] is not None
    assert admin.execute(
        "SELECT id FROM app.site_memberships WHERE id = %s",
        (second_membership_id,),
    ).fetchone() == (second_membership_id,)


def test_reselecting_current_site_is_idempotent_without_new_event(
    admin, identity, scopes, identity_context
):
    selected = select(identity, identity_context, scopes[0].site_id, expected_version=2)

    assert selected.changed is False
    assert selected.session_version == 2
    assert admin.execute(
        "SELECT count(*) FROM app.session_site_context_events WHERE session_id = %s",
        (identity_context["tenant_session_id"],),
    ).fetchone() == (0,)


def test_stale_selection_conflicts_without_changing_server_context(
    admin, identity, scopes, identity_context
):
    grant_site(admin, scope=scopes[1], user_id=identity_context["user_id"])
    first = select(identity, identity_context, scopes[1].site_id, expected_version=2)
    assert first.session_version == 3

    with pytest.raises(SessionContextConflict):
        select(identity, identity_context, scopes[0].site_id, expected_version=2)

    assert admin.execute(
        "SELECT active_site_id, session_version FROM app.sessions WHERE id = %s",
        (identity_context["tenant_session_id"],),
    ).fetchone() == (scopes[1].site_id, 3)
    assert admin.execute(
        "SELECT count(*) FROM app.session_site_context_events WHERE session_id = %s",
        (identity_context["tenant_session_id"],),
    ).fetchone() == (1,)


def test_wrong_site_and_cross_tenant_site_are_indistinguishably_denied(
    admin, identity, scopes, identity_context
):
    other_membership_id = uuid4()
    admin.execute(
        "INSERT INTO app.memberships "
        "(tenant_id, id, user_id, role_key, state, authorization_epoch) "
        "VALUES (%s, %s, %s, 'viewer', 'active', 1)",
        (scopes[2].tenant_id, other_membership_id, identity_context["user_id"]),
    )
    grant_site(admin, scope=scopes[2], user_id=identity_context["user_id"])

    for site_id in (scopes[1].site_id, scopes[2].site_id):
        with pytest.raises(SiteSelectionDenied) as error:
            select(identity, identity_context, site_id, expected_version=2)
        assert str(error.value) == ""


def test_invalid_parent_authority_rejects_selection(admin, identity, scopes, identity_context):
    admin.execute(
        "UPDATE control.identity_sessions SET revoked_at = now() WHERE id = %s",
        (identity_context["identity_session_id"],),
    )

    with pytest.raises(InvalidSession):
        select(identity, identity_context, scopes[0].site_id, expected_version=2)


def test_revoked_selected_grant_is_not_projected_or_usable(
    admin, identity, scopes, identity_context
):
    admin.execute(
        "UPDATE app.site_memberships SET state = 'suspended' WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, identity_context["site_membership_id"]),
    )

    current = inspect_tenant_session(
        identity,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
    )
    assert current.active_site_id is None
    assert current.session_version == 2
    with pytest.raises(AuthorizationDenied):
        authorize_snapshot(
            identity,
            session_token=identity_context["session_token"],
            requested_site_id=scopes[0].site_id,
            current_recovery_generation=identity_context["generation"],
        )


def test_snapshot_authority_requires_the_server_selected_site(
    admin, identity, scopes, identity_context
):
    admin.execute(
        "UPDATE app.sessions SET active_site_id = NULL, session_version = 1 WHERE id = %s",
        (identity_context["tenant_session_id"],),
    )

    with pytest.raises(AuthorizationDenied):
        authorize_snapshot(
            identity,
            session_token=identity_context["session_token"],
            requested_site_id=scopes[0].site_id,
            current_recovery_generation=identity_context["generation"],
        )


def test_concurrent_stale_selections_allow_one_context_transition(admin, scopes, identity_context):
    grant_site(admin, scope=scopes[1], user_id=identity_context["user_id"])
    admin.execute(
        "UPDATE app.sessions SET active_site_id = NULL, session_version = 1 WHERE id = %s",
        (identity_context["tenant_session_id"],),
    )

    def choose(site_id):
        with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as connection:
            try:
                return select(connection, identity_context, site_id, expected_version=1).site_id
            except SessionContextConflict:
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(choose, (scopes[0].site_id, scopes[1].site_id)))

    assert sum(result is not None for result in results) == 1
    assert admin.execute(
        "SELECT count(*) FROM app.session_site_context_events WHERE session_id = %s",
        (identity_context["tenant_session_id"],),
    ).fetchone() == (1,)
    active_site_id, version = admin.execute(
        "SELECT active_site_id, session_version FROM app.sessions WHERE id = %s",
        (identity_context["tenant_session_id"],),
    ).fetchone()
    assert active_site_id in {scopes[0].site_id, scopes[1].site_id}
    assert version == 2


def test_event_id_collision_retries_the_atomic_transition(
    admin, identity, scopes, identity_context
):
    grant_site(admin, scope=scopes[1], user_id=identity_context["user_id"])
    first_event_id = uuid4()
    select(
        identity,
        identity_context,
        scopes[1].site_id,
        expected_version=2,
        event_id_factory=lambda: first_event_id,
    )
    replacement_event_id = uuid4()
    identifiers = iter((first_event_id, replacement_event_id))

    selected = select(
        identity,
        identity_context,
        scopes[0].site_id,
        expected_version=3,
        event_id_factory=lambda: next(identifiers),
    )

    assert selected.site_id == scopes[0].site_id
    assert selected.session_version == 4
    events = admin.execute(
        "SELECT id, session_version, previous_hash IS NOT NULL "
        "FROM app.session_site_context_events WHERE session_id = %s "
        "ORDER BY session_version",
        (identity_context["tenant_session_id"],),
    ).fetchall()
    assert events == [(first_event_id, 3, False), (replacement_event_id, 4, True)]


def test_identity_role_has_only_selection_function_authority(admin, identity, identity_context):
    assert admin.execute(
        "SELECT has_function_privilege("
        "'signal_identity', "
        "'control.select_session_site(bytea,text,uuid,bigint,uuid)', 'EXECUTE')"
    ).fetchone() == (True,)
    assert admin.execute(
        "SELECT has_table_privilege('signal_identity', 'app.session_site_context_events', 'SELECT')"
    ).fetchone() == (False,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), identity.transaction():
        identity.execute(
            "UPDATE app.sessions SET active_site_id = NULL WHERE id = %s",
            (identity_context["tenant_session_id"],),
        )


def test_context_evidence_is_immutable_and_rejects_forged_hash(
    admin, identity, scopes, identity_context
):
    grant_site(admin, scope=scopes[1], user_id=identity_context["user_id"])
    select(identity, identity_context, scopes[1].site_id, expected_version=2)
    event_id = admin.execute(
        "SELECT id FROM app.session_site_context_events WHERE session_id = %s",
        (identity_context["tenant_session_id"],),
    ).fetchone()[0]

    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "DELETE FROM app.session_site_context_events WHERE id = %s",
            (event_id,),
        )

    forged_session_id = uuid4()
    forged_updated_at = admin.execute(
        "INSERT INTO app.sessions "
        "(tenant_id, id, identity_session_id, user_id, session_token_hash, auth_time, "
        "mfa_level, expires_at, last_seen_at, session_version, active_site_id, updated_at) "
        "SELECT tenant_id, %s, identity_session_id, user_id, %s, auth_time, mfa_level, "
        "expires_at, last_seen_at, 2, %s, statement_timestamp() "
        "FROM app.sessions WHERE id = %s RETURNING updated_at",
        (
            forged_session_id,
            hashlib.sha256(str(forged_session_id).encode("ascii")).digest(),
            scopes[1].site_id,
            identity_context["tenant_session_id"],
        ),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "INSERT INTO app.session_site_context_events "
            "(tenant_id, id, site_id, session_id, user_id, session_version, role_key, "
            "authentication_level, membership_authorization_epoch, "
            "site_authorization_epoch, recovery_generation, event_hash, occurred_at) "
            "VALUES (%s, %s, %s, %s, %s, 2, 'analyst', 'primary', 1, 1, %s, "
            "%s, %s)",
            (
                scopes[0].tenant_id,
                uuid4(),
                scopes[1].site_id,
                forged_session_id,
                identity_context["user_id"],
                identity_context["generation"],
                hashlib.sha256(b"forged").digest(),
                forged_updated_at,
            ),
        )


@pytest.mark.parametrize("version", [None, True, 0, -1, "2"])
def test_malformed_selection_version_fails_before_database(
    identity, scopes, identity_context, version
):
    identity.close()
    with pytest.raises(SessionContextConflict):
        select_session_site(
            identity,
            session_token=identity_context["session_token"],
            current_recovery_generation=identity_context["generation"],
            requested_site_id=scopes[0].site_id,
            expected_session_version=version,
        )
