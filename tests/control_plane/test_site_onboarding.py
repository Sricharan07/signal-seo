import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from signal_core.authorization import InvalidSession
from signal_core.session_management import select_session_site
from signal_core.site_onboarding import (
    InvalidSiteOnboarding,
    SiteLimitReached,
    SiteOnboardingConflict,
    SiteOnboardingDenied,
    normalize_public_site_origin,
    onboard_site,
)


def make_owner(admin, identity_context):
    admin.execute(
        "UPDATE app.memberships SET role_key = 'owner', authorization_epoch = 2 WHERE id = %s",
        (identity_context["membership_id"],),
    )


def onboard(identity, identity_context, **overrides):
    values = {
        "session_token": identity_context["session_token"],
        "current_recovery_generation": identity_context["generation"],
        "name": "New public site",
        "primary_origin": "https://www.example.com",
        "timezone": "America/Phoenix",
        "reporting_currency": "USD",
        "expected_session_version": 2,
        "idempotency_key": uuid4(),
        **overrides,
    }
    return onboard_site(identity, **values)


def test_owner_atomically_creates_grants_selects_and_audits_site(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)
    request_id = uuid4()

    created = onboard(identity, identity_context, idempotency_key=request_id)

    assert created.tenant_id == scopes[0].tenant_id
    assert created.user_id == identity_context["user_id"]
    assert created.name == "New public site"
    assert created.primary_origin == "https://www.example.com"
    assert created.timezone == "America/Phoenix"
    assert created.reporting_currency == "USD"
    assert created.session_version == 3
    assert created.replayed is False
    assert admin.execute(
        "SELECT name, primary_origin, timezone, reporting_currency, state, "
        "ownership_status FROM app.sites WHERE tenant_id = %s AND id = %s",
        (created.tenant_id, created.site_id),
    ).fetchone() == (
        "New public site",
        "https://www.example.com",
        "America/Phoenix",
        "USD",
        "onboarding",
        "unverified",
    )
    assert admin.execute(
        "SELECT user_id, permission_set, authorization_epoch, state "
        "FROM app.site_memberships WHERE tenant_id = %s AND site_id = %s",
        (created.tenant_id, created.site_id),
    ).fetchone() == (
        identity_context["user_id"],
        {"permissions": ["site.snapshot.request"], "schema_version": 1},
        1,
        "active",
    )
    assert admin.execute(
        "SELECT active_site_id, session_version FROM app.sessions WHERE id = %s",
        (identity_context["tenant_session_id"],),
    ).fetchone() == (created.site_id, 3)
    event = admin.execute(
        "SELECT event_type, schema_version, scope_kind, idempotency_key, role_key, "
        "authentication_level, membership_authorization_epoch, "
        "site_authorization_epoch, session_version, octet_length(request_hash), "
        "octet_length(event_hash) FROM app.site_onboarding_events "
        "WHERE tenant_id = %s AND site_id = %s",
        (created.tenant_id, created.site_id),
    ).fetchone()
    assert event == (
        "site.onboarded",
        1,
        "tenant",
        request_id,
        "owner",
        "primary",
        2,
        1,
        3,
        32,
        32,
    )
    assert admin.execute(
        "SELECT primary_origin, state FROM control.tenant_site_routes "
        "WHERE tenant_id = %s AND site_id = %s",
        (created.tenant_id, created.site_id),
    ).fetchone() == ("https://www.example.com", "onboarding")
    context_event = admin.execute(
        "SELECT session_version, previous_hash, event_hash "
        "FROM app.session_site_context_events "
        "WHERE tenant_id = %s AND site_id = %s",
        (created.tenant_id, created.site_id),
    ).fetchone()
    assert context_event[:2] == (3, None)
    assert len(context_event[2]) == 32


def test_onboarding_context_event_continues_the_active_site_chain(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)
    created = onboard(identity, identity_context)
    onboarding_hash = admin.execute(
        "SELECT event_hash FROM app.session_site_context_events "
        "WHERE tenant_id = %s AND site_id = %s",
        (created.tenant_id, created.site_id),
    ).fetchone()[0]

    selected = select_session_site(
        identity,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
        requested_site_id=scopes[0].site_id,
        expected_session_version=3,
    )

    assert selected.session_version == 4
    assert admin.execute(
        "SELECT previous_hash FROM app.session_site_context_events "
        "WHERE tenant_id = %s AND site_id = %s AND session_version = 4",
        (created.tenant_id, scopes[0].site_id),
    ).fetchone() == (onboarding_hash,)


