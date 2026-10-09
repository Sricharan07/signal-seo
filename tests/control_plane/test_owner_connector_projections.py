import hashlib
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.github_read_binding import finish_github_read_binding, revoke_github_read_binding

from tests.control_plane.test_github_read_binding import _prepare, _snapshot, _target
from tests.control_plane.test_gsc_binding import (
    begin,
    owner_and_verified_origin,
    session_args,
    site_origin,
)


@pytest.fixture
def owner(admin, identity, identity_context, scopes):
    site = scopes[0].site_id
    owner_and_verified_origin(admin, identity, identity_context, site)
    admin.execute(
        "UPDATE control.identity_sessions SET authentication_level='mfa' WHERE id=%s",
        (identity_context["identity_session_id"],),
    )
    admin.execute(
        "UPDATE app.sessions SET mfa_level='mfa' WHERE id=%s",
        (identity_context["tenant_session_id"],),
    )
    return identity_context, site


def read(identity, context, site, connector):
    return identity.execute(
        f"SELECT control.read_owner_{connector}_connector(%s,%s,%s)",
        session_args(context, site),
    ).fetchone()[0]


@pytest.mark.parametrize("connector", ["gsc", "github"])
@pytest.mark.parametrize("change", [None, "primary", "revoked", "generation", "site", "member"])
def test_owner_projection_requires_current_mfa_owner(admin, identity, owner, connector, change):
    context, site = owner
    if change == "primary":
        admin.execute(
            "UPDATE control.identity_sessions SET authentication_level='primary' WHERE id=%s",
            (context["identity_session_id"],),
        )
    elif change == "revoked":
        admin.execute(
            "UPDATE app.sessions SET revoked_at=clock_timestamp() WHERE id=%s",
            (context["tenant_session_id"],),
        )
    elif change == "member":
        admin.execute(
            "UPDATE app.memberships SET role_key='viewer' WHERE id=%s",
            (context["membership_id"],),
        )
    elif change == "generation":
        context = {**context, "generation": "stale"}
    elif change == "site":
        site = uuid4()
    assert read(identity, context, site, connector) == {
        "availability": "unbound" if change is None else "denied"
    }


def test_gsc_projection_limits_selection_and_hides_secrets_then_tracks_revocation(
    admin, identity, owner
):
    context, site = owner
    attempt = uuid4()
    assert begin(identity, context, site, attempt) == "created"
    common = (*session_args(context, site), attempt)
    assert identity.execute(
        "SELECT outcome FROM control.consume_gsc_oauth_attempt(%s,%s,%s,%s,%s,%s)",
        (
            *common,
            hashlib.sha256(attempt.bytes).digest(),
            "https://signal.example/oauth/gsc/callback",
        ),
    ).fetchone() == ("consumed",)
    resource = site_origin(site) + "/"
    candidates = [
        {"resource_name": resource, "property_type": "url_prefix", "eligible": True},
        {
            "resource_name": "https://private-other.invalid/",
            "property_type": "url_prefix",
            "eligible": True,
        },
        {
            "resource_name": "sc-domain:unrelated.invalid",
            "property_type": "domain",
            "eligible": True,
        },
        {
            "resource_name": "https://ineligible.invalid/",
            "property_type": "url_prefix",
            "eligible": False,
        },
    ]
    assert identity.execute(
        "SELECT control.stage_gsc_oauth_attempt(%s,%s,%s,%s,%s,%s)",
        (*common, f"secret://gsc/{attempt}", Jsonb(candidates)),
    ).fetchone() == ("staged",)
    assert read(identity, context, site, "gsc") == {
        "availability": "selecting",
        "attempt_id": str(attempt),
        "properties": [{"resource_name": resource, "property_type": "url_prefix"}],
    }
    binding = uuid4()
    assert identity.execute(
        "SELECT control.confirm_gsc_binding(%s,%s,%s,%s,%s,%s,%s)",
        (*common, binding, uuid4(), resource),
    ).fetchone() == ("bound",)
    assert read(identity, context, site, "gsc") == {
        "availability": "bound",
        "binding_id": str(binding),
        "property_resource_name": resource,
    }
    assert identity.execute(
        "SELECT outcome FROM control.revoke_gsc_binding(%s,%s,%s,%s,%s)",
        (*session_args(context, site), binding, uuid4()),
    ).fetchone() == ("revoked",)
    assert read(identity, context, site, "gsc") == {"availability": "unbound"}
    assert not admin.execute(
        "SELECT has_table_privilege('signal_identity','app.gsc_binding_events','SELECT')"
    ).fetchone()[0]


def test_github_projection_prepared_active_stale_revoked(identity, admin, scopes, owner):
    context, site = owner
    target = _target()
    prepared = _prepare(identity, scopes[0], context, target=target)
    projection = read(identity, context, site, "github")
    assert projection["availability"] == "prepared"
    assert projection["binding_id"] == str(prepared.id)
    assert (
        finish_github_read_binding(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=prepared,
            snapshot=_snapshot(target),
        )
        == "active"
    )
    projection = read(identity, context, site, "github")
    assert projection["availability"] == "active" and projection["base_sha"] == "a" * 40
    assert "secret" not in str(projection)
    admin.execute(
        "UPDATE app.memberships SET authorization_epoch=authorization_epoch+1 WHERE id=%s",
        (context["membership_id"],),
    )
    assert read(identity, context, site, "github")["availability"] == "stale"
    revoke_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=site,
        binding_id=prepared.id,
    )
    assert read(identity, context, site, "github") == {"availability": "unbound"}


@pytest.mark.parametrize("connector", ["gsc", "github"])
def test_nonidentity_role_cannot_read_owner_projection(api, owner, connector):
    context, site = owner
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        read(api, context, site, connector)
