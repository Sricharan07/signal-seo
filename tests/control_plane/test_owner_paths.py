"""Real PostgreSQL owner entrypoints, provider observation and deny-only restrictions."""

import os
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import psycopg
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_api.owner_connector_http import ComposedGithubGateway
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.github_pr_extension import (
    GitHubPrExtensionConflict,
    GitHubPrExtensionUnavailable,
    finish_github_pr_extension,
    observe_github_pr_extension,
    prepare_github_pr_extension,
    read_github_pr_extension,
    revoke_github_pr_extension,
)
from signal_core.integration_scope import load_integration_scope
from signal_core.weekly_control import read_site_pause, set_site_paused

from tests.control_plane.test_github_pr_extension import _active_binding, _credential, _provider
from tests.control_plane.test_github_read_binding import _target


def mfa_time(admin, context, offset="0 minutes"):
    stamp = admin.execute("SELECT clock_timestamp()+%s::interval", (offset,)).fetchone()[0]
    for table, key in (
        ("control.identity_sessions", "identity_session_id"),
        ("app.sessions", "tenant_session_id"),
    ):
        admin.execute(
            f"UPDATE {table} SET auth_time=%s,last_seen_at=greatest(last_seen_at,%s) WHERE id=%s",
            (stamp, stamp, context[key]),
        )


@pytest.fixture
def owner_pr(admin, identity, scopes, identity_context):
    scope, context, binding = _active_binding(admin, identity, scopes, identity_context)
    admin.execute(
        "UPDATE control.identity_sessions SET authentication_level='mfa' WHERE id=%s",
        (context["identity_session_id"],),
    )
    admin.execute(
        "UPDATE app.sessions SET mfa_level='mfa' WHERE id=%s",
        (context["tenant_session_id"],),
    )
    mfa_time(admin, context)
    return scope, context, binding


def args(scope, context):
    return {
        "session_token": context["session_token"],
        "current_recovery_generation": context["generation"],
        "site_id": scope.site_id,
        "owner_flow": True,
    }


@pytest.mark.parametrize(
    "change",
    [
        "viewer",
        "primary",
        "stale_mfa",
        "future_mfa",
        "unverified",
        "no_binding",
        "stale_binding",
        "cross_tenant",
        "cross_site",
    ],
)
def test_owner_pr_prepare_denies_without_current_exact_authority(
    admin, identity, scopes, owner_pr, change
):
    scope, context, binding = owner_pr
    if change == "viewer":
        admin.execute(
            "UPDATE app.memberships SET role_key='viewer' WHERE id=%s", (context["membership_id"],)
        )
    elif change == "primary":
        admin.execute(
            "UPDATE control.identity_sessions SET authentication_level='primary' WHERE id=%s",
            (context["identity_session_id"],),
        )
        admin.execute(
            "UPDATE app.sessions SET mfa_level='primary' WHERE id=%s",
            (context["tenant_session_id"],),
        )
    elif change in {"stale_mfa", "future_mfa"}:
        mfa_time(admin, context, "-6 minutes" if change == "stale_mfa" else "1 minute")
    elif change == "unverified":
        admin.execute(
            "UPDATE app.sites SET ownership_status='reverification_required' WHERE id=%s",
            (scope.site_id,),
        )
    elif change == "no_binding":
        binding = replace(binding, id=uuid4())
    elif change == "stale_binding":
        admin.execute(
            "UPDATE app.memberships SET authorization_epoch=authorization_epoch+1 WHERE id=%s",
            (context["membership_id"],),
        )
    elif change == "cross_tenant":
        scope = scopes[1]
    elif change == "cross_site":
        scope = replace(scope, site_id=uuid4())
    expected = (
        GitHubPrExtensionUnavailable
        if change in {"stale_mfa", "future_mfa"}
        else (AuthorizationDenied, InvalidSession, GitHubPrExtensionUnavailable)
    )
    with pytest.raises(expected):
        prepare_github_pr_extension(
            identity, **args(scope, context), binding_id=binding.id, idempotency_key=uuid4()
        )
    assert (
        admin.execute(
            "SELECT count(*) FROM app.github_pr_extensions WHERE binding_id=%s", (binding.id,)
        ).fetchone()[0]
        == 0
    )


