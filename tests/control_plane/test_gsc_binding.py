import hashlib
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.origin_verification import (
    OriginProofObservation,
    issue_origin_challenge,
    prepare_origin_verification,
    record_origin_verification,
)


def site_origin(site_id):
    return f"https://gsc-{site_id}.example.invalid"


def owner_and_verified_origin(admin, identity, identity_context, site_id):
    origin = site_origin(site_id)
    admin.execute("UPDATE app.sites SET primary_origin = %s WHERE id = %s", (origin, site_id))
    admin.execute(
        "UPDATE app.memberships SET role_key = 'owner', authorization_epoch = 2 WHERE id = %s",
        (identity_context["membership_id"],),
    )
    args = {
        "session_token": identity_context["session_token"],
        "current_recovery_generation": identity_context["generation"],
        "requested_site_id": site_id,
        "origin": origin,
        "idempotency_key": uuid4(),
    }
    challenge = issue_origin_challenge(identity, **args)
    prepared = prepare_origin_verification(
        identity,
        session_token=args["session_token"],
        current_recovery_generation=args["current_recovery_generation"],
        requested_site_id=site_id,
        challenge_id=challenge.challenge_id,
        origin=origin,
        idempotency_key=uuid4(),
    )
    record_origin_verification(
        identity,
        session_token=args["session_token"],
        current_recovery_generation=args["current_recovery_generation"],
        requested_site_id=site_id,
        challenge_id=challenge.challenge_id,
        origin=origin,
        idempotency_key=uuid4(),
        observation=OriginProofObservation(
            outcome="matched",
            http_status=200,
            media_type="text/plain",
            response_sha256=prepared.proof_sha256,
            final_url=prepared.proof_url,
            resolved_address="93.184.216.34",
            elapsed_ms=12,
        ),
    )


def session_args(identity_context, site_id):
    return (
        hashlib.sha256(identity_context["session_token"].encode()).digest(),
        identity_context["generation"],
        site_id,
    )


def begin(identity, identity_context, site_id, attempt_id, state=None):
    if state is None:
        state = attempt_id.bytes
    return identity.execute(
        "SELECT control.begin_gsc_oauth_attempt(%s, %s, %s, %s, %s, %s, %s)",
        (
            *session_args(identity_context, site_id),
            attempt_id,
            hashlib.sha256(state).digest(),
            "https://signal.example/oauth/gsc/callback",
            "A" * 43,
        ),
    ).fetchone()[0]


def test_owner_exact_property_binding_revocation_and_one_time_state(
    admin,
    api,
    identity,
    crawl_ingest,
    scopes,
    identity_context,
):
    site_id = scopes[0].site_id
    origin = site_origin(site_id)
    property_name = f"{origin}/"
    attempt_id = uuid4()
    assert begin(identity, identity_context, site_id, attempt_id, b"synthetic-state") == "denied"
    owner_and_verified_origin(admin, identity, identity_context, site_id)
    assert begin(identity, identity_context, site_id, attempt_id, b"synthetic-state") == "created"
    common = (*session_args(identity_context, site_id), attempt_id)
    callback = "https://signal.example/oauth/gsc/callback"
    assert identity.execute(
        "SELECT outcome FROM control.consume_gsc_oauth_attempt(%s, %s, %s, %s, %s, %s)",
        (*common, hashlib.sha256(b"wrong").digest(), callback),
    ).fetchone() == ("unavailable",)
    assert identity.execute(
        "SELECT outcome FROM control.consume_gsc_oauth_attempt(%s, %s, %s, %s, %s, %s)",
        (*common, hashlib.sha256(b"synthetic-state").digest(), callback),
    ).fetchone() == ("consumed",)
    assert identity.execute(
        "SELECT outcome FROM control.consume_gsc_oauth_attempt(%s, %s, %s, %s, %s, %s)",
        (*common, hashlib.sha256(b"synthetic-state").digest(), callback),
    ).fetchone() == ("unavailable",)
    candidates = [
        {"resource_name": property_name, "property_type": "url_prefix", "eligible": True},
        {
            "resource_name": "https://other.invalid/",
            "property_type": "url_prefix",
            "eligible": False,
        },
    ]
    assert identity.execute(
        "SELECT control.stage_gsc_oauth_attempt(%s, %s, %s, %s, %s, %s)",
        (*common, f"secret://gsc/{attempt_id}", Jsonb(candidates)),
    ).fetchone() == ("staged",)
    binding_id = uuid4()
    assert identity.execute(
        "SELECT control.confirm_gsc_binding(%s, %s, %s, %s, %s, %s, %s)",
        (*common, binding_id, uuid4(), "https://other.invalid/"),
    ).fetchone() == ("wrong_property",)
    assert identity.execute(
        "SELECT control.confirm_gsc_binding(%s, %s, %s, %s, %s, %s, %s)",
        (*common, binding_id, uuid4(), property_name),
    ).fetchone() == ("bound",)
    assert crawl_ingest.execute(
        "SELECT binding_id, property_resource_name, secret_reference, origin "
        "FROM control.current_gsc_binding(%s, %s)",
        (scopes[0].tenant_id, site_id),
    ).fetchone() == (binding_id, property_name, f"secret://gsc/{attempt_id}", origin)
    assert identity.execute(
        "SELECT outcome, secret_reference FROM control.revoke_gsc_binding(%s, %s, %s, %s, %s)",
        (*session_args(identity_context, site_id), binding_id, uuid4()),
    ).fetchone() == ("revoked", f"secret://gsc/{attempt_id}")
    assert (
        crawl_ingest.execute(
            "SELECT * FROM control.current_gsc_binding(%s, %s)",
            (scopes[0].tenant_id, site_id),
        ).fetchone()
        is None
    )


def test_wrong_site_stale_session_and_direct_mutation_fail_closed(
    admin,
    api,
    identity,
    scopes,
    identity_context,
):
    owner_and_verified_origin(admin, identity, identity_context, scopes[0].site_id)
    assert begin(identity, identity_context, scopes[1].site_id, uuid4()) == "denied"
    assert (
        begin(identity, {**identity_context, "generation": "stale"}, scopes[0].site_id, uuid4())
        == "denied"
    )
    attempt_id = uuid4()
    assert begin(identity, identity_context, scopes[0].site_id, attempt_id) == "created"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("DELETE FROM app.gsc_oauth_attempts")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("INSERT INTO app.gsc_binding_events DEFAULT VALUES")


def test_import_requires_current_binding_and_actual_egress_evidence(
    admin,
    api,
    identity,
    crawl_ingest,
    scopes,
    identity_context,
):
    owner_and_verified_origin(admin, identity, identity_context, scopes[0].site_id)
    property_name = f"{site_origin(scopes[0].site_id)}/"
    outcome = crawl_ingest.execute(
        "SELECT control.record_gsc_import_generation(" + ", ".join(["%s"] * 15) + ")",
        (
            scopes[0].tenant_id,
            scopes[0].site_id,
            uuid4(),
            uuid4(),
            property_name,
            "web",
            Jsonb(["date"]),
            "2026-09-01",
            "2026-09-28",
            "all",
            "auto",
            Jsonb([]),
            Jsonb(
                {
                    "complete": False,
                    "missing_data": "unknown_not_zero",
                    "filters": [],
                    "start_row": 0,
                    "row_limit": 5000,
                }
            ),
            hashlib.sha256(b"{}").digest(),
            uuid4(),
        ),
    ).fetchone()[0]
    assert outcome == "binding_unavailable"
