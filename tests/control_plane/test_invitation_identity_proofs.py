import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from signal_core.invitation_identity_proofs import (
    InvitationIdentityProofDenied,
    cleanup_invitation_identity_proofs,
    issue_invitation_identity_proof,
)
from signal_core.oidc_protocol import VerifiedOidcIdentity


def opaque() -> str:
    return secrets.token_urlsafe(32)


def verified(now: datetime, **overrides) -> VerifiedOidcIdentity:
    values = {
        "issuer": "https://identity.example.test/realms/signal",
        "subject": f"invitee-{uuid4()}",
        "client_id": "signal-dashboard",
        "issued_at": int(now.timestamp()),
        "expires_at": int((now + timedelta(minutes=5)).timestamp()),
        "auth_time": int((now - timedelta(seconds=5)).timestamp()),
        "provider_session_id": f"provider-{uuid4()}",
        "authentication_context": "1",
        "verified_email": "invitee@example.test",
        **overrides,
    }
    return VerifiedOidcIdentity(**values)


def test_fresh_verified_identity_issues_hash_only_bounded_proof(identity, admin):
    now = datetime.now(UTC).replace(microsecond=0)
    raw = opaque()
    issued = issue_invitation_identity_proof(
        identity,
        identity=verified(now),
        now=now,
        token_factory=lambda: raw,
    )
    stored = admin.execute(
        "SELECT token_hash, oidc_issuer, oidc_subject, verified_email, "
        "identity_issued_at, identity_expires_at, expires_at, consumed_at "
        "FROM control.invitation_identity_proofs WHERE id = %s",
        (issued.id,),
    ).fetchone()
    assert stored[0] == hashlib.sha256(raw.encode("ascii")).digest()
    assert stored[1] == "https://identity.example.test/realms/signal"
    assert stored[3] == "invitee@example.test"
    assert stored[4] == now
    assert stored[5] == now + timedelta(minutes=5)
    assert stored[6] == issued.expires_at == now + timedelta(minutes=5)
    assert stored[7] is None
    assert raw not in repr(issued)
    assert raw.encode("ascii") not in stored[0]


def test_proof_lifetime_is_capped_at_ten_minutes(identity, admin):
    now = datetime.now(UTC).replace(microsecond=0)
    issued = issue_invitation_identity_proof(
        identity,
        identity=verified(now, expires_at=int((now + timedelta(hours=1)).timestamp())),
        now=now,
    )
    assert issued.expires_at == now + timedelta(minutes=10)
    assert admin.execute(
        "SELECT expires_at FROM control.invitation_identity_proofs WHERE id = %s",
        (issued.id,),
    ).fetchone() == (issued.expires_at,)


@pytest.mark.parametrize(
    "overrides",
    [
        {"verified_email": None},
        {"verified_email": "Invitee@example.test"},
        {"verified_email": "other@example.test\n"},
        {"issued_at": True},
        {"expires_at": False},
    ],
)
def test_unverified_or_malformed_identity_is_denied_before_database(identity, overrides):
    now = datetime.now(UTC).replace(microsecond=0)
    identity.close()
    with pytest.raises(InvitationIdentityProofDenied):
        issue_invitation_identity_proof(
            identity,
            identity=verified(now, **overrides),
            now=now,
        )


@pytest.mark.parametrize("timing", ["stale", "future", "expired", "reversed"])
def test_stale_or_invalid_provider_times_are_denied(identity, timing):
    now = datetime.now(UTC).replace(microsecond=0)
    overrides = {
        "stale": {"issued_at": int((now - timedelta(minutes=12)).timestamp())},
        "future": {"issued_at": int((now + timedelta(minutes=1)).timestamp())},
        "expired": {"expires_at": int((now - timedelta(seconds=1)).timestamp())},
        "reversed": {
            "issued_at": int(now.timestamp()),
            "expires_at": int((now - timedelta(seconds=1)).timestamp()),
        },
    }[timing]
    with pytest.raises(InvitationIdentityProofDenied):
        issue_invitation_identity_proof(
            identity,
            identity=verified(now, **overrides),
            now=now,
        )


def test_token_collision_retries_without_returning_colliding_secret(identity, admin):
    now = datetime.now(UTC).replace(microsecond=0)
    before = admin.execute("SELECT count(*) FROM control.invitation_identity_proofs").fetchone()[0]
    collision = opaque()
    replacement = opaque()
    first = issue_invitation_identity_proof(
        identity,
        identity=verified(now),
        now=now,
        token_factory=lambda: collision,
    )
    tokens = iter((collision, replacement))
    second = issue_invitation_identity_proof(
        identity,
        identity=verified(now, verified_email="second@example.test"),
        now=now,
        token_factory=lambda: next(tokens),
    )
    assert first.token == collision
    assert second.token == replacement
    after = admin.execute("SELECT count(*) FROM control.invitation_identity_proofs").fetchone()[0]
    assert after == before + 2


def test_terminal_collision_has_no_partial_proof(identity, admin):
    now = datetime.now(UTC).replace(microsecond=0)
    collision = opaque()
    issue_invitation_identity_proof(
        identity,
        identity=verified(now),
        now=now,
        token_factory=lambda: collision,
    )
    subject = f"terminal-{uuid4()}"
    with pytest.raises(RuntimeError, match="allocation failed"):
        issue_invitation_identity_proof(
            identity,
            identity=verified(now, subject=subject, verified_email="terminal@example.test"),
            now=now,
            token_factory=lambda: collision,
        )
    assert admin.execute(
        "SELECT count(*) FROM control.invitation_identity_proofs WHERE oidc_subject = %s",
        (subject,),
    ).fetchone() == (0,)