@pytest.mark.anyio
async def test_owner_http_prepares_finishes_rejects_forgery_replay_and_revokes_pending(
    admin, identity, owner_pr
):
    scope, context, binding = owner_pr
    credential, bao = _credential()
    transport, calls = _provider()
    target = _target()
    integration = replace(
        load_integration_scope(),
        site_id=str(scope.site_id),
        github_installation_id=target.installation_id,
        github_owner=target.owner,
        github_repository=target.repository,
        github_base_branch=target.base_branch,
        github_content_path=target.content_path,
    )
    gateway = ComposedGithubGateway(
        lambda: nullcontext(identity),
        SimpleNamespace(
            current_generation=AsyncMock(return_value=SimpleNamespace(value=context["generation"]))
        ),
        lambda *_: nullcontext(transport.provider),
        credential=credential,
        credential_options={"openbao_transport": bao},
        integration_scope=integration,
    )
    security = BrowserSecurity(
        b"synthetic-owner-paths-csrf-key-0149", frozenset({integration.origin})
    )
    app = create_app(
        settings=ApiSettings(environment="test"), browser_security=security, browser_github=gateway
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={context['session_token']}",
        "Origin": integration.origin,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": security.issue_csrf_token(context["session_token"]),
    }
    route = f"/v1/sites/{scope.site_id}/github-pr"
    key = uuid4()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://test.invalid"
    ) as api:
        assert (await api.get(route, headers=headers)).json() == {"availability": "ungranted"}
        prepared = await api.post(
            route,
            headers=headers,
            json={
                "operation": "prepare",
                "binding_id": str(binding.id),
                "idempotency_key": str(key),
            },
        )
        assert prepared.status_code == 200 and prepared.json()["state"] == "prepared"
        extension = prepared.json()["extension_id"]
        command = {
            "operation": "finish",
            "binding_id": str(binding.id),
            "idempotency_key": str(key),
            "extension_id": extension,
        }
        for bad in (
            {**command, "extension_id": str(uuid4())},
            {**command, "binding_id": str(uuid4())},
            {**command, "idempotency_key": str(uuid4())},
        ):
            assert (await api.post(route, headers=headers, json=bad)).status_code == 403
        assert calls == []
        assert (
            admin.execute(
                "SELECT count(*) FROM app.github_pr_extensions WHERE binding_id=%s", (binding.id,)
            ).fetchone()[0]
            == 1
        )
        forged = {**command, "snapshot": {"protected": True}, "state": "observed"}
        assert (await api.post(route, headers=headers, json=forged)).status_code == 422
        finished = await api.post(route, headers=headers, json=command)
        assert finished.status_code == 200 and finished.json()["state"] == "observed"
        state = (await api.get(route, headers=headers)).json()
        assert (
            state["availability"] == "observed"
            and state["owner"] == target.owner
            and state["base_branch"] == target.base_branch
        )
        before = len(calls)
        assert (await api.post(route, headers=headers, json=command)).status_code == 403
        assert len(calls) == before
        revoke = {"operation": "revoke", "extension_id": extension}
        for _ in range(2):
            revoked = await api.post(route, headers=headers, json=revoke)
            assert revoked.json() == {
                "state": "revoked",
                "durability": "AUTHORITY_DURABILITY_PENDING",
            }
        assert (await api.get(route, headers=headers)).json() == {"availability": "ungranted"}
    assert (
        admin.execute(
            "SELECT count(*) FROM control.authority_restriction_outbox WHERE target_id=%s",
            (extension,),
        ).fetchone()[0]
        == 1
    )
    assert all("/pulls" not in call["url"] and "/git/refs" not in call["url"] for call in calls)


