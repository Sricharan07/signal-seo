from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest
from signal_core.github_read_binding import read_github_read_binding, record_github_binding_state

from tests.control_plane.test_github_read_binding import _owner_site, _prepare, _snapshot, _target


def fresh_mfa(admin, context, *, age="0 seconds"):
    for table, column, key in (
        ("control.identity_sessions", "authentication_level", "identity_session_id"),
        ("app.sessions", "mfa_level", "tenant_session_id"),
    ):
        admin.execute(
            f"UPDATE {table} SET {column}='mfa', auth_time=transaction_timestamp()-%s::interval, "
            "last_seen_at=transaction_timestamp() WHERE id=%s",
            (age, context[key]),
        )
    # Both session layers must share the exact signed authentication timestamp.
    admin.execute(
        "UPDATE app.sessions SET auth_time=(SELECT auth_time FROM control.identity_sessions "
        "WHERE id=%s) WHERE id=%s",
        (context["identity_session_id"], context["tenant_session_id"]),
    )


def accept_sql(identity, scope, context, binding, **changes):
    values = dict(
        repository=245,
        full_name="SignalOwner/website",
        branch="main",
        base_sha="a" * 40,
        private=True,
        protected=False,
        permissions=b"p" * 32,
    )
    values.update(changes)
    return identity.execute(
        "SELECT control.accept_github_unprotected_base(" + ",".join(["%s"] * 12) + ")",
        (
            sha256(context["session_token"].encode()).digest(),
            scope.site_id,
            context["generation"],
            binding.id,
            uuid4(),
            values["repository"],
            values["full_name"],
            values["branch"],
            values["base_sha"],
            values["private"],
            values["protected"],
            values["permissions"],
        ),
    ).fetchone()[0]


def accepted_binding(admin, identity, scopes, context):
    scope, context = _owner_site(admin, identity, scopes, context)
    binding = _prepare(identity, scope, context)
    fresh_mfa(admin, context)
    with identity.transaction():
        assert accept_sql(identity, scope, context, binding) == "active"
    return scope, context, binding


def read_binding(identity, scope, context, binding):
    return read_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=binding.id,
    )


