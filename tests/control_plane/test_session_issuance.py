import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from signal_core.authorization import authorize_snapshot
from signal_core.oidc_protocol import VerifiedOidcIdentity
from signal_core.session_issuance import (
    DEFAULT_SESSION_POLICY,
    InvalidIdentitySession,
    SessionIssuanceDenied,
    SessionPolicy,
    issue_identity_session,
    issue_tenant_session,
)
from signal_core.session_management import select_session_site


def opaque(character: str | None = None) -> str:
    return character * 43 if character is not None else secrets.token_urlsafe(32)


@pytest.fixture
def session_context(admin, scopes):
    user_id = uuid4()
    membership_id = uuid4()
    site_membership_id = uuid4()
    issuer = "https://identity.example.test/realms/signal"
    subject = f"subject-{user_id}"
    generation = "generation-session-tests-1"
    admin.execute(
        "INSERT INTO control.users "
        "(id, oidc_issuer, oidc_subject, display_name, contact_email) "
        "VALUES (%s, %s, %s, 'Session test user', %s)",
        (user_id, issuer, subject, f"{user_id}@example.invalid"),
    )
    admin.execute(
        "INSERT INTO app.memberships "
        "(tenant_id, id, user_id, role_key, state, authorization_epoch) "
        "VALUES (%s, %s, %s, 'editor', 'active', 1)",
        (scopes[0].tenant_id, membership_id, user_id),
    )
    admin.execute(
        "INSERT INTO app.site_memberships "
        "(tenant_id, site_id, id, user_id, permission_set, authorization_epoch, state) "
        "VALUES (%s, %s, %s, %s, %s, 1, 'active')",
        (
            scopes[0].tenant_id,
            scopes[0].site_id,
            site_membership_id,
            user_id,
            '{"permissions":["site.snapshot.request"],"schema_version":1}',
        ),
    )
    return {
        "user_id": user_id,
        "membership_id": membership_id,
        "site_membership_id": site_membership_id,
        "issuer": issuer,
        "subject": subject,
        "generation": generation,
    }


def verified(context, now: datetime, **overrides) -> VerifiedOidcIdentity:
    values = {
        "issuer": context["issuer"],
        "subject": context["subject"],
        "client_id": "signal-dashboard",
        "issued_at": int(now.timestamp()),
        "expires_at": int((now + timedelta(minutes=5)).timestamp()),
        "auth_time": int((now - timedelta(seconds=5)).timestamp()),
        "provider_session_id": f"provider-{uuid4()}",
        "authentication_context": "1",
        **overrides,
    }
    return VerifiedOidcIdentity(**values)


def issue_global(connection, context, now: datetime, **overrides):
    arguments = {
        "identity": verified(context, now),
        "current_recovery_generation": context["generation"],
        "now": now,
        **overrides,
    }
    return issue_identity_session(connection, **arguments)


def issue_tenant(connection, scopes, context, global_session, now: datetime, **overrides):
    arguments = {
        "identity_session_token": global_session.token,
        "requested_tenant_id": scopes[0].tenant_id,
        "current_recovery_generation": context["generation"],
        "now": now,
        **overrides,
    }
    return issue_tenant_session(connection, **arguments)


def test_required_mfa_method_needs_signed_completed_otp(identity, admin, session_context):
    now = datetime.now(UTC).replace(microsecond=0)
    policy = SessionPolicy(
        primary_acr_values=frozenset({"0"}),
        mfa_acr_values=frozenset({"1"}),
        required_mfa_methods=frozenset({"otp"}),
    )
    for methods in (frozenset(), frozenset({"pwd"})):
        with pytest.raises(SessionIssuanceDenied):
            issue_global(
                identity,
                session_context,
                now,
                policy=policy,
                identity=verified(session_context, now, authentication_methods=methods),
            )
    assert (
        admin.execute(
            "SELECT count(*) FROM control.identity_sessions WHERE user_id = %s",
            (session_context["user_id"],),
        ).fetchone()[0]
        == 0
    )
    session = issue_global(
        identity,
        session_context,
        now,
        policy=policy,
        identity=verified(session_context, now, authentication_methods=frozenset({"otp"})),
    )
    assert session.authentication_level == "mfa"