def test_exact_retry_returns_the_original_receipt_without_duplicate_rows(
    admin, identity, identity_context
):
    make_owner(admin, identity_context)
    request_id = uuid4()
    first = onboard(identity, identity_context, idempotency_key=request_id)

    replay = onboard(identity, identity_context, idempotency_key=request_id)

    assert replay == type(first)(**{**first.__dict__, "replayed": True})
    assert admin.execute(
        "SELECT count(*) FROM app.site_onboarding_events WHERE idempotency_key = %s",
        (request_id,),
    ).fetchone() == (1,)
    assert admin.execute(
        "SELECT count(*) FROM app.session_site_context_events "
        "WHERE tenant_id = %s AND site_id = %s",
        (first.tenant_id, first.site_id),
    ).fetchone() == (1,)
    assert admin.execute(
        "SELECT count(*) FROM app.sites WHERE tenant_id = %s AND id = %s",
        (first.tenant_id, first.site_id),
    ).fetchone() == (1,)


def test_reused_request_identity_with_different_input_conflicts(admin, identity, identity_context):
    make_owner(admin, identity_context)
    request_id = uuid4()
    onboard(identity, identity_context, idempotency_key=request_id)

    with pytest.raises(SiteOnboardingConflict):
        onboard(
            identity,
            identity_context,
            idempotency_key=request_id,
            name="Different site",
        )

    assert admin.execute(
        "SELECT count(*) FROM app.site_onboarding_events WHERE idempotency_key = %s",
        (request_id,),
    ).fetchone() == (1,)


