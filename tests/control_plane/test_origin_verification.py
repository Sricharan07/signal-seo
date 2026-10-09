import hashlib
import secrets
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from signal_core.authorization import InvalidSession
from signal_core.origin_verification import (
    CHALLENGE_PATH,
    InvalidOriginVerification,
    OriginChallengeExpired,
    OriginClaimConflict,
    OriginProofMismatch,
    OriginProofObservation,
    OriginProofUnavailable,
    OriginVerificationConflict,
    OriginVerificationDenied,
    PreparedOriginVerification,
    VerifiedOrigin,
    issue_origin_challenge,
    prepare_origin_verification,
    record_origin_verification,
)
from signal_core.session_management import list_tenant_sites


def make_owner(admin, identity_context):
    admin.execute(
        "UPDATE app.memberships SET role_key = 'owner', authorization_epoch = 2 WHERE id = %s",
        (identity_context["membership_id"],),
    )


def issue(identity, identity_context, site_id, **overrides):
    values = {
        "session_token": identity_context["session_token"],
        "current_recovery_generation": identity_context["generation"],
        "requested_site_id": site_id,
        "origin": "https://example.invalid",
        "idempotency_key": uuid4(),
        **overrides,
    }
    return issue_origin_challenge(identity, **values)


def prepare(identity, identity_context, challenge, **overrides):
    values = {
        "session_token": identity_context["session_token"],
        "current_recovery_generation": identity_context["generation"],
        "requested_site_id": challenge.site_id,
        "challenge_id": challenge.challenge_id,
        "origin": challenge.origin,
        "idempotency_key": uuid4(),
        **overrides,
    }
    return prepare_origin_verification(identity, **values)


def matching_observation(prepared):
    return OriginProofObservation(
        outcome="matched",
        http_status=200,
        media_type="text/plain",
        response_sha256=prepared.proof_sha256,
        final_url=prepared.proof_url,
        resolved_address="93.184.216.34",
        elapsed_ms=12,
    )


def record(identity, identity_context, prepared, request_id, **overrides):
    values = {
        "session_token": identity_context["session_token"],
        "current_recovery_generation": identity_context["generation"],
        "requested_site_id": prepared.site_id,
        "challenge_id": prepared.challenge_id,
        "origin": prepared.origin,
        "idempotency_key": request_id,
        "observation": matching_observation(prepared),
        **overrides,
    }
    return record_origin_verification(identity, **values)


def create_owner_context(admin, scope):
    user_id = uuid4()
    identity_session_id = uuid4()
    tenant_session_id = uuid4()
    membership_id = uuid4()
    site_membership_id = uuid4()
    session_token = secrets.token_urlsafe(32)
    now = admin.execute("SELECT statement_timestamp()").fetchone()[0]
    expires_at = now + timedelta(hours=1)
    generation = "second-owner-generation"
    admin.execute(
        "INSERT INTO control.users "
        "(id, oidc_issuer, oidc_subject, display_name, contact_email) "
        "VALUES (%s, 'https://identity.example.invalid', %s, 'Second owner', %s)",
        (user_id, f"subject-{user_id}", f"{user_id}@example.invalid"),
    )
    admin.execute(
        "INSERT INTO control.identity_sessions "
        "(id, user_id, token_hash, auth_time, authentication_level, recovery_generation, "
        "expires_at, last_seen_at) VALUES (%s, %s, %s, %s, 'primary', %s, %s, %s)",
        (
            identity_session_id,
            user_id,
            hashlib.sha256(secrets.token_bytes(32)).digest(),
            now,
            generation,
            expires_at,
            now,
        ),
    )
    admin.execute(
        "INSERT INTO app.memberships "
        "(tenant_id, id, user_id, role_key, state, authorization_epoch) "
        "VALUES (%s, %s, %s, 'owner', 'active', 1)",
        (scope.tenant_id, membership_id, user_id),
    )
    admin.execute(
        "INSERT INTO app.sessions "
        "(tenant_id, id, identity_session_id, user_id, session_token_hash, auth_time, "
        "mfa_level, expires_at, last_seen_at, active_site_id, session_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'primary', %s, %s, %s, 1)",
        (
            scope.tenant_id,
            tenant_session_id,
            identity_session_id,
            user_id,
            hashlib.sha256(session_token.encode("ascii")).digest(),
            now,
            expires_at,
            now,
            scope.site_id,
        ),
    )
    admin.execute(
        "INSERT INTO app.site_memberships "
        "(tenant_id, site_id, id, user_id, permission_set, authorization_epoch, state) "
        "VALUES (%s, %s, %s, %s, %s, 1, 'active')",
        (
            scope.tenant_id,
            scope.site_id,
            site_membership_id,
            user_id,
            '{"permissions":["site.snapshot.request"],"schema_version":1}',
        ),
    )
    return {"session_token": session_token, "generation": generation}