def test_identity_role_is_hash_scoped_and_cannot_mutate_proofs(identity, admin):
    now = datetime.now(UTC).replace(microsecond=0)
    issued = issue_invitation_identity_proof(
        identity,
        identity=verified(now),
        now=now,
    )
    assert identity.execute(
        "SELECT count(*) FROM control.invitation_identity_proofs"
    ).fetchone() == (0,)
    identity.execute(
        "SELECT set_config('signal.invitation_identity_proof_hash', %s, false)",
        (hashlib.sha256(issued.token.encode("ascii")).hexdigest(),),
    )
    assert identity.execute("SELECT id FROM control.invitation_identity_proofs").fetchone() == (
        issued.id,
    )
    identity.execute("RESET signal.invitation_identity_proof_hash")
    for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
        assert not admin.execute(
            "SELECT has_table_privilege("
            "'signal_identity', 'control.invitation_identity_proofs', %s)",
            (privilege,),
        ).fetchone()[0]


def test_proof_rows_are_immutable(identity, admin):
    now = datetime.now(UTC).replace(microsecond=0)
    issued = issue_invitation_identity_proof(
        identity,
        identity=verified(now),
        now=now,
    )
    for statement in (
        "UPDATE control.invitation_identity_proofs SET consumed_at = now() WHERE id = %s",
        "DELETE FROM control.invitation_identity_proofs WHERE id = %s",
    ):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(statement, (issued.id,))


def test_scheduler_cleanup_is_bounded_to_expired_proofs(identity, admin):
    now = datetime.now(UTC).replace(microsecond=0)
    live = issue_invitation_identity_proof(
        identity,
        identity=verified(now),
        now=now,
    )
    expired_ids = [uuid4(), uuid4()]
    for index, proof_id in enumerate(expired_ids):
        created_at = now - timedelta(minutes=20 + index)
        admin.execute(
            "INSERT INTO control.invitation_identity_proofs "
            "(id, token_hash, oidc_issuer, oidc_subject, verified_email, "
            "identity_issued_at, identity_expires_at, expires_at, created_at) "
            "VALUES (%s, %s, %s, %s, 'expired@example.test', %s, %s, %s, %s)",
            (
                proof_id,
                hashlib.sha256(f"expired-{proof_id}".encode("ascii")).digest(),
                "https://identity.example.test/realms/signal",
                f"expired-{proof_id}",
                created_at,
                created_at + timedelta(minutes=5),
                created_at + timedelta(minutes=5),
                created_at,
            ),
        )

    with psycopg.connect(os.environ["SIGNAL_TEST_SCHEDULER_DSN"], autocommit=True) as scheduler:
        expired_count = admin.execute(
            "SELECT count(*) FROM control.invitation_identity_proofs WHERE expires_at <= now()"
        ).fetchone()[0]
        assert expired_count >= 2
        for _ in range(expired_count):
            assert cleanup_invitation_identity_proofs(scheduler, batch_size=1) == 1
        assert cleanup_invitation_identity_proofs(scheduler, batch_size=1) == 0

    assert admin.execute(
        "SELECT count(*) FROM control.invitation_identity_proofs WHERE id = ANY(%s)",
        (expired_ids,),
    ).fetchone() == (0,)
    assert admin.execute(
        "SELECT id FROM control.invitation_identity_proofs WHERE id = %s", (live.id,)
    ).fetchone() == (live.id,)


def test_cleanup_privilege_is_scheduler_only_and_has_no_table_grant(admin):
    signature = "control.cleanup_invitation_identity_proofs(integer)"
    assert admin.execute(
        "SELECT has_function_privilege('signal_scheduler', %s, 'EXECUTE')",
        (signature,),
    ).fetchone() == (True,)
    for role in ("public", "signal_identity", "signal_api", "signal_bootstrap"):
        assert admin.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
            (role, signature),
        ).fetchone() == (False,)
    for privilege in ("SELECT", "DELETE"):
        assert admin.execute(
            "SELECT has_table_privilege("
            "'signal_scheduler', 'control.invitation_identity_proofs', %s)",
            (privilege,),
        ).fetchone() == (False,)


@pytest.mark.parametrize("batch_size", [None, True, 0, 1001, "10"])
def test_invalid_cleanup_batch_fails_before_database(batch_size):
    with psycopg.connect(os.environ["SIGNAL_TEST_SCHEDULER_DSN"], autocommit=True) as scheduler:
        scheduler.close()
        with pytest.raises(ValueError):
            cleanup_invitation_identity_proofs(scheduler, batch_size=batch_size)


def test_invalid_factories_and_time_fail_before_database(identity):
    now = datetime.now(UTC).replace(microsecond=0)
    cases = [
        {"now": now.replace(tzinfo=None)},
        {"token_factory": None},
        {"proof_id_factory": None},
        {"token_factory": lambda: "short"},
        {"proof_id_factory": lambda: UUID(int=0)},
    ]
    for overrides in cases:
        arguments = {
            "identity": verified(now),
            "now": now,
            **overrides,
        }
        with pytest.raises((ValueError, RuntimeError)):
            issue_invitation_identity_proof(
                identity,
                **arguments,
            )


def test_residual_proof_scope_contaminates_connection(identity):
    identity.execute(
        "SELECT set_config('signal.invitation_identity_proof_hash', %s, false)",
        ("a" * 64,),
    )
    now = datetime.now(UTC).replace(microsecond=0)
    with pytest.raises(ValueError, match="residual session scope"):
        issue_invitation_identity_proof(identity, identity=verified(now), now=now)
