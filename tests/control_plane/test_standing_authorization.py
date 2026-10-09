"""Real PostgreSQL authority, revocation, and aggregate weekly cap checks."""

from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import pytest
from psycopg import Error
from psycopg.errors import InsufficientPrivilege
from signal_core.recipe_releases import VERIFIED_HOMEPAGE_METADATA_RELEASE_ID
from signal_core.session_tokens import hash_session_token
from signal_core.standing_authorization import (
    RecipeRange,
    StandingAuthorizationConflict,
    StandingAuthorizationUnavailable,
    StandingGrantRequest,
    grant_standing_authorization,
    revoke_standing_authorization,
)


def _request(admin, site_id, **changes):
    now = admin.execute("SELECT transaction_timestamp()").fetchone()[0]
    values = dict(
        site_id=site_id,
        recipe_ranges=(RecipeRange("title_description_improvement", "1.0.0", "2.0.0"),),
        thresholds={"draft_patch": 0.8},
        weekly_volume_caps={"draft_patch": 2},
        weekly_total_cap=2,
        weekly_spend_cents=100,
        excluded_paths=("/private",),
        starts_at=now,
        ends_at=now + timedelta(days=7),
        recovery_window_hours=24,
    )
    values.update(changes)
    return StandingGrantRequest(**values)


def _owner_site(admin, scopes, identity_context):
    scope = scopes[0]
    admin.execute(
        "UPDATE app.memberships SET role_key='owner' WHERE tenant_id=%s AND user_id=%s",
        (scope.tenant_id, identity_context["user_id"]),
    )
    admin.execute(
        "UPDATE app.sites SET state='active', ownership_status='verified' "
        "WHERE tenant_id=%s AND id=%s",
        (scope.tenant_id, scope.site_id),
    )
    return scope


def _grant(api, admin, scope, identity_context, **changes):
    return grant_standing_authorization(
        api,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
        request=_request(admin, scope.site_id, **changes),
    )


def _eligibility(workflow, scope, identity_context, grant, path="/blog/post"):
    return workflow.execute(
        "SELECT * FROM control.standing_grant_eligibility(%s,%s,%s,%s,%s,%s,%s)",
        (
            scope.tenant_id,
            scope.site_id,
            grant.id,
            identity_context["generation"],
            VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,
            "draft_patch",
            path,
        ),
    ).fetchone()


def test_owner_grant_is_exact_immutable_and_recovery_bound(
    admin, api, workflow, scopes, identity_context
):
    scope = _owner_site(admin, scopes, identity_context)
    grant = _grant(api, admin, scope, identity_context)
    assert grant.recipe_release_ids == (VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,)
    assert _eligibility(workflow, scope, identity_context, grant) == (
        True,
        "eligible",
        Decimal("0.8"),
    )
    assert _eligibility(workflow, scope, identity_context, grant, "/private/report")[0] is False
    assert workflow.execute(
        "SELECT eligible FROM control.standing_grant_eligibility(%s,%s,%s,%s,%s,%s,%s)",
        (
            scope.tenant_id,
            scope.site_id,
            grant.id,
            "older-generation",
            VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,
            "draft_patch",
            "/blog/post",
        ),
    ).fetchone() == (False,)
    with pytest.raises(InsufficientPrivilege):
        api.execute("UPDATE app.standing_authorizations SET weekly_total_cap=1000")
    with pytest.raises(Error):
        admin.execute(
            "UPDATE app.standing_authorizations SET weekly_total_cap=1000 WHERE id=%s",
            (grant.id,),
        )
    with pytest.raises(StandingAuthorizationConflict):
        _grant(api, admin, scope, identity_context)


def test_future_grant_is_not_shown_or_used_as_current(
    admin, api, workflow, scopes, identity_context
):
    scope = _owner_site(admin, scopes, identity_context)
    now = admin.execute("SELECT transaction_timestamp()").fetchone()[0]
    grant = _grant(
        api,
        admin,
        scope,
        identity_context,
        starts_at=now + timedelta(hours=1),
        ends_at=now + timedelta(days=7),
    )
    assert _eligibility(workflow, scope, identity_context, grant)[0] is False
    assert api.execute(
        "SELECT outcome FROM control.read_standing_authorization(%s,%s,%s)",
        (
            hash_session_token(identity_context["session_token"]),
            scope.site_id,
            identity_context["generation"],
        ),
    ).fetchone() == ("not_started",)


