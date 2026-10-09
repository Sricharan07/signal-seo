import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
import signal_core.session_issuance as session_issuance
from psycopg.types.json import Jsonb
from signal_core.oidc_login import (
    OidcClientRegistration,
    OidcLoginAuditError,
    consume_oidc_login_attempt,
    create_oidc_login_attempt,
    record_consumed_login_failure,
)
from signal_core.oidc_protocol import VerifiedOidcIdentity
from signal_core.session_issuance import issue_identity_session


def opaque() -> str:
    return secrets.token_urlsafe(32)


@pytest.fixture
def audit_context(admin):
    user_id = uuid4()
    issuer = "https://identity.example.test/realms/signal"
    subject = f"audit-subject-{user_id}"
    admin.execute(
        "INSERT INTO control.users "
        "(id, oidc_issuer, oidc_subject, display_name, contact_email) "
        "VALUES (%s, %s, %s, 'Audit test user', %s)",
        (user_id, issuer, subject, f"{user_id}@example.invalid"),
    )
    return {
        "user_id": user_id,
        "issuer": issuer,
        "subject": subject,
        "generation": "audit-generation-1",
    }


def verified(context, now: datetime) -> VerifiedOidcIdentity:
    return VerifiedOidcIdentity(
        issuer=context["issuer"],
        subject=context["subject"],
        client_id="signal-dashboard",
        issued_at=int(now.timestamp()),
        expires_at=int((now + timedelta(minutes=5)).timestamp()),
        auth_time=int((now - timedelta(seconds=5)).timestamp()),
        provider_session_id=f"provider-{uuid4()}",
        authentication_context="1",
    )


def consumed_attempt(identity):
    state = opaque()
    browser_binding = opaque()
    nonce = opaque()
    attempt_id = create_oidc_login_attempt(
        identity,
        state=state,
        nonce=nonce,
        browser_binding=browser_binding,
        registration=OidcClientRegistration(
            issuer="https://identity.example.test/realms/signal",
            client_id="signal-dashboard",
            redirect_uri="https://dashboard.example.test/auth/callback",
        ),
        pkce_secret_reference=f"secret://oidc-login/{uuid4()}/1",
        return_path="/",
    )
    attempt = consume_oidc_login_attempt(
        identity,
        state=state,
        browser_binding=browser_binding,
    )
    assert attempt.id == attempt_id
    return attempt, state, browser_binding


def test_platform_event_is_immutable_and_shape_constrained(admin, identity, audit_context):
    now = datetime.now(UTC).replace(microsecond=0)
    issued = issue_identity_session(
        identity,
        identity=verified(audit_context, now),
        current_recovery_generation=audit_context["generation"],
        now=now,
    )

    for statement in [
        "UPDATE control.platform_events SET reason = 'changed' WHERE id = %s",
        "DELETE FROM control.platform_events WHERE id = %s",
    ]:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(statement, (issued.audit_event_id,))

    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "INSERT INTO control.platform_events "
            "(id, event_type, actor_user_id, object_kind, object_id, facts) "
            "VALUES (%s, 'identity.session.issued', %s, 'identity_session', %s, %s)",
            (
                uuid4(),
                audit_context["user_id"],
                issued.id,
                Jsonb(
                    {
                        "schema_version": 1,
                        "authentication_level": "primary",
                        "raw_token": "bad",
                    }
                ),
            ),
        )

    admin.execute("DELETE FROM control.identity_sessions WHERE id = %s", (issued.id,))
    assert admin.execute(
        "SELECT object_id FROM control.platform_events WHERE id = %s",
        (issued.audit_event_id,),
    ).fetchone() == (issued.id,)