def test_owner_finish_rechecks_mfa_and_cannot_finish_revoked_intent(admin, identity, owner_pr):
    scope, context, binding = owner_pr
    extension = prepare_github_pr_extension(
        identity, **args(scope, context), binding_id=binding.id, idempotency_key=uuid4()
    )
    mfa_time(admin, context, "-6 minutes")
    with pytest.raises(GitHubPrExtensionUnavailable, match="STEP_UP_REQUIRED"):
        finish_github_pr_extension(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=extension,
            failure_code="GITHUB_PROVIDER_UNAVAILABLE",
            owner_flow=True,
        )
    mfa_time(admin, context)
    assert (
        revoke_github_pr_extension(identity, **args(scope, context), extension_id=extension.id)
        == "AUTHORITY_DURABILITY_PENDING"
    )
    with pytest.raises(GitHubPrExtensionConflict):
        finish_github_pr_extension(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=extension,
            failure_code="GITHUB_PROVIDER_UNAVAILABLE",
            owner_flow=True,
        )


def test_weekly_pause_projection_is_owner_scoped_not_last_cycle(api, admin, owner_pr, scopes):
    scope, context, _ = owner_pr
    kw = {
        "session_token": context["session_token"],
        "site_id": scope.site_id,
        "recovery_generation": context["generation"],
    }
    assert read_site_pause(api, **kw) == {"site_id": str(scope.site_id), "paused": False}
    set_site_paused(api, **kw, paused=True)
    assert read_site_pause(api, **kw)["paused"] is True
    set_site_paused(api, **kw, paused=False)
    assert read_site_pause(api, **kw)["paused"] is False
    for site in (scopes[1].site_id, uuid4()):
        with pytest.raises(PermissionError):
            read_site_pause(api, **{**kw, "site_id": site})
    admin.execute(
        "UPDATE app.memberships SET role_key='viewer' WHERE id=%s", (context["membership_id"],)
    )
    with pytest.raises(PermissionError):
        read_site_pause(api, **kw)


@pytest.mark.anyio
async def test_pr_journal_acknowledgment_and_restored_observed_row_denial(
    admin, identity, owner_pr
):
    scope, context, binding = owner_pr
    credential, bao = _credential()
    transport, _ = _provider()
    extension = await observe_github_pr_extension(
        identity,
        **args(scope, context),
        binding_id=binding.id,
        idempotency_key=uuid4(),
        credential=credential,
        github_transport=transport,
        openbao_transport=bao,
    )
    assert (
        revoke_github_pr_extension(identity, **args(scope, context), extension_id=extension.id)
        == "AUTHORITY_DURABILITY_PENDING"
    )
    event = admin.execute(
        "SELECT event_id FROM control.github_pr_revocations WHERE target_id=%s", (extension.id,)
    ).fetchone()[0]
    with psycopg.connect(
        os.environ["SIGNAL_TEST_AUTHORITY_DISPATCHER_DSN"], autocommit=True
    ) as dispatcher:
        stream = uuid4()
        dispatcher.execute(
            "SELECT control.record_authority_restriction_receipt(%s,%s,%s,%s,%s,clock_timestamp())",
            (uuid4(), event, stream, 1, "a" * 64),
        )
        assert (
            revoke_github_pr_extension(identity, **args(scope, context), extension_id=extension.id)
            == "ACKNOWLEDGED"
        )
        # Model the row from an older primary snapshot. Real pg_restore/journal
        # replay is separately exercised by run-authority-journal-tests.py.
        admin.execute(
            "UPDATE app.github_pr_extensions SET status='observed',revoked_at=NULL WHERE id=%s",
            (extension.id,),
        )
        replay = (event, extension.id, 1, stream, 1, "a" * 64)
        for _ in range(2):
            dispatcher.execute(
                "SELECT control.apply_github_pr_authority_denial(%s,%s,%s,%s,%s,%s)", replay
            )
        with pytest.raises(psycopg.errors.UniqueViolation):
            dispatcher.execute(
                "SELECT control.apply_github_pr_authority_denial(%s,%s,%s,%s,%s,%s)",
                (*replay[:-1], "b" * 64),
            )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        identity.execute(
            "SELECT control.apply_github_pr_authority_denial(%s,%s,%s,%s,%s,%s)", replay
        )
    with pytest.raises(GitHubPrExtensionUnavailable):
        read_github_pr_extension(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            extension_id=extension.id,
        )