def test_grant_denies_nonowner_unverified_and_a4(admin, api, scopes, identity_context):
    scope = scopes[0]
    with pytest.raises(StandingAuthorizationUnavailable):
        _grant(api, admin, scope, identity_context)
    _owner_site(admin, scopes, identity_context)
    with pytest.raises(StandingAuthorizationUnavailable):
        _grant(
            api,
            admin,
            scope,
            identity_context,
            thresholds={"metadata_pr": 0.9},
            weekly_volume_caps={"metadata_pr": 1},
        )
    admin.execute(
        "UPDATE app.memberships SET role_key='editor' WHERE tenant_id=%s AND user_id=%s",
        (scope.tenant_id, identity_context["user_id"]),
    )
    with pytest.raises(StandingAuthorizationUnavailable):
        _grant(api, admin, scope, identity_context)
    admin.execute(
        "UPDATE app.memberships SET role_key='owner' WHERE tenant_id=%s AND user_id=%s",
        (scope.tenant_id, identity_context["user_id"]),
    )
    with pytest.raises(ValueError):
        _request(admin, scope.site_id, thresholds={"canonical_change": 0.9})
    with pytest.raises(StandingAuthorizationUnavailable):
        grant_standing_authorization(
            api,
            session_token=identity_context["session_token"],
            current_recovery_generation="different-generation",
            request=_request(admin, scope.site_id),
        )


def test_role_removal_and_site_stop_invalidate_immediately(
    admin, api, workflow, scopes, identity_context
):
    scope = _owner_site(admin, scopes, identity_context)
    grant = _grant(api, admin, scope, identity_context)
    admin.execute(
        "UPDATE app.memberships SET role_key='editor' WHERE tenant_id=%s AND user_id=%s",
        (scope.tenant_id, identity_context["user_id"]),
    )
    assert _eligibility(workflow, scope, identity_context, grant)[0] is False
    admin.execute(
        "UPDATE app.memberships SET role_key='owner' WHERE tenant_id=%s AND user_id=%s",
        (scope.tenant_id, identity_context["user_id"]),
    )
    admin.execute(
        "UPDATE app.sites SET state='archived' WHERE tenant_id=%s AND id=%s",
        (scope.tenant_id, scope.site_id),
    )
    assert _eligibility(workflow, scope, identity_context, grant)[0] is False


def test_revocation_is_locally_final_and_journal_pending(
    admin, api, workflow, scopes, identity_context
):
    scope = _owner_site(admin, scopes, identity_context)
    grant = _grant(api, admin, scope, identity_context)
    revoked = revoke_standing_authorization(
        api,
        session_token=identity_context["session_token"],
        current_recovery_generation=identity_context["generation"],
        site_id=scope.site_id,
        grant_id=grant.id,
    )
    assert revoked.durability == "AUTHORITY_DURABILITY_PENDING"
    assert _eligibility(workflow, scope, identity_context, grant)[0] is False
    assert admin.execute(
        "SELECT target_kind,restriction_kind FROM control.authority_restriction_outbox "
        "WHERE event_id=%s",
        (revoked.restriction_event_id,),
    ).fetchone() == ("standing_grant", "standing_grant_revoked")
    assert (
        revoke_standing_authorization(
            api,
            session_token=identity_context["session_token"],
            current_recovery_generation=identity_context["generation"],
            site_id=scope.site_id,
            grant_id=grant.id,
        )
        == revoked
    )


def test_weekly_site_caps_cannot_be_split_across_operations(
    admin, api, workflow, scopes, identity_context
):
    scope = _owner_site(admin, scopes, identity_context)
    grant = _grant(
        api,
        admin,
        scope,
        identity_context,
        weekly_volume_caps={"draft_patch": 1},
        weekly_total_cap=1,
        weekly_spend_cents=50,
    )
    operation_id = uuid4()
    revision = sha256(b"sealed revision").digest()

    def reserve(op, cost, digest=revision, path="/blog/post"):
        return workflow.execute(
            "SELECT control.reserve_standing_budget(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                grant.id,
                identity_context["generation"],
                VERIFIED_HOMEPAGE_METADATA_RELEASE_ID,
                "draft_patch",
                path,
                op,
                digest,
                cost,
            ),
        ).fetchone()[0]

    assert reserve(operation_id, 50) == "reserved"
    assert admin.execute(
        "SELECT resource_path,recipe_release_id FROM app.autonomy_reservations "
        "WHERE tenant_id=%s AND site_id=%s AND operation_id=%s",
        (scope.tenant_id, scope.site_id, operation_id),
    ).fetchone() == ("/blog/post", VERIFIED_HOMEPAGE_METADATA_RELEASE_ID)
    assert reserve(operation_id, 50) == "reserved"
    assert reserve(operation_id, 50, sha256(b"other").digest()) == "reservation_conflict"
    assert reserve(operation_id, 50, path="/blog/another") == "reservation_conflict"
    assert reserve(uuid4(), 1) == "weekly_cap_reached"
    assert admin.execute(
        "SELECT total_count,spend_cents FROM app.autonomy_weekly_usage "
        "WHERE tenant_id=%s AND site_id=%s",
        (scope.tenant_id, scope.site_id),
    ).fetchone() == (1, 50)