@pytest.mark.parametrize("violation", ["subject", "session", "timestamp", "session_hash"])
def test_email_claim_authority_failure_rolls_back_entire_sign_in(
    identity, admin, session_context, violation
):
    now = datetime.now(UTC)
    original = issue_global(identity, session_context, now)
    head = admin.execute(
        "SELECT * FROM control.email_claim_heads WHERE user_id=%s",
        (session_context["user_id"],),
    ).fetchone()
    token = opaque()

    class InvalidClaimConnection:
        def __getattr__(self, name):
            return getattr(identity, name)

        def execute(self, query, params=None):
            if "SELECT control.record_email_identity_claim(" in query:
                params = list(params)
                if violation == "subject":
                    params[2] = "synthetic-wrong-subject"
                elif violation == "session":
                    params[0] = uuid4()
                elif violation == "timestamp":
                    params[4] = None
                else:
                    identity.execute(
                        "SELECT set_config('signal.identity_session_hash',%s,true)",
                        (hashlib.sha256(b"synthetic-wrong-session").hexdigest(),),
                    )
            return identity.execute(query, params)

    with pytest.raises(psycopg.Error, match="email_claim_denied"):
        issue_global(
            InvalidClaimConnection(),
            session_context,
            now,
            token_factory=lambda: token,
        )
    assert admin.execute(
        "SELECT id FROM control.identity_sessions WHERE user_id=%s",
        (session_context["user_id"],),
    ).fetchall() == [(original.id,)]
    assert admin.execute(
        "SELECT session_id FROM control.email_identity_claims WHERE user_id=%s",
        (session_context["user_id"],),
    ).fetchall() == [(original.id,)]
    assert admin.execute(
        "SELECT object_id FROM control.platform_events WHERE actor_user_id=%s",
        (session_context["user_id"],),
    ).fetchall() == [(original.id,)]
    assert (
        admin.execute(
            "SELECT * FROM control.email_claim_heads WHERE user_id=%s",
            (session_context["user_id"],),
        ).fetchone()
        == head
    )
    assert admin.execute(
        "SELECT count(*) FROM control.identity_sessions WHERE token_hash=%s",
        (hashlib.sha256(token.encode("ascii")).digest(),),
    ).fetchone() == (0,)


def test_email_claim_wrong_database_user_still_denied(admin):
    with pytest.raises(psycopg.Error, match="email_claim_denied"):
        admin.execute(
            "SELECT control.record_email_identity_claim(%s,%s,%s,%s,%s)",
            (
                uuid4(),
                "https://identity.example.invalid",
                "synthetic-subject",
                "owner@example.invalid",
                datetime.now(UTC),
            ),
        )


def test_verified_identity_issues_hash_only_global_session(identity, admin, session_context):
    now = datetime.now(UTC).replace(microsecond=0)
    token = opaque("a")
    issued = issue_global(
        identity,
        session_context,
        now,
        token_factory=lambda: token,
    )

    assert issued.user_id == session_context["user_id"]
    assert issued.authentication_level == "primary"
    assert issued.expires_at == now + timedelta(hours=8)
    assert token not in repr(issued)
    stored = admin.execute(
        "SELECT token_hash, auth_time, authentication_level, recovery_generation, expires_at "
        "FROM control.identity_sessions WHERE id = %s",
        (issued.id,),
    ).fetchone()
    assert stored == (
        hashlib.sha256(token.encode("ascii")).digest(),
        now - timedelta(seconds=5),
        "primary",
        session_context["generation"],
        now + timedelta(hours=8),
    )
    assert token.encode("ascii") not in bytes(stored[0])
    event = admin.execute(
        "SELECT id, event_type, actor_user_id, object_kind, object_id, facts, reason "
        "FROM control.platform_events WHERE object_id = %s",
        (issued.id,),
    ).fetchone()
    assert event == (
        issued.audit_event_id,
        "identity.session.issued",
        session_context["user_id"],
        "identity_session",
        issued.id,
        {"schema_version": 1, "authentication_level": "primary"},
        None,
    )
    assert identity.execute("SELECT count(*) FROM control.identity_sessions").fetchone()[0] == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        identity.execute("SELECT count(*) FROM control.platform_events")


def test_mfa_acr_maps_to_mfa_without_trusting_display_claims(identity, session_context):
    now = datetime.now(UTC).replace(microsecond=0)
    issued = issue_global(
        identity,
        session_context,
        now,
        identity=verified(session_context, now, authentication_context="urn:signal:acr:mfa"),
    )
    assert issued.authentication_level == "mfa"


