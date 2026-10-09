import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from integration_secrets import OperatorError
from integration_test_owner import bootstrap_sql, validate_proof
from joserfc import jwt
from joserfc.jwk import RSAKey
from signal_api.test_identity_proof import CLIENT, TestAssertionCapture
from signal_core.oidc_login import ConsumedOidcLoginAttempt, OidcClientRegistration
from signal_core.oidc_protocol import OidcProtocolError, OidcTokenResponse, VerifiedOidcIdentity
from signal_core.session_issuance import SessionIssuanceDenied

EMAIL = "owner@example.invalid"
ISSUER = "https://signal-test.example.invalid/identity/realms/signal"
SUBJECT = "00000000-0000-4000-8000-000000000017"

KEY = RSAKey.generate_key(parameters={"kid": "test-owner-fixture", "alg": "RS256", "use": "sig"})
NOW = datetime.now(UTC).replace(microsecond=0)
TIMESTAMP = int(NOW.timestamp())


def fixture(changes=None):
    nonce = "n" * 43
    claims = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "aud": CLIENT,
        "iat": TIMESTAMP,
        "exp": TIMESTAMP + 300,
        "auth_time": TIMESTAMP - 1,
        "nonce": nonce,
        "acr": "1",
        "amr": ["otp"],
        "email": EMAIL,
        "email_verified": True,
    }
    claims.update(changes or {})
    identifier = str(uuid4())
    proof = {
        "schema_version": 1,
        "attempt_id": identifier,
        "id_token": jwt.encode({"alg": "RS256", "kid": "test-owner-fixture"}, claims, KEY),
        "access_token": "fixture-not-a-provider-token",
        "expires_in": 300,
    }
    row = {
        "id": identifier,
        "purpose": "login",
        "oidc_issuer": ISSUER,
        "client_id": CLIENT,
        "redirect_uri": "https://signal-test.example.invalid/auth/callback",
        "nonce_hash": hashlib.sha256(nonce.encode()).hexdigest(),
        "pkce_secret_reference": f"secret://oidc-login/{identifier}/1",
        "return_path": "/",
        "created_at": (NOW - timedelta(seconds=2)).isoformat(),
        "consumed_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(seconds=298)).isoformat(),
    }
    return (
        proof,
        row,
        {"keys": [KEY.as_dict(private=False)]},
        {
            "approved_at": TIMESTAMP - 3,
            "expires_at": TIMESTAMP + 3597,
        },
    )


def test_fresh_signed_identity_is_verified_without_granting_a_session():
    identity = validate_proof(*fixture(), now=NOW)
    assert identity.subject == SUBJECT
    assert identity.authentication_methods == frozenset({"otp"})


@pytest.mark.parametrize(
    "changes",
    [
        {"sub": "other-subject"},
        {"email": "other@example.invalid"},
        {"email_verified": False},
        {"amr": []},
        {"amr": ["pwd"]},
        {"acr": "0"},
        {"acr": "unknown"},
        {"auth_time": TIMESTAMP - 301},
        {"auth_time": None},
        {"iat": TIMESTAMP - 4},
        {"exp": TIMESTAMP - 1},
        {"iss": "https://other.invalid"},
        {"aud": "other"},
        {"nonce": "x" * 43},
    ],
)
def test_wrong_stale_or_missing_signed_proof_is_denied(changes):
    with pytest.raises((OperatorError, OidcProtocolError, SessionIssuanceDenied)):
        validate_proof(*fixture(changes), now=NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"purpose": "invitation_acceptance"},
        {"consumed_at": None},
        {"client_id": "other"},
        {"nonce_hash": "00" * 32},
        {"created_at": (NOW - timedelta(minutes=5)).isoformat()},
        {"expires_at": NOW.isoformat()},
    ],
)
def test_durable_consumed_attempt_binding_is_required(changes):
    proof, row, keys, approval = fixture()
    row.update(changes)
    with pytest.raises((OperatorError, OidcProtocolError, TypeError)):
        validate_proof(proof, row, keys, approval, now=NOW)


def test_signature_tampering_is_rejected():
    proof, row, keys, approval = fixture()
    proof["id_token"] = proof["id_token"][:-10] + "tamperedAA"
    with pytest.raises(OidcProtocolError):
        validate_proof(proof, row, keys, approval, now=NOW)


def capture_context():
    proof, row, _, approval = fixture()
    attempt = ConsumedOidcLoginAttempt(
        uuid4(),
        OidcClientRegistration(ISSUER, CLIENT, row["redirect_uri"]),
        bytes.fromhex(row["nonce_hash"]),
        row["pkce_secret_reference"],
        "/",
    )
    response = OidcTokenResponse(proof["id_token"], proof["access_token"], 300)
    identity = VerifiedOidcIdentity(
        ISSUER,
        SUBJECT,
        CLIENT,
        TIMESTAMP,
        TIMESTAMP + 300,
        TIMESTAMP - 1,
        None,
        "1",
        EMAIL,
        frozenset({"otp"}),
    )
    return attempt, response, identity, approval


