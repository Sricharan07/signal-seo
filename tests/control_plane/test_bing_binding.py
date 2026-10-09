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


def origin(site_id):
    return f"https://bing-{site_id}.example.invalid"


def owner_and_origin(admin, identity, context, site_id):
    site_origin = origin(site_id)
    admin.execute("UPDATE app.sites SET primary_origin = %s WHERE id = %s", (site_origin, site_id))
    admin.execute(
        "UPDATE app.memberships SET role_key = 'owner', authorization_epoch = 2 WHERE id = %s",
        (context["membership_id"],),
    )
    args = {
        "session_token": context["session_token"],
        "current_recovery_generation": context["generation"],
        "requested_site_id": site_id,
        "origin": site_origin,
        "idempotency_key": uuid4(),
    }
    challenge = issue_origin_challenge(identity, **args)
    prepared = prepare_origin_verification(
        identity,
        session_token=args["session_token"],
        current_recovery_generation=args["current_recovery_generation"],
        requested_site_id=site_id,
        challenge_id=challenge.challenge_id,
        origin=site_origin,
        idempotency_key=uuid4(),
    )
    record_origin_verification(
        identity,
        session_token=args["session_token"],
        current_recovery_generation=args["current_recovery_generation"],
        requested_site_id=site_id,
        challenge_id=challenge.challenge_id,
        origin=site_origin,
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


def session_args(context, site_id):
    return (
        hashlib.sha256(context["session_token"].encode()).digest(),
        context["generation"],
        site_id,
    )


def test_owner_confirmed_exact_site_replay_wrong_site_and_revoke(
    admin,
    api,
    identity,
    crawl_ingest,
    scopes,
    identity_context,
):
    site_id = scopes[0].site_id
    attempt = uuid4()
    state_hash = hashlib.sha256(b"synthetic-state").digest()
    callback = "https://signal.example/oauth/bing/callback"
    common = (*session_args(identity_context, site_id), attempt)
    begin = "SELECT control.begin_bing_oauth_attempt(%s, %s, %s, %s, %s, %s)"
    assert identity.execute(begin, (*common, state_hash, callback)).fetchone() == ("denied",)
    owner_and_origin(admin, identity, identity_context, site_id)
    assert identity.execute(begin, (*common, state_hash, callback)).fetchone() == ("created",)
    consume = "SELECT outcome FROM control.consume_bing_oauth_attempt(%s, %s, %s, %s, %s, %s)"
    assert identity.execute(
        consume, (*common, hashlib.sha256(b"bad").digest(), callback)
    ).fetchone() == ("unavailable",)
    assert identity.execute(consume, (*common, state_hash, callback)).fetchone() == ("consumed",)
    assert identity.execute(consume, (*common, state_hash, callback)).fetchone() == ("unavailable",)
    exact = origin(site_id) + "/"
    assert identity.execute(
        "SELECT control.stage_bing_oauth_attempt(%s, %s, %s, %s, %s, %s)",
        (
            *common,
            f"secret://bing/{attempt}",
            Jsonb(
                [
                    {"url": exact, "eligible": True},
                    {"url": "https://wrong.example/", "eligible": True},
                ]
            ),
        ),
    ).fetchone() == ("staged",)
    bind = "SELECT control.confirm_bing_binding(%s, %s, %s, %s, %s, %s, %s)"
    binding_id = uuid4()
    assert identity.execute(
        bind, (*common, binding_id, uuid4(), "https://wrong.example/")
    ).fetchone() == ("wrong_property",)
    assert identity.execute(bind, (*common, binding_id, uuid4(), exact)).fetchone() == ("bound",)
    assert crawl_ingest.execute(
        "SELECT binding_id, property_resource_name FROM control.current_bing_binding(%s, %s)",
        (scopes[0].tenant_id, site_id),
    ).fetchone() == (binding_id, exact)
    assert crawl_ingest.execute(
        "SELECT control.record_bing_import_generation(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            scopes[0].tenant_id,
            site_id,
            uuid4(),
            binding_id,
            exact,
            "own_site_inbound_link_details",
            Jsonb([]),
            Jsonb(
                {
                    "complete": False,
                    "missing_data": "unknown",
                    "source": "bing_webmaster",
                    "target_url": exact,
                }
            ),
            hashlib.sha256(b"{}").digest(),
            uuid4(),
        ),
    ).fetchone() == ("egress_evidence_unavailable",)
    assert identity.execute(
        "SELECT outcome, secret_reference FROM control.revoke_bing_binding(%s, %s, %s, %s, %s)",
        (*session_args(identity_context, site_id), binding_id, uuid4()),
    ).fetchone() == ("revoked", f"secret://bing/{attempt}")
    assert (
        crawl_ingest.execute(
            "SELECT * FROM control.current_bing_binding(%s, %s)",
            (scopes[0].tenant_id, site_id),
        ).fetchone()
        is None
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("DELETE FROM app.bing_binding_events")


def test_wrong_site_stale_session_and_unsupported_evidence_fail_closed(
    admin,
    api,
    identity,
    crawl_ingest,
    scopes,
    identity_context,
):
    site_id = scopes[0].site_id
    owner_and_origin(admin, identity, identity_context, site_id)
    begin = "SELECT control.begin_bing_oauth_attempt(%s, %s, %s, %s, %s, %s)"
    assert identity.execute(
        begin,
        (
            *session_args(identity_context, scopes[1].site_id),
            uuid4(),
            hashlib.sha256(b"s").digest(),
            "https://signal.example/bing/callback",
        ),
    ).fetchone() == ("denied",)
    stale = {**identity_context, "generation": "stale"}
    assert identity.execute(
        begin,
        (
            *session_args(stale, site_id),
            uuid4(),
            hashlib.sha256(b"s").digest(),
            "https://signal.example/bing/callback",
        ),
    ).fetchone() == ("denied",)
    assert crawl_ingest.execute(
        "SELECT control.record_bing_import_generation(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            scopes[0].tenant_id,
            site_id,
            uuid4(),
            uuid4(),
            origin(site_id) + "/",
            "performance",
            Jsonb([]),
            Jsonb({"complete": False, "missing_data": "unknown", "source": "bing_webmaster"}),
            hashlib.sha256(b"{}").digest(),
            uuid4(),
        ),
    ).fetchone() == ("binding_unavailable",)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        crawl_ingest.execute(
            "SELECT control.record_bing_import_generation(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                scopes[0].tenant_id,
                site_id,
                uuid4(),
                uuid4(),
                origin(site_id) + "/",
                "performance",
                Jsonb([]),
                Jsonb({"complete": True, "missing_data": "zero", "source": "bing_webmaster"}),
                hashlib.sha256(b"{}").digest(),
                uuid4(),
            ),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("INSERT INTO app.bing_import_generations DEFAULT VALUES")