def test_unknown_and_disabled_users_are_indistinguishable(identity, admin, session_context):
    now = datetime.now(UTC).replace(microsecond=0)
    unknown = verified(session_context, now, subject=f"unknown-{uuid4()}")
    with pytest.raises(SessionIssuanceDenied) as missing:
        issue_identity_session(
            identity,
            identity=unknown,
            current_recovery_generation=session_context["generation"],
            now=now,
        )
    admin.execute(
        "UPDATE control.users SET disabled_at = now() WHERE id = %s",
        (session_context["user_id"],),
    )
    with pytest.raises(SessionIssuanceDenied) as disabled:
        issue_global(identity, session_context, now)
    assert str(missing.value) == str(disabled.value) == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"authentication_context": None},
        {"authentication_context": ["1"]},
        {"authentication_context": "unapproved-acr"},
        {"auth_time": None},
        {"auth_time": True},
        {"issued_at": True},
        {"expires_at": True},
        {"issued_at": lambda now: int((now - timedelta(minutes=11, seconds=1)).timestamp())},
        {"issued_at": lambda now: int((now + timedelta(seconds=31)).timestamp())},
        {"expires_at": lambda now: int(now.timestamp())},
        {"auth_time": lambda now: int((now + timedelta(seconds=31)).timestamp())},
        {"auth_time": lambda now: int((now - timedelta(hours=12, seconds=1)).timestamp())},
    ],
)
def test_untrusted_identity_or_stale_authentication_fails_before_database(
    identity, session_context, overrides
):
    now = datetime.now(UTC).replace(microsecond=0)
    resolved = {key: value(now) if callable(value) else value for key, value in overrides.items()}
    candidate = verified(session_context, now, **resolved)
    identity.close()
    with pytest.raises(SessionIssuanceDenied):
        issue_identity_session(
            identity,
            identity=candidate,
            current_recovery_generation=session_context["generation"],
            now=now,
        )


def test_session_policy_rejects_ambiguous_acr_and_time_configuration():
    invalid = [
        {"primary_acr_values": {"1"}, "mfa_acr_values": frozenset()},
        {"primary_acr_values": frozenset(), "mfa_acr_values": frozenset()},
        {"primary_acr_values": frozenset({"1"}), "mfa_acr_values": frozenset({"1"})},
        {"primary_acr_values": frozenset({"bad acr"}), "mfa_acr_values": frozenset()},
        {
            "primary_acr_values": frozenset({"1"}),
            "mfa_acr_values": frozenset(),
            "identity_ttl_seconds": 299,
        },
        {
            "primary_acr_values": frozenset({"1"}),
            "mfa_acr_values": frozenset(),
            "maximum_auth_age_seconds": True,
        },
    ]
    for arguments in invalid:
        with pytest.raises(ValueError):
            SessionPolicy(**arguments)


@pytest.mark.parametrize("generation", [None, "", "bad generation", "x" * 129])
def test_invalid_recovery_generation_fails_before_database(identity, session_context, generation):
    now = datetime.now(UTC).replace(microsecond=0)
    identity.close()
    with pytest.raises(RuntimeError, match="external recovery generation"):
        issue_identity_session(
            identity,
            identity=verified(session_context, now),
            current_recovery_generation=generation,
            now=now,
        )


def test_global_token_collision_is_retried_then_bounded(identity, admin, session_context):
    now = datetime.now(UTC).replace(microsecond=0)
    first_token = opaque()
    final_token = opaque()
    issue_global(identity, session_context, now, token_factory=lambda: first_token)
    candidates = iter((first_token, first_token, final_token))
    issued = issue_global(identity, session_context, now, token_factory=lambda: next(candidates))
    assert issued.token == final_token
    with pytest.raises(RuntimeError, match="allocation failed"):
        issue_global(identity, session_context, now, token_factory=lambda: first_token)
    assert (
        admin.execute(
            "SELECT count(*) FROM control.identity_sessions WHERE user_id = %s",
            (session_context["user_id"],),
        ).fetchone()[0]
        == 2
    )


@pytest.mark.parametrize("factory", [None, lambda: "short", lambda: "x" * 44])
def test_invalid_global_token_source_fails_before_database(identity, session_context, factory):
    now = datetime.now(UTC).replace(microsecond=0)
    identity.close()
    with pytest.raises((ValueError, RuntimeError)):
        issue_global(identity, session_context, now, token_factory=factory)