def test_capture_is_owner_only_create_only_and_expiring(tmp_path, monkeypatch):
    monkeypatch.setattr("signal_api.test_identity_proof.time.time", lambda: TIMESTAMP)
    attempt, response, identity, approval = capture_context()
    path = tmp_path / "assertion"
    observer = TestAssertionCapture(approval, path)
    observer(attempt, response, identity)
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["attempt_id"] == str(attempt.id)
    original = path.read_bytes()
    observer(replace(attempt, id=uuid4()), response, identity)
    assert path.read_bytes() == original
    path.unlink()
    monkeypatch.setattr("signal_api.test_identity_proof.time.time", lambda: approval["expires_at"])
    observer(attempt, response, identity)
    assert not path.exists()


def test_capture_rejects_symlink_and_other_identity(tmp_path, monkeypatch):
    monkeypatch.setattr("signal_api.test_identity_proof.time.time", lambda: TIMESTAMP)
    attempt, response, identity, approval = capture_context()
    path = tmp_path / "assertion"
    target = tmp_path / "untouched"
    target.write_text("untouched")
    path.symlink_to(target)
    TestAssertionCapture(approval, path)(attempt, response, identity)
    assert target.read_text() == "untouched"
    with pytest.raises(ValueError):
        TestAssertionCapture(approval, path)(attempt, response, replace(identity, subject="other"))


def test_private_capture_expires_and_completion_stops_recapture(tmp_path, monkeypatch):
    monkeypatch.setattr("signal_api.test_identity_proof.time.time", lambda: TIMESTAMP)
    attempt, response, identity, approval = capture_context()
    path = tmp_path / "assertion.json"
    observer = TestAssertionCapture(approval, path)
    observer(attempt, response, identity)
    os.utime(path, (TIMESTAMP, TIMESTAMP))
    monkeypatch.setattr("signal_api.test_identity_proof.time.time", lambda: TIMESTAMP + 300)
    observer.expire()
    assert not path.exists()
    path.with_suffix(".done").touch()
    observer(attempt, response, identity)
    assert not path.exists()


@pytest.mark.parametrize(
    "approval",
    [
        {},
        {"approved_at": True, "expires_at": 2},
        {"approved_at": 2, "expires_at": 1},
        {"approved_at": 1, "expires_at": 3602},
    ],
)
def test_capture_rejects_invalid_approval(approval):
    with pytest.raises(ValueError):
        TestAssertionCapture(approval)


def test_private_operator_can_renew_window_without_restarting_workload(tmp_path, monkeypatch):
    monkeypatch.setattr("signal_api.test_identity_proof.time.time", lambda: TIMESTAMP)
    attempt, response, identity, approval = capture_context()
    expired = {"approved_at": TIMESTAMP - 3601, "expires_at": TIMESTAMP - 1}
    current = expired
    path = tmp_path / "assertion.json"
    observer = TestAssertionCapture(expired, path, approval_reader=lambda: current)
    observer(attempt, response, identity)
    assert not path.exists()
    current = approval
    observer(attempt, response, identity)
    assert path.exists()
    path.unlink()
    current = {"approved_at": TIMESTAMP + 1, "expires_at": TIMESTAMP + 3601}
    observer(attempt, response, identity)
    assert not path.exists()


@pytest.mark.parametrize("failure", ["missing", "malformed", "unsafe"])
def test_private_window_read_failure_denies_capture_and_purges_artifact(
    tmp_path,
    monkeypatch,
    failure,
):
    monkeypatch.setattr("signal_api.test_identity_proof.time.time", lambda: TIMESTAMP)
    attempt, response, identity, approval = capture_context()
    path = tmp_path / "assertion.json"

    def unavailable():
        if failure == "malformed":
            return {}
        if failure == "missing":
            raise FileNotFoundError("private fixture")
        raise RuntimeError("private fixture rejected")

    observer = TestAssertionCapture(approval, path, approval_reader=unavailable)
    with pytest.raises((ValueError, FileNotFoundError, RuntimeError)):
        observer(attempt, response, identity)
    assert not path.exists()
    path.write_text("private fixture")
    observer.expire()
    assert not path.exists()


def test_completed_capture_never_reads_a_renewed_or_removed_window(tmp_path):
    attempt, response, identity, approval = capture_context()
    path = tmp_path / "assertion.json"
    path.with_suffix(".done").touch()

    def unavailable():
        raise AssertionError("Completed capture cannot be reenabled by renewal")

    TestAssertionCapture(approval, path, approval_reader=unavailable)(attempt, response, identity)
    assert not path.exists()


def test_bootstrap_sql_is_create_only_auditable_and_cannot_grant_standing_authority():
    intent = {key: str(uuid4()) for key in ("user_id", "tenant_id", "membership_id", "attempt_id")}
    intent.update(approved_at=TIMESTAMP, nonce_hash="ab" * 32)
    statement = bootstrap_sql(intent, rehearse=True)
    assert statement.endswith("ROLLBACK;")
    assert "SET LOCAL ROLE signal_bootstrap" in statement
    assert "create-only" in statement
    assert "lock_timeout='5s'" in statement
    assert "INSERT INTO app.sites" not in statement
    assert "standing" not in statement
    assert "UPDATE " not in statement
    assert bootstrap_sql(intent, rehearse=False).endswith("COMMIT;")