def test_owner_issues_exact_expiring_challenge_and_replays_without_raw_storage(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)
    request_id = uuid4()

    challenge = issue(
        identity,
        identity_context,
        scopes[0].site_id,
        idempotency_key=request_id,
    )
    replay = issue(
        identity,
        identity_context,
        scopes[0].site_id,
        idempotency_key=request_id,
    )

    assert challenge.origin == "https://example.invalid"
    assert challenge.proof_url == f"https://example.invalid{CHALLENGE_PATH}"
    assert challenge.proof_content == f"signal-site-verification={challenge.challenge_id}\n"
    assert challenge.expires_at - challenge.issued_at == timedelta(minutes=30)
    assert replay == type(challenge)(**{**challenge.__dict__, "replayed": True})
    stored = admin.execute(
        "SELECT proof_method, proof_path, proof_sha256, count(*) OVER () "
        "FROM app.site_origin_challenges WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone()
    assert stored[:2] == ("http_well_known", CHALLENGE_PATH)
    assert stored[2] == hashlib.sha256(challenge.proof_content.encode("ascii")).digest()
    assert stored[3] == 1
    assert admin.execute(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'app' "
        "AND table_name = 'site_origin_challenges' AND column_name = 'proof_content'"
    ).fetchone() == (0,)
    assert challenge.proof_content not in repr(challenge)


def test_challenge_requires_owner_exact_selected_site_and_canonical_origin(
    admin, identity, scopes, identity_context
):
    with pytest.raises(OriginVerificationDenied):
        issue(identity, identity_context, scopes[0].site_id)

    make_owner(admin, identity_context)
    with pytest.raises(InvalidSession):
        issue(identity, identity_context, scopes[1].site_id)
    for invalid_origin in (
        "http://example.invalid",
        "https://example.invalid/path",
        "https://www.example.invalid",
    ):
        with pytest.raises((InvalidOriginVerification, OriginVerificationDenied)):
            issue(
                identity,
                identity_context,
                scopes[0].site_id,
                origin=invalid_origin,
            )
    with pytest.raises(InvalidOriginVerification):
        issue(
            identity,
            identity_context,
            scopes[0].site_id,
            idempotency_key=object(),
        )


def test_challenge_request_conflicts_and_open_challenge_limit_are_bounded(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)
    request_id = uuid4()
    issue(identity, identity_context, scopes[0].site_id, idempotency_key=request_id)
    admin.execute(
        "UPDATE app.sites SET primary_origin = 'https://www.example.invalid' "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    )
    with pytest.raises(OriginVerificationConflict):
        issue(
            identity,
            identity_context,
            scopes[0].site_id,
            idempotency_key=request_id,
            origin="https://www.example.invalid",
        )
    admin.execute(
        "UPDATE app.sites SET primary_origin = 'https://example.invalid' "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    )

    for _ in range(9):
        issue(identity, identity_context, scopes[0].site_id)
    with pytest.raises(OriginVerificationConflict):
        issue(identity, identity_context, scopes[0].site_id)
    assert admin.execute(
        "SELECT count(*) FROM app.site_origin_challenges WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (10,)


def test_exact_proof_promotes_site_records_claim_and_replays(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)
    origin = f"https://origin-{scopes[0].site_id}.example.invalid"
    admin.execute(
        "UPDATE app.sites SET primary_origin = %s WHERE tenant_id = %s AND id = %s",
        (origin, scopes[0].tenant_id, scopes[0].site_id),
    )
    challenge = issue(identity, identity_context, scopes[0].site_id, origin=origin)
    request_id = uuid4()
    prepared = prepare(identity, identity_context, challenge, idempotency_key=request_id)
    assert isinstance(prepared, PreparedOriginVerification)

    verified = record(identity, identity_context, prepared, request_id)
    replay = record(identity, identity_context, prepared, request_id)
    prepared_replay = prepare(
        identity,
        identity_context,
        challenge,
        idempotency_key=request_id,
    )

    assert verified.proof_method == "http_well_known"
    assert verified.recheck_at - verified.verified_at == timedelta(days=30)
    assert replay == type(verified)(**{**verified.__dict__, "replayed": True})
    assert isinstance(prepared_replay, VerifiedOrigin)
    assert prepared_replay.replayed is True
    assert admin.execute(
        "SELECT state, ownership_status, row_version FROM app.sites "
        "WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == ("active", "verified", 2)
    assert admin.execute(
        "SELECT resource_identity, permitted_origins, revocation_conditions "
        "FROM app.site_origin_verifications WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (
        origin,
        [origin],
        ["origin_changed", "ownership_changed", "claim_revoked", "recheck_expired"],
    )
    assert admin.execute(
        "SELECT tenant_id, site_id, claim_generation FROM control.public_origin_claims "
        "WHERE origin = %s",
        (origin,),
    ).fetchone() == (scopes[0].tenant_id, scopes[0].site_id, 1)
    assert admin.execute(
        "SELECT count(*) FROM app.site_origin_verification_attempts "
        "WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (1,)
    directory = list_tenant_sites(
        identity,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
    )
    assert directory.sites[0].ownership_status == "verified"


def test_failed_exact_proof_is_durable_without_promoting_site(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)
    challenge = issue(identity, identity_context, scopes[0].site_id)
    request_id = uuid4()
    prepared = prepare(identity, identity_context, challenge, idempotency_key=request_id)
    mismatched = matching_observation(prepared)
    mismatched = OriginProofObservation(
        **{**mismatched.__dict__, "outcome": "proof_mismatch", "response_sha256": b"x" * 32}
    )

    with pytest.raises(OriginProofMismatch):
        record(
            identity,
            identity_context,
            prepared,
            request_id,
            observation=mismatched,
        )
    with pytest.raises(OriginProofMismatch):
        prepare(
            identity,
            identity_context,
            challenge,
            idempotency_key=request_id,
        )

    assert admin.execute(
        "SELECT outcome, response_sha256 FROM app.site_origin_verification_attempts "
        "WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == ("proof_mismatch", b"x" * 32)
    assert admin.execute(
        "SELECT state, ownership_status FROM app.sites WHERE tenant_id = %s AND id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == ("onboarding", "unverified")
    assert admin.execute(
        "SELECT count(*) FROM app.site_origin_verifications WHERE tenant_id = %s AND site_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id),
    ).fetchone() == (0,)


def test_authority_is_rechecked_after_network_preparation(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)
    challenge = issue(identity, identity_context, scopes[0].site_id)
    request_id = uuid4()
    prepared = prepare(identity, identity_context, challenge, idempotency_key=request_id)
    admin.execute(
        "UPDATE app.memberships SET role_key = 'admin', authorization_epoch = 3 WHERE id = %s",
        (identity_context["membership_id"],),
    )

    with pytest.raises(OriginVerificationDenied):
        record(identity, identity_context, prepared, request_id)

    assert admin.execute(
        "SELECT count(*) FROM app.site_origin_verification_attempts WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    ).fetchone() == (0,)


def test_expired_challenge_and_attempt_budget_fail_without_network_authority(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)
    expired = issue(identity, identity_context, scopes[0].site_id)
    admin.execute(
        "ALTER TABLE app.site_origin_challenges DISABLE TRIGGER site_origin_challenges_immutable"
    )
    try:
        admin.execute(
            "UPDATE app.site_origin_challenges "
            "SET issued_at = now() - interval '31 minutes', "
            "expires_at = now() - interval '1 minute' "
            "WHERE tenant_id = %s AND site_id = %s AND id = %s",
            (scopes[0].tenant_id, scopes[0].site_id, expired.challenge_id),
        )
    finally:
        admin.execute(
            "ALTER TABLE app.site_origin_challenges ENABLE TRIGGER site_origin_challenges_immutable"
        )
    with pytest.raises(OriginChallengeExpired):
        prepare(identity, identity_context, expired)

    challenge = issue(identity, identity_context, scopes[0].site_id)
    prepared_attempts = []
    for _ in range(11):
        request_id = uuid4()
        prepared = prepare(
            identity,
            identity_context,
            challenge,
            idempotency_key=request_id,
        )
        prepared_attempts.append((request_id, prepared))
    for request_id, prepared in prepared_attempts[:10]:
        with pytest.raises(OriginProofUnavailable):
            record(
                identity,
                identity_context,
                prepared,
                request_id,
                observation=OriginProofObservation(outcome="transport_unavailable"),
            )
    with pytest.raises(OriginVerificationConflict):
        record(
            identity,
            identity_context,
            prepared_attempts[10][1],
            prepared_attempts[10][0],
            observation=OriginProofObservation(outcome="transport_unavailable"),
        )
    with pytest.raises(OriginVerificationConflict):
        prepare(identity, identity_context, challenge)
    assert admin.execute(
        "SELECT count(*) FROM app.site_origin_verification_attempts "
        "WHERE tenant_id = %s AND site_id = %s AND challenge_id = %s",
        (scopes[0].tenant_id, scopes[0].site_id, challenge.challenge_id),
    ).fetchone() == (10,)


def test_global_claim_prevents_cross_tenant_write_authority_without_disclosure(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)
    origin = f"https://shared-{scopes[0].site_id}.example.invalid"
    admin.execute(
        "UPDATE app.sites SET primary_origin = %s WHERE tenant_id IN (%s, %s) AND id IN (%s, %s)",
        (
            origin,
            scopes[0].tenant_id,
            scopes[2].tenant_id,
            scopes[0].site_id,
            scopes[2].site_id,
        ),
    )
    first_challenge = issue(
        identity,
        identity_context,
        scopes[0].site_id,
        origin=origin,
    )
    first_request = uuid4()
    first_prepared = prepare(
        identity,
        identity_context,
        first_challenge,
        idempotency_key=first_request,
    )
    record(identity, identity_context, first_prepared, first_request)

    second_context = create_owner_context(admin, scopes[2])
    second_challenge = issue(
        identity,
        second_context,
        scopes[2].site_id,
        origin=origin,
    )
    second_request = uuid4()
    second_prepared = prepare(
        identity,
        second_context,
        second_challenge,
        idempotency_key=second_request,
    )
    with pytest.raises(OriginClaimConflict) as captured:
        record(identity, second_context, second_prepared, second_request)

    assert str(scopes[0].tenant_id) not in str(captured.value)
    assert admin.execute(
        "SELECT tenant_id, site_id FROM control.public_origin_claims WHERE origin = %s",
        (origin,),
    ).fetchone() == (scopes[0].tenant_id, scopes[0].site_id)
    assert admin.execute(
        "SELECT outcome FROM app.site_origin_verification_attempts "
        "WHERE tenant_id = %s AND site_id = %s",
        (scopes[2].tenant_id, scopes[2].site_id),
    ).fetchone() == ("claim_conflict",)
    assert admin.execute(
        "SELECT ownership_status FROM app.sites WHERE tenant_id = %s AND id = %s",
        (scopes[2].tenant_id, scopes[2].site_id),
    ).fetchone() == ("unverified",)


def test_identity_role_has_function_only_access(identity, admin, identity_context):
    for signature in (
        "control.issue_site_origin_challenge(bytea,text,uuid,uuid,uuid,bytea,text,integer,integer)",
        "control.prepare_site_origin_verification(bytea,text,uuid,uuid,uuid,bytea,text,integer)",
        "control.record_site_origin_verification(bytea,text,uuid,uuid,uuid,uuid,bytea,text,text,integer,text,bytea,text,text,integer,integer)",
    ):
        assert admin.execute(
            "SELECT has_function_privilege('signal_identity', %s, 'EXECUTE')", (signature,)
        ).fetchone() == (True,)
        assert admin.execute(
            "SELECT has_function_privilege('public', %s, 'EXECUTE')", (signature,)
        ).fetchone() == (False,)
    for table in (
        "app.site_origin_challenges",
        "app.site_origin_verification_attempts",
        "app.site_origin_verifications",
        "control.public_origin_claims",
    ):
        assert admin.execute(
            "SELECT has_table_privilege('signal_identity', %s, 'SELECT')", (table,)
        ).fetchone() == (False,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege), identity.transaction():
            identity.execute(f"SELECT * FROM {table}")
