import hashlib
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from signal_core.oidc_login import (
    InvalidOidcLoginAttempt,
    OidcClientRegistration,
    OidcLoginStateConflict,
    consume_oidc_login_attempt,
    create_oidc_login_attempt,
    read_oidc_login_purpose,
)

REGISTRATION = OidcClientRegistration(
    issuer="https://identity.example.test/realms/signal",
    client_id="signal-dashboard",
    redirect_uri="https://signal.example.test/v1/auth/callback",
)


def opaque() -> str:
    return secrets.token_urlsafe(32)


def create_attempt(identity, *, state=None, nonce=None, binding=None, purpose="login"):
    values = {
        "state": state or opaque(),
        "nonce": nonce or opaque(),
        "binding": binding or opaque(),
    }
    values["id"] = create_oidc_login_attempt(
        identity,
        state=values["state"],
        nonce=values["nonce"],
        browser_binding=values["binding"],
        registration=REGISTRATION,
        pkce_secret_reference=f"secret://oidc-login/{uuid4()}/1",
        return_path="/sites?view=overview",
        purpose=purpose,
    )
    return values


def test_attempt_persists_only_hashes_and_consumes_once(identity, admin):
    attempt = create_attempt(identity)
    assert identity.execute("SELECT count(*) FROM control.oidc_login_attempts").fetchone()[0] == 0

    stored = admin.execute(
        "SELECT state_hash, nonce_hash, browser_binding_hash, consumed_at, purpose "
        "FROM control.oidc_login_attempts WHERE id = %s",
        (attempt["id"],),
    ).fetchone()
    assert stored[:3] == tuple(
        hashlib.sha256(attempt[key].encode("ascii")).digest()
        for key in ("state", "nonce", "binding")
    )
    assert stored[3:] == (None, "login")
    for raw_value in (attempt["state"], attempt["nonce"], attempt["binding"]):
        assert raw_value.encode("ascii") not in b"".join(stored[:3])

    consumed = consume_oidc_login_attempt(
        identity, state=attempt["state"], browser_binding=attempt["binding"]
    )
    assert consumed.id == attempt["id"]
    assert consumed.registration == REGISTRATION
    assert consumed.return_path == "/sites?view=overview"
    assert consumed.purpose == "login"
    consumed.validate_nonce(attempt["nonce"])
    with pytest.raises(InvalidOidcLoginAttempt):
        consumed.validate_nonce(opaque())
    with pytest.raises(InvalidOidcLoginAttempt):
        consume_oidc_login_attempt(
            identity, state=attempt["state"], browser_binding=attempt["binding"]
        )


def test_invitation_attempt_purpose_is_immutable_and_returned(identity, admin):
    attempt = create_attempt(identity, purpose="invitation_acceptance")
    assert (
        read_oidc_login_purpose(
            identity,
            state=attempt["state"],
            browser_binding=attempt["binding"],
        )
        == "invitation_acceptance"
    )
    consumed = consume_oidc_login_attempt(
        identity,
        state=attempt["state"],
        browser_binding=attempt["binding"],
    )
    assert consumed.purpose == "invitation_acceptance"
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE control.oidc_login_attempts SET purpose = 'login' WHERE id = %s",
            (attempt["id"],),
        )


def test_purpose_read_requires_exact_live_proofs_without_consuming(identity, admin):
    attempt = create_attempt(identity)
    with pytest.raises(InvalidOidcLoginAttempt):
        read_oidc_login_purpose(
            identity,
            state=attempt["state"],
            browser_binding=opaque(),
        )
    assert admin.execute(
        "SELECT consumed_at IS NULL FROM control.oidc_login_attempts WHERE id = %s",
        (attempt["id"],),
    ).fetchone() == (True,)
    assert (
        read_oidc_login_purpose(
            identity,
            state=attempt["state"],
            browser_binding=attempt["binding"],
        )
        == "login"
    )
    consume_oidc_login_attempt(
        identity,
        state=attempt["state"],
        browser_binding=attempt["binding"],
    )
    with pytest.raises(InvalidOidcLoginAttempt):
        read_oidc_login_purpose(
            identity,
            state=attempt["state"],
            browser_binding=attempt["binding"],
        )