def test_duplicate_current_origin_conflicts_without_partial_site(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)

    with pytest.raises(SiteOnboardingConflict):
        onboard(
            identity,
            identity_context,
            primary_origin="https://example.invalid",
        )

    assert admin.execute(
        "SELECT count(*) FROM app.sites WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    ).fetchone() == (2,)
    assert admin.execute(
        "SELECT count(*) FROM app.site_onboarding_events WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    ).fetchone() == (0,)


def test_stale_session_version_rolls_back_without_partial_site(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)

    with pytest.raises(SiteOnboardingConflict):
        onboard(identity, identity_context, expected_session_version=1)

    assert admin.execute(
        "SELECT count(*) FROM app.sites WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    ).fetchone() == (2,)
    assert admin.execute(
        "SELECT count(*) FROM app.site_onboarding_events WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    ).fetchone() == (0,)


def test_non_owner_and_invalid_parent_authority_are_closed(admin, identity, identity_context):
    with pytest.raises(SiteOnboardingDenied):
        onboard(identity, identity_context)

    make_owner(admin, identity_context)
    admin.execute(
        "UPDATE control.identity_sessions SET revoked_at = now() WHERE id = %s",
        (identity_context["identity_session_id"],),
    )
    with pytest.raises(InvalidSession):
        onboard(identity, identity_context)


def test_tenant_site_limit_is_enforced_under_the_locked_tenant(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)
    for index in range(98):
        admin.execute(
            "INSERT INTO app.sites (tenant_id, id, name, primary_origin, timezone, "
            "reporting_currency) VALUES (%s, %s, %s, %s, 'UTC', 'USD')",
            (
                scopes[0].tenant_id,
                uuid4(),
                f"Synthetic limit site {index}",
                f"https://limit-{index}.example.com",
            ),
        )

    with pytest.raises(SiteLimitReached):
        onboard(identity, identity_context)

    assert admin.execute(
        "SELECT count(*) FROM control.tenant_site_routes "
        "WHERE tenant_id = %s AND state <> 'archived'",
        (scopes[0].tenant_id,),
    ).fetchone() == (100,)
    assert admin.execute(
        "SELECT count(*) FROM app.site_onboarding_events WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    ).fetchone() == (0,)


def test_concurrent_exact_requests_create_one_site_and_one_replay(admin, scopes, identity_context):
    make_owner(admin, identity_context)
    request_id = uuid4()

    def create():
        with psycopg.connect(os.environ["SIGNAL_TEST_IDENTITY_DSN"], autocommit=True) as connection:
            return onboard(connection, identity_context, idempotency_key=request_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create(), range(2)))

    assert {result.site_id for result in results} == {results[0].site_id}
    assert sorted(result.replayed for result in results) == [False, True]
    assert admin.execute(
        "SELECT count(*) FROM app.sites WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    ).fetchone() == (3,)
    assert admin.execute(
        "SELECT count(*) FROM app.site_onboarding_events WHERE idempotency_key = %s",
        (request_id,),
    ).fetchone() == (1,)


def test_generated_site_collision_retries_the_whole_transaction(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)
    replacement_site_id = uuid4()
    site_ids = iter((scopes[0].site_id, replacement_site_id))

    created = onboard(
        identity,
        identity_context,
        site_id_factory=lambda: next(site_ids),
    )

    assert created.site_id == replacement_site_id
    assert admin.execute(
        "SELECT count(*) FROM app.site_onboarding_events WHERE site_id = %s",
        (replacement_site_id,),
    ).fetchone() == (1,)


def test_context_event_collision_retries_without_partial_onboarding(
    admin, identity, scopes, identity_context
):
    make_owner(admin, identity_context)
    first = onboard(identity, identity_context)
    first_context_id = admin.execute(
        "SELECT id FROM app.session_site_context_events WHERE tenant_id = %s AND site_id = %s",
        (first.tenant_id, first.site_id),
    ).fetchone()[0]
    replacement_context_id = uuid4()
    context_ids = iter((first_context_id, replacement_context_id))

    second = onboard(
        identity,
        identity_context,
        name="Second public site",
        primary_origin="https://second.example.com",
        expected_session_version=3,
        context_event_id_factory=lambda: next(context_ids),
    )

    assert second.session_version == 4
    assert admin.execute(
        "SELECT count(*) FROM app.sites WHERE tenant_id = %s",
        (scopes[0].tenant_id,),
    ).fetchone() == (4,)
    assert admin.execute(
        "SELECT id FROM app.session_site_context_events WHERE tenant_id = %s AND site_id = %s",
        (second.tenant_id, second.site_id),
    ).fetchone() == (replacement_context_id,)


def test_event_is_trigger_validated_immutable_and_not_runtime_readable(
    admin, identity, identity_context
):
    make_owner(admin, identity_context)
    created = onboard(identity, identity_context)

    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "INSERT INTO app.site_onboarding_events "
            "SELECT tenant_id, %s, site_id, site_membership_id, session_id, user_id, "
            "event_type, schema_version, scope_kind, %s, request_hash, site_name, primary_origin, "
            "timezone, reporting_currency, role_key, authentication_level, "
            "membership_authorization_epoch, site_authorization_epoch, "
            "recovery_generation, session_version, %s, occurred_at "
            "FROM app.site_onboarding_events WHERE tenant_id = %s AND site_id = %s",
            (
                uuid4(),
                uuid4(),
                hashlib.sha256(b"invalid").digest(),
                created.tenant_id,
                created.site_id,
            ),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "DELETE FROM app.site_onboarding_events WHERE tenant_id = %s AND site_id = %s",
            (created.tenant_id, created.site_id),
        )
    assert admin.execute(
        "SELECT has_function_privilege('signal_identity', "
        "'control.onboard_site(bytea,text,uuid,uuid,uuid,uuid,uuid,bytea,text,text,text,text,"
        "bigint,integer)', "
        "'EXECUTE')"
    ).fetchone() == (True,)
    for table in ("control.tenant_site_routes", "app.site_onboarding_events"):
        assert admin.execute(
            "SELECT has_table_privilege('signal_identity', %s, 'SELECT')",
            (table,),
        ).fetchone() == (False,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege), identity.transaction():
            identity.execute(f"SELECT * FROM {table}")


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://example.com/path",
        "https://example.com?query",
        "https://example.com/#fragment",
        "https://127.0.0.1",
        "https://localhost",
        "https://user@example.com",
        "HTTPS://example.com",
    ],
)
def test_public_site_origin_rejects_noncanonical_or_non_https_input(origin):
    with pytest.raises(InvalidSiteOnboarding):
        normalize_public_site_origin(origin)


def test_public_site_origin_canonicalizes_idna_before_acceptance():
    assert normalize_public_site_origin("https://xn--bcher-kva.example") == (
        "https://xn--bcher-kva.example"
    )


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"name": " New site "}, InvalidSiteOnboarding),
        ({"timezone": "Mars/Base"}, InvalidSiteOnboarding),
        ({"reporting_currency": "usd"}, InvalidSiteOnboarding),
        ({"expected_session_version": True}, SiteOnboardingConflict),
        ({"idempotency_key": uuid4().hex}, InvalidSiteOnboarding),
        ({"site_id_factory": lambda: "not-a-uuid"}, RuntimeError),
    ],
)
def test_site_onboarding_rejects_invalid_domain_input_before_database(
    identity, identity_context, overrides, error
):
    with pytest.raises(error):
        onboard(identity, identity_context, **overrides)