def test_identity_role_can_insert_only_for_its_hash_scoped_session(admin, identity, audit_context):
    now = datetime.now(UTC).replace(microsecond=0)
    issued = issue_identity_session(
        identity,
        identity=verified(audit_context, now),
        current_recovery_generation=audit_context["generation"],
        now=now,
    )
    other_session_id = uuid4()
    admin.execute(
        "INSERT INTO control.identity_sessions "
        "(id, user_id, token_hash, auth_time, authentication_level, recovery_generation, "
        "expires_at, last_seen_at) VALUES (%s, %s, %s, %s, 'primary', %s, %s, %s)",
        (
            other_session_id,
            audit_context["user_id"],
            hashlib.sha256(opaque().encode("ascii")).digest(),
            now,
            audit_context["generation"],
            issued.expires_at,
            now,
        ),
    )
    identity.execute(
        "SELECT set_config('signal.identity_session_hash', %s, false)",
        (hashlib.sha256(issued.token.encode("ascii")).hexdigest(),),
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        identity.execute(
            "INSERT INTO control.platform_events "
            "(id, event_type, actor_user_id, object_kind, object_id, facts, reason) "
            "VALUES (%s, 'identity.session.issued', %s, 'identity_session', %s, %s, NULL)",
            (
                uuid4(),
                audit_context["user_id"],
                other_session_id,
                Jsonb({"schema_version": 1, "authentication_level": "primary"}),
            ),
        )
    identity.execute("RESET signal.identity_session_hash")


def test_audit_insert_failure_rolls_back_session(monkeypatch, identity, admin, audit_context):
    now = datetime.now(UTC).replace(microsecond=0)
    existing = issue_identity_session(
        identity,
        identity=verified(audit_context, now),
        current_recovery_generation=audit_context["generation"],
        now=now,
    )
    candidate_session_id = uuid4()
    identifiers = iter((candidate_session_id, existing.audit_event_id))
    monkeypatch.setattr(session_issuance, "uuid4", lambda: next(identifiers))
    candidate_token = opaque()

    with pytest.raises(RuntimeError, match="allocation failed"):
        issue_identity_session(
            identity,
            identity=verified(audit_context, now),
            current_recovery_generation=audit_context["generation"],
            now=now,
            token_factory=lambda: candidate_token,
        )

    assert (
        admin.execute(
            "SELECT count(*) FROM control.identity_sessions WHERE id = %s OR token_hash = %s",
            (candidate_session_id, hashlib.sha256(candidate_token.encode("ascii")).digest()),
        ).fetchone()[0]
        == 0
    )

    assert admin.execute(
        "SELECT count(*) FROM control.email_identity_claims WHERE session_id=%s",
        (candidate_session_id,),
    ).fetchone() == (0,)


def test_platform_event_insert_privilege_is_column_limited(admin):
    expected = {
        "id",
        "event_type",
        "actor_user_id",
        "object_kind",
        "object_id",
        "facts",
        "reason",
    }
    actual = {
        column
        for (column,) in admin.execute(
            "SELECT column_name FROM information_schema.column_privileges "
            "WHERE grantee = 'signal_identity' AND table_schema = 'control' "
            "AND table_name = 'platform_events' AND privilege_type = 'INSERT'"
        )
    }
    assert actual == expected
    for privilege in ["SELECT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]:
        assert not admin.execute(
            "SELECT has_table_privilege('signal_identity', 'control.platform_events', %s)",
            (privilege,),
        ).fetchone()[0]
    assert not admin.execute(
        "SELECT has_function_privilege("
        "'signal_identity', 'control.validate_platform_event_reference()', 'EXECUTE')"
    ).fetchone()[0]


def test_consumed_login_failure_event_is_typed_and_sanitized(identity, admin):
    attempt, state, browser_binding = consumed_attempt(identity)

    event_id = record_consumed_login_failure(
        identity,
        attempt=attempt,
        state=state,
        browser_binding=browser_binding,
        reason="provider_assertion_failed",
    )

    event = admin.execute(
        "SELECT id, event_type, actor_user_id, object_kind, object_id, facts, reason "
        "FROM control.platform_events WHERE id = %s",
        (event_id,),
    ).fetchone()
    assert event == (
        event_id,
        "identity.login.failed",
        None,
        "oidc_login_attempt",
        attempt.id,
        {"schema_version": 1},
        "provider_assertion_failed",
    )
    rendered = repr(event)
    assert state not in rendered
    assert browser_binding not in rendered
    assert attempt.pkce_secret_reference not in rendered

    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "INSERT INTO control.platform_events "
            "(id, event_type, actor_user_id, object_kind, object_id, facts, reason) "
            "VALUES (%s, 'identity.login.failed', NULL, 'oidc_login_attempt', %s, %s, "
            "'provider_assertion_failed')",
            (uuid4(), attempt.id, Jsonb({"schema_version": 1, "raw_token": "bad"})),
        )


@pytest.mark.parametrize(
    "reason",
    [
        "invitation_identity_not_verified",
        "invitation_proof_persistence_failed",
    ],
)
def test_invitation_completion_failure_reasons_are_closed_and_typed(identity, admin, reason):
    attempt, state, browser_binding = consumed_attempt(identity)
    event_id = record_consumed_login_failure(
        identity,
        attempt=attempt,
        state=state,
        browser_binding=browser_binding,
        reason=reason,
    )
    assert admin.execute(
        "SELECT event_type, object_id, facts, reason FROM control.platform_events WHERE id = %s",
        (event_id,),
    ).fetchone() == (
        "identity.login.failed",
        attempt.id,
        {"schema_version": 1},
        reason,
    )


def test_consumed_login_failure_requires_original_proofs_and_is_single_use(identity, admin):
    attempt, state, browser_binding = consumed_attempt(identity)

    with pytest.raises(OidcLoginAuditError):
        record_consumed_login_failure(
            identity,
            attempt=attempt,
            state=state,
            browser_binding=opaque(),
            reason="pkce_unavailable",
        )
    assert admin.execute(
        "SELECT count(*) FROM control.platform_events WHERE object_id = %s",
        (attempt.id,),
    ).fetchone() == (0,)

    record_consumed_login_failure(
        identity,
        attempt=attempt,
        state=state,
        browser_binding=browser_binding,
        reason="pkce_unavailable",
    )
    with pytest.raises(OidcLoginAuditError):
        record_consumed_login_failure(
            identity,
            attempt=attempt,
            state=state,
            browser_binding=browser_binding,
            reason="provider_assertion_failed",
        )


def test_login_failure_cannot_reference_an_unconsumed_attempt(identity, admin):
    state = opaque()
    browser_binding = opaque()
    attempt_id = create_oidc_login_attempt(
        identity,
        state=state,
        nonce=opaque(),
        browser_binding=browser_binding,
        registration=OidcClientRegistration(
            issuer="https://identity.example.test/realms/signal",
            client_id="signal-dashboard",
            redirect_uri="https://dashboard.example.test/auth/callback",
        ),
        pkce_secret_reference=f"secret://oidc-login/{uuid4()}/1",
        return_path="/",
    )

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin.execute(
            "INSERT INTO control.platform_events "
            "(id, event_type, actor_user_id, object_kind, object_id, facts, reason) "
            "VALUES (%s, 'identity.login.failed', NULL, 'oidc_login_attempt', %s, "
            "'{\"schema_version\":1}', 'pkce_unavailable')",
            (uuid4(), attempt_id),
        )


@pytest.mark.parametrize(
    "reason",
    [None, [], "unknown", "provider_assertion_failed\nprivate"],
)
def test_consumed_login_failure_rejects_untyped_reasons_before_database(identity, reason):
    attempt, state, browser_binding = consumed_attempt(identity)
    identity.close()

    with pytest.raises(OidcLoginAuditError):
        record_consumed_login_failure(
            identity,
            attempt=attempt,
            state=state,
            browser_binding=browser_binding,
            reason=reason,
        )