def test_identity_role_is_hash_scoped_and_insert_column_limited(identity, admin, session_context):
    now = datetime.now(UTC).replace(microsecond=0)
    issued = issue_global(identity, session_context, now)
    token_hash = hashlib.sha256(issued.token.encode("ascii")).hexdigest()

    assert identity.execute("SELECT count(*) FROM control.identity_sessions").fetchone()[0] == 0
    identity.execute(
        "SELECT set_config('signal.identity_session_hash', %s, false)",
        (token_hash,),
    )
    assert identity.execute(
        "SELECT id FROM control.identity_sessions WHERE token_hash = decode(%s, 'hex')",
        (token_hash,),
    ).fetchall() == [(issued.id,)]
    identity.execute("RESET signal.identity_session_hash")

    expected_identity_columns = {
        "id",
        "user_id",
        "token_hash",
        "auth_time",
        "authentication_level",
        "recovery_generation",
        "expires_at",
        "last_seen_at",
    }
    expected_tenant_columns = {
        "tenant_id",
        "id",
        "identity_session_id",
        "user_id",
        "session_token_hash",
        "auth_time",
        "mfa_level",
        "expires_at",
        "last_seen_at",
    }
    for table, expected in [
        ("control.identity_sessions", expected_identity_columns),
        ("app.sessions", expected_tenant_columns),
    ]:
        actual = {
            column
            for (column,) in admin.execute(
                "SELECT column_name FROM information_schema.column_privileges "
                "WHERE grantee = 'signal_identity' AND table_schema || '.' || table_name = %s "
                "AND privilege_type = 'INSERT'",
                (table,),
            )
        }
        assert actual == expected
        assert not admin.execute(
            "SELECT has_table_privilege('signal_identity', %s, 'UPDATE')", (table,)
        ).fetchone()[0]
        assert not admin.execute(
            "SELECT has_table_privilege('signal_identity', %s, 'DELETE')", (table,)
        ).fetchone()[0]
        assert not admin.execute(
            "SELECT has_table_privilege('signal_identity', %s, 'TRUNCATE')", (table,)
        ).fetchone()[0]


def test_active_membership_issues_hash_only_tenant_session(
    identity, admin, scopes, session_context
):
    now = datetime.now(UTC).replace(microsecond=0)
    global_session = issue_global(identity, session_context, now)
    tenant_token = opaque("c")
    issued = issue_tenant(
        identity,
        scopes,
        session_context,
        global_session,
        now,
        token_factory=lambda: tenant_token,
    )

    assert issued.tenant_id == scopes[0].tenant_id
    assert issued.user_id == session_context["user_id"]
    assert issued.role_key == "editor"
    assert issued.authentication_level == "primary"
    assert issued.expires_at == global_session.expires_at
    assert tenant_token not in repr(issued)
    stored = admin.execute(
        "SELECT identity_session_id, session_token_hash, mfa_level, expires_at "
        "FROM app.sessions WHERE id = %s",
        (issued.id,),
    ).fetchone()
    assert stored == (
        global_session.id,
        hashlib.sha256(tenant_token.encode("ascii")).digest(),
        "primary",
        global_session.expires_at,
    )
    assert identity.execute("SELECT count(*) FROM app.sessions").fetchone()[0] == 0
    identity.execute(
        "SELECT set_config('signal.tenant_id', %s, false)",
        (str(scopes[0].tenant_id),),
    )
    assert identity.execute("SELECT count(*) FROM app.sessions").fetchone()[0] == 0
    identity.execute("RESET signal.tenant_id")
    identity.execute(
        "SELECT set_config('signal.session_hash', %s, false)",
        (hashlib.sha256(tenant_token.encode("ascii")).hexdigest(),),
    )
    assert identity.execute("SELECT id FROM app.sessions").fetchall() == [(issued.id,)]
    identity.execute("RESET signal.session_hash")


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("control.identity_sessions", "revoked_at", "now()"),
        ("control.identity_sessions", "recovery_generation", "'stale-generation'"),
        ("control.users", "disabled_at", "now()"),
        ("app.memberships", "state", "'suspended'"),
        ("app.tenants", "lifecycle", "'suspended'"),
    ],
)
def test_invalid_parent_and_authority_states_are_indistinguishable(
    identity, admin, scopes, session_context, table, column, value
):
    now = datetime.now(UTC).replace(microsecond=0)
    global_session = issue_global(identity, session_context, now)
    identifiers = {
        "control.identity_sessions": global_session.id,
        "control.users": session_context["user_id"],
        "app.memberships": session_context["membership_id"],
        "app.tenants": scopes[0].tenant_id,
    }
    key = "tenant_id" if table == "app.tenants" else "id"
    admin.execute(
        f"UPDATE {table} SET {column} = {value} WHERE {key} = %s",
        (identifiers[table],),
    )
    with pytest.raises(InvalidIdentitySession) as failure:
        issue_tenant(identity, scopes, session_context, global_session, now)
    assert str(failure.value) == ""