def test_wrong_binding_is_indistinguishable_and_does_not_consume(identity, admin):
    attempt = create_attempt(identity)
    with pytest.raises(InvalidOidcLoginAttempt):
        consume_oidc_login_attempt(identity, state=attempt["state"], browser_binding=opaque())
    assert admin.execute(
        "SELECT consumed_at IS NULL FROM control.oidc_login_attempts WHERE id = %s",
        (attempt["id"],),
    ).fetchone()[0]
    assert (
        consume_oidc_login_attempt(
            identity, state=attempt["state"], browser_binding=attempt["binding"]
        ).id
        == attempt["id"]
    )


@pytest.mark.parametrize("value", [None, "", "short", "x" * 42, "x" * 44, "bad+token"])
def test_malformed_browser_values_fail_before_database_access(identity, value):
    identity.close()
    with pytest.raises((ValueError, InvalidOidcLoginAttempt)):
        consume_oidc_login_attempt(identity, state=value, browser_binding=opaque())


def test_expired_attempt_cannot_be_consumed(identity, admin):
    state = opaque()
    nonce = opaque()
    binding = opaque()
    created_at = datetime.now(UTC) - timedelta(minutes=10)
    admin.execute(
        "INSERT INTO control.oidc_login_attempts "
        "(id, state_hash, nonce_hash, browser_binding_hash, oidc_issuer, client_id, "
        "redirect_uri, pkce_secret_reference, return_path, created_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '/', %s, %s)",
        (
            uuid4(),
            hashlib.sha256(state.encode("ascii")).digest(),
            hashlib.sha256(nonce.encode("ascii")).digest(),
            hashlib.sha256(binding.encode("ascii")).digest(),
            REGISTRATION.issuer,
            REGISTRATION.client_id,
            REGISTRATION.redirect_uri,
            f"secret://oidc-login/{uuid4()}/1",
            created_at,
            created_at + timedelta(minutes=5),
        ),
    )
    with pytest.raises(InvalidOidcLoginAttempt):
        consume_oidc_login_attempt(identity, state=state, browser_binding=binding)


def test_duplicate_state_is_a_safe_conflict(identity):
    state = opaque()
    create_attempt(identity, state=state)
    with pytest.raises(OidcLoginStateConflict):
        create_attempt(identity, state=state)


def test_concurrent_callback_consumes_exactly_once(identity):
    attempt = create_attempt(identity)

    def consume_once():
        with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as connection:
            try:
                return consume_oidc_login_attempt(
                    connection,
                    state=attempt["state"],
                    browser_binding=attempt["binding"],
                ).id
            except InvalidOidcLoginAttempt:
                return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume_once(), range(2)))
    assert results.count(attempt["id"]) == 1
    assert results.count(None) == 1


