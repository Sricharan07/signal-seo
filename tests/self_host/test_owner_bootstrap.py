"""Positive, negative and atomic failure checks on invocation-owned PostgreSQL."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest
from signal_core.oidc_login import OidcClientRegistration
from signal_core.oidc_protocol import VerifiedOidcIdentity
from signal_core.owner_bootstrap import (
    OwnerBootstrapIntent,
    OwnerBootstrapRejected,
    bootstrap_owner,
)
from signal_core.self_host_config import CLIENT
from signal_core.session_issuance import SessionIssuanceDenied


def context(admin, *, consumed=True):
    now = datetime.now(UTC).replace(microsecond=0)
    epoch = int(now.timestamp())
    issuer = "https://signal.example.invalid/identity/realms/signal"
    registration = OidcClientRegistration(
        issuer, CLIENT, "https://signal.example.invalid/auth/callback"
    )
    intent = OwnerBootstrapIntent(
        uuid4(),
        uuid4(),
        uuid4(),
        str(uuid4()),
        registration,
        "Synthetic workspace",
        "self-host",
        epoch - 3,
        epoch + 3597,
    )
    identity = VerifiedOidcIdentity(
        issuer,
        intent.subject,
        CLIENT,
        epoch,
        epoch + 300,
        epoch - 1,
        None,
        "1",
        None,
        frozenset({"otp"}),
    )
    attempt_id = uuid4()
    nonce_hash = sha256(b"synthetic-nonce").digest()
    admin.execute(
        "INSERT INTO control.oidc_login_attempts "
        "(id, state_hash, nonce_hash, browser_binding_hash, oidc_issuer, client_id, redirect_uri, "
        "pkce_secret_reference, return_path, created_at, expires_at, consumed_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'/',%s,%s,%s)",
        (
            attempt_id,
            sha256(b"synthetic-state").digest(),
            nonce_hash,
            sha256(b"synthetic-browser").digest(),
            issuer,
            CLIENT,
            registration.redirect_uri,
            f"secret://oidc-login/{attempt_id}/1",
            now - timedelta(seconds=2),
            now + timedelta(seconds=298),
            now if consumed else None,
        ),
    )
    return {
        "intent": intent,
        "identity": identity,
        "attempt_id": attempt_id,
        "nonce_hash": nonce_hash,
        "now": now,
    }


def test_only_first_owner_created_exact_retry_has_no_new_authority(admin):
    args = context(admin)
    assert bootstrap_owner(admin, **args)
    assert not bootstrap_owner(admin, **args)
    assert admin.execute("SELECT role_key,state FROM app.memberships").fetchall() == [
        ("owner", "active")
    ]
    assert admin.execute("SELECT count(*) FROM app.sites").fetchone() == (0,)
    assert admin.execute("SELECT count(*) FROM control.identity_sessions").fetchone() == (0,)
    assert admin.execute("SELECT current_user").fetchone() == ("postgres",)


@pytest.mark.parametrize(
    "change", ["subject", "client", "otp", "stale", "unconsumed", "nonce", "expired"]
)
def test_proof_denials_leave_database_empty(admin, change):
    args = context(admin, consumed=change != "unconsumed")
    if change == "subject":
        args["identity"] = replace(args["identity"], subject="synthetic-other")
    elif change == "client":
        args["identity"] = replace(args["identity"], client_id="synthetic-other")
    elif change == "otp":
        args["identity"] = replace(args["identity"], authentication_methods=frozenset({"pwd"}))
    elif change == "stale":
        args["identity"] = replace(args["identity"], auth_time=int(args["now"].timestamp()) - 301)
    elif change == "nonce":
        args["nonce_hash"] = b"x" * 32
    elif change == "expired":
        args["intent"] = replace(args["intent"], expires_at=int(args["now"].timestamp()))
    with pytest.raises((OwnerBootstrapRejected, SessionIssuanceDenied)):
        bootstrap_owner(admin, **args)
    assert admin.execute("SELECT count(*) FROM control.users").fetchone() == (0,)
    assert admin.execute("SELECT count(*) FROM app.tenants").fetchone() == (0,)


def test_existing_owner_is_not_repaired_or_reenabled(admin):
    args = context(admin)
    bootstrap_owner(admin, **args)
    admin.execute("UPDATE control.users SET disabled_at=statement_timestamp()")
    with pytest.raises(OwnerBootstrapRejected):
        bootstrap_owner(admin, **args)


def test_late_database_failure_rolls_back_user_and_restores_role(admin):
    args = context(admin)
    args["intent"] = replace(args["intent"], workspace_name="")
    with pytest.raises(psycopg.errors.CheckViolation):
        bootstrap_owner(admin, **args)
    assert admin.execute("SELECT count(*) FROM control.users").fetchone() == (0,)
    assert admin.execute("SELECT current_user").fetchone() == ("postgres",)


def test_runtime_identity_role_cannot_exercise_bootstrap(admin):
    args = context(admin)
    admin.execute("SET ROLE signal_identity")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            bootstrap_owner(admin, **args)
    finally:
        admin.execute("RESET ROLE")