def test_acceptance_is_explicit_owner_fresh_mfa_and_immutable(
    admin, identity, api, scopes, identity_context
):
    scope, context = _owner_site(admin, identity, scopes, identity_context)
    binding = _prepare(identity, scope, context)
    with identity.transaction():
        assert accept_sql(identity, scope, context, binding) == "permission_denied"
    fresh_mfa(admin, context, age="6 minutes")
    with identity.transaction():
        assert accept_sql(identity, scope, context, binding) == "step_up_required"
    fresh_mfa(admin, context)
    with identity.transaction():
        assert (
            accept_sql(identity, scope, context, binding, branch="other") == "invalid_observation"
        )
        assert (
            accept_sql(identity, scope, context, binding, protected=True) == "invalid_observation"
        )
        assert accept_sql(identity, scope, context, binding) == "active"
        assert accept_sql(identity, scope, context, binding) == "active"
    active = read_binding(identity, scope, context, binding)
    assert active.owner_accepted_unprotected and active.protected is False
    assert admin.execute(
        "SELECT owner_user_id,repository_id,default_branch,protection_state,recovery_generation "
        "FROM app.github_unprotected_base_acceptances WHERE binding_id=%s",
        (binding.id,),
    ).fetchall() == [(context["user_id"], 245, "main", "none", context["generation"])]
    projection = identity.execute(
        "SELECT control.read_owner_github_connector(%s,%s,%s)",
        (sha256(context["session_token"].encode()).digest(), context["generation"], scope.site_id),
    ).fetchone()[0]
    assert projection["base_protection"] == "owner_accepted_unprotected"
    for statement in (
        "UPDATE app.github_unprotected_base_acceptances SET protection_state='unavailable_on_plan' "
        "WHERE binding_id=%s",
        "DELETE FROM app.github_unprotected_base_acceptances WHERE binding_id=%s",
    ):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            admin.execute(statement, (binding.id,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("SELECT * FROM app.github_unprotected_base_acceptances")
    admin.execute(
        "UPDATE app.memberships SET role_key='analyst',authorization_epoch=authorization_epoch+1 "
        "WHERE id=%s",
        (context["membership_id"],),
    )
    with identity.transaction():
        assert accept_sql(identity, scope, context, binding) == "permission_denied"
    assert not read_binding(identity, scope, context, binding).owner_accepted_unprotected


@pytest.mark.parametrize(
    "changes,permissions",
    [
        ({"repository_id": 246}, "70" * 32),
        ({"default_branch": "trunk"}, "70" * 32),
        ({"installation_id": 9343}, "70" * 32),
        ({"full_name": "Other/website"}, "70" * 32),
        ({"base_branch": "trunk"}, "70" * 32),
        ({}, "71" * 32),
        ({}, None),
    ],
)
def test_identity_and_permission_drift_permanently_invalidate_acceptance(
    admin, identity, scopes, identity_context, changes, permissions
):
    scope, context, binding = accepted_binding(admin, identity, scopes, identity_context)
    active = read_binding(identity, scope, context, binding)
    snapshot = replace(_snapshot(_target(), protected=False), **changes)
    assert not record_github_binding_state(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        binding=active,
        snapshot=snapshot,
        permissions_sha256=permissions,
    )
    assert not read_binding(identity, scope, context, binding).owner_accepted_unprotected
    assert not record_github_binding_state(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        binding=active,
        snapshot=_snapshot(_target(), protected=False),
        permissions_sha256="70" * 32,
    )
    assert admin.execute(
        "SELECT count(*) FROM app.github_unprotected_base_acceptances WHERE binding_id=%s",
        (binding.id,),
    ).fetchone() == (1,)


def test_provider_failure_invalidates_and_protection_is_preferred(
    admin, identity, scopes, identity_context
):
    scope, context, binding = accepted_binding(admin, identity, scopes, identity_context)
    active = read_binding(identity, scope, context, binding)
    assert not record_github_binding_state(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        binding=active,
    )
    assert not read_binding(identity, scope, context, binding).owner_accepted_unprotected
    assert record_github_binding_state(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        binding=active,
        snapshot=_snapshot(_target()),
        permissions_sha256="70" * 32,
    )
    active = read_binding(identity, scope, context, binding)
    assert active.protected and not active.owner_accepted_unprotected
    assert not record_github_binding_state(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        binding=active,
        snapshot=_snapshot(_target(), protected=False),
        permissions_sha256="70" * 32,
    )


def test_acceptance_rolls_back_atomically_without_activation(
    admin, identity, scopes, identity_context
):
    scope, context = _owner_site(admin, identity, scopes, identity_context)
    binding = _prepare(identity, scope, context)
    fresh_mfa(admin, context)
    with pytest.raises(RuntimeError, match="rollback"), identity.transaction():
        assert accept_sql(identity, scope, context, binding) == "active"
        raise RuntimeError("rollback")
    assert read_binding(identity, scope, context, binding).status == "prepared"
    assert admin.execute(
        "SELECT count(*) FROM app.github_unprotected_base_acceptances WHERE binding_id=%s",
        (binding.id,),
    ).fetchone() == (0,)


def test_recovery_generation_cannot_reuse_acceptance(admin, identity, scopes, identity_context):
    scope, context, binding = accepted_binding(admin, identity, scopes, identity_context)
    admin.execute(
        "UPDATE control.identity_sessions SET recovery_generation='synthetic-new-generation' "
        "WHERE id=%s",
        (context["identity_session_id"],),
    )
    context = {**context, "generation": "synthetic-new-generation"}
    assert not read_binding(identity, scope, context, binding).owner_accepted_unprotected
    with identity.transaction():
        assert accept_sql(identity, scope, context, binding) == "binding_not_authorized"