def test_identity_role_is_hash_scoped_and_column_limited(identity, admin):
    attempt = create_attempt(identity)
    assert identity.execute("SELECT count(*) FROM control.oidc_login_attempts").fetchone()[0] == 0
    identity.execute(
        "SELECT set_config('signal.oidc_state_hash', %s, false)",
        (hashlib.sha256(attempt["state"].encode("ascii")).hexdigest(),),
    )
    assert identity.execute("SELECT count(*) FROM control.oidc_login_attempts").fetchone()[0] == 0
    identity.execute("RESET signal.oidc_state_hash")
    assert not admin.execute(
        "SELECT has_table_privilege('signal_identity', 'control.oidc_login_attempts', 'UPDATE')"
    ).fetchone()[0]
    assert admin.execute(
        "SELECT has_column_privilege('signal_identity', "
        "'control.oidc_login_attempts', 'consumed_at', 'UPDATE')"
    ).fetchone()[0]
    assert not admin.execute(
        "SELECT has_column_privilege('signal_identity', "
        "'control.oidc_login_attempts', 'redirect_uri', 'UPDATE')"
    ).fetchone()[0]
    assert not admin.execute(
        "SELECT has_table_privilege('signal_identity', 'control.oidc_login_attempts', 'DELETE')"
    ).fetchone()[0]

    consume_oidc_login_attempt(identity, state=attempt["state"], browser_binding=attempt["binding"])
    state_hash = hashlib.sha256(attempt["state"].encode("ascii")).hexdigest()
    binding_hash = hashlib.sha256(attempt["binding"].encode("ascii")).hexdigest()
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState), identity.transaction():
        identity.execute("SELECT set_config('signal.oidc_state_hash', %s, true)", (state_hash,))
        identity.execute(
            "SELECT set_config('signal.oidc_browser_binding_hash', %s, true)",
            (binding_hash,),
        )
        identity.execute(
            "UPDATE control.oidc_login_attempts SET consumed_at = now() WHERE id = %s",
            (attempt["id"],),
        )


def test_residual_oidc_scope_contaminates_connection(identity):
    identity.execute("SELECT set_config('signal.oidc_state_hash', %s, false)", ("a" * 64,))
    with pytest.raises(ValueError, match="residual session scope"):
        create_attempt(identity)


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer", "http://identity.example.test/realms/signal"),
        ("issuer", "https://*.example.test/realms/signal"),
        ("issuer", "https://user@example.test/realms/signal"),
        ("issuer", "https://example.test/realm?query=1"),
        ("client_id", "bad client"),
        ("client_id", None),
        ("redirect_uri", "//example.test/callback"),
        ("redirect_uri", "https://example.test/callback#fragment"),
    ],
)
def test_registration_rejects_ambiguous_or_unsafe_values(field, value):
    values = {
        "issuer": REGISTRATION.issuer,
        "client_id": REGISTRATION.client_id,
        "redirect_uri": REGISTRATION.redirect_uri,
    }
    values[field] = value
    with pytest.raises(ValueError):
        OidcClientRegistration(**values)


def test_loopback_http_registration_is_available_for_disposable_labs():
    registration = OidcClientRegistration(
        issuer="http://localhost:8080/realms/signal",
        client_id="signal-lab",
        redirect_uri="http://127.0.0.1:8000/v1/auth/callback",
    )
    assert registration.client_id == "signal-lab"


@pytest.mark.parametrize(
    "keyword,value",
    [
        ("attempt_id", UUID(int=0)),
        ("pkce_secret_reference", "plain-verifier"),
        ("registration", None),
        ("return_path", "https://attacker.example"),
        ("return_path", "//attacker.example"),
        ("return_path", "/bad\\path"),
        ("return_path", "/caf\u00e9"),
        ("ttl_seconds", 59),
        ("ttl_seconds", 601),
        ("purpose", "password_reset"),
    ],
)
def test_attempt_input_contracts_fail_before_database_access(identity, keyword, value):
    arguments = {
        "state": opaque(),
        "nonce": opaque(),
        "browser_binding": opaque(),
        "registration": REGISTRATION,
        "pkce_secret_reference": f"secret://oidc-login/{uuid4()}/1",
        "return_path": "/",
        "ttl_seconds": 300,
    }
    arguments[keyword] = value
    identity.close()
    with pytest.raises(ValueError):
        create_oidc_login_attempt(identity, **arguments)


def test_consumed_attempt_representation_hides_nonce_and_secret_reference(identity):
    attempt = create_attempt(identity)
    consumed = consume_oidc_login_attempt(
        identity, state=attempt["state"], browser_binding=attempt["binding"]
    )
    rendered = repr(consumed)
    assert "secret://" not in rendered
    assert consumed.nonce_hash.hex() not in rendered