def test_expired_parent_and_external_generation_mismatch_are_rejected(
    identity, admin, scopes, session_context
):
    now = datetime.now(UTC).replace(microsecond=0)
    global_session = issue_global(identity, session_context, now)
    admin.execute(
        "UPDATE control.identity_sessions SET expires_at = %s WHERE id = %s",
        (now, global_session.id),
    )
    with pytest.raises(InvalidIdentitySession):
        issue_tenant(identity, scopes, session_context, global_session, now)

    fresh = issue_global(identity, session_context, now)
    with pytest.raises(InvalidIdentitySession):
        issue_tenant(
            identity,
            scopes,
            session_context,
            fresh,
            now,
            current_recovery_generation="new-generation",
        )


def test_wrong_tenant_does_not_disclose_membership(identity, scopes, session_context):
    now = datetime.now(UTC).replace(microsecond=0)
    global_session = issue_global(identity, session_context, now)
    with pytest.raises(InvalidIdentitySession) as failure:
        issue_tenant(
            identity,
            scopes,
            session_context,
            global_session,
            now,
            requested_tenant_id=scopes[2].tenant_id,
        )
    assert str(failure.value) == ""


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("identity_session_token", "short"),
        ("identity_session_token", "x" * 44),
        ("requested_tenant_id", "not-a-uuid"),
        ("current_recovery_generation", ""),
    ],
)
def test_invalid_tenant_session_input_fails_before_database(
    identity, scopes, session_context, override, value
):
    now = datetime.now(UTC).replace(microsecond=0)
    global_session = type("Session", (), {"token": opaque()})()
    identity.close()
    with pytest.raises((InvalidIdentitySession, RuntimeError)):
        issue_tenant(
            identity,
            scopes,
            session_context,
            global_session,
            now,
            **{override: value},
        )


def test_tenant_token_collision_is_retried_then_bounded(identity, admin, scopes, session_context):
    now = datetime.now(UTC).replace(microsecond=0)
    global_session = issue_global(identity, session_context, now)
    first_token = opaque("d")
    final_token = opaque("e")
    issue_tenant(
        identity,
        scopes,
        session_context,
        global_session,
        now,
        token_factory=lambda: first_token,
    )
    candidates = iter((first_token, first_token, final_token))
    issued = issue_tenant(
        identity,
        scopes,
        session_context,
        global_session,
        now,
        token_factory=lambda: next(candidates),
    )
    assert issued.token == final_token
    with pytest.raises(RuntimeError, match="allocation failed"):
        issue_tenant(
            identity,
            scopes,
            session_context,
            global_session,
            now,
            token_factory=lambda: first_token,
        )
    assert (
        admin.execute(
            "SELECT count(*) FROM app.sessions WHERE user_id = %s",
            (session_context["user_id"],),
        ).fetchone()[0]
        == 2
    )


def test_session_issuance_does_not_create_authority_and_composes_with_site_authorization(
    identity, admin, scopes, session_context
):
    now = datetime.now(UTC).replace(microsecond=0)
    before = admin.execute(
        "SELECT (SELECT count(*) FROM control.users), "
        "(SELECT count(*) FROM app.memberships), "
        "(SELECT count(*) FROM app.site_memberships)"
    ).fetchone()
    global_session = issue_global(identity, session_context, now)
    tenant_session = issue_tenant(identity, scopes, session_context, global_session, now)
    after = admin.execute(
        "SELECT (SELECT count(*) FROM control.users), "
        "(SELECT count(*) FROM app.memberships), "
        "(SELECT count(*) FROM app.site_memberships)"
    ).fetchone()
    assert after == before

    selected = select_session_site(
        identity,
        session_token=tenant_session.token,
        current_recovery_generation=session_context["generation"],
        requested_site_id=scopes[0].site_id,
        expected_session_version=1,
    )
    assert selected.session_version == 2
    authorized = authorize_snapshot(
        identity,
        session_token=tenant_session.token,
        requested_site_id=scopes[0].site_id,
        current_recovery_generation=session_context["generation"],
    )
    assert authorized.user_id == session_context["user_id"]
    assert authorized.scope.tenant_id == scopes[0].tenant_id
    assert authorized.scope.site_id == scopes[0].site_id


def test_residual_global_session_scope_contaminates_connection(identity, session_context):
    identity.execute(
        "SELECT set_config('signal.identity_session_hash', %s, false)",
        ("a" * 64,),
    )
    now = datetime.now(UTC).replace(microsecond=0)
    with pytest.raises(ValueError, match="residual session scope"):
        issue_global(identity, session_context, now)


def test_default_policy_is_frozen_and_has_disjoint_acr_sets():
    assert DEFAULT_SESSION_POLICY.primary_acr_values == frozenset({"1"})
    assert DEFAULT_SESSION_POLICY.mfa_acr_values == frozenset({"2", "urn:signal:acr:mfa"})
