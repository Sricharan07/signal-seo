import hashlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import httpx2
import psycopg
import pytest
from joserfc.jwk import RSAKey
from signal_core.authorization import AuthorizationDenied
from signal_core.github_app import GitHubRepositorySnapshot, GitHubRepositoryTarget
from signal_core.github_read_binding import (
    GitHubBindingConflict,
    GitHubBindingUnavailable,
    GitHubSharedEgressTransport,
    OpenBaoGitHubAppCredential,
    bind_github_read_repository,
    finish_github_read_binding,
    inspect_current_github_read_binding,
    prepare_github_read_binding,
    read_github_read_binding,
    revoke_github_read_binding,
)
from signal_core.origin_verification import (
    OriginProofObservation,
    issue_origin_challenge,
    prepare_origin_verification,
    record_origin_verification,
)
from signal_core.shared_egress import ProviderEgressResponse, SharedEgressProvider


def _owner_site(admin, identity, scopes, identity_context):
    scope = scopes[0]
    context = identity_context
    admin.execute(
        "UPDATE app.memberships SET role_key = 'owner', authorization_epoch = 2 WHERE id = %s",
        (context["membership_id"],),
    )
    origin = f"https://site-{scope.site_id.hex}.example.invalid"
    admin.execute(
        "UPDATE app.sites SET primary_origin = %s WHERE tenant_id = %s AND id = %s",
        (origin, scope.tenant_id, scope.site_id),
    )
    challenge = issue_origin_challenge(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        requested_site_id=scope.site_id,
        origin=origin,
        idempotency_key=uuid4(),
    )
    request_id = uuid4()
    prepared = prepare_origin_verification(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        requested_site_id=scope.site_id,
        challenge_id=challenge.challenge_id,
        origin=origin,
        idempotency_key=request_id,
    )
    record_origin_verification(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        requested_site_id=scope.site_id,
        challenge_id=challenge.challenge_id,
        origin=origin,
        idempotency_key=request_id,
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
    return scope, context


def _target(**changes):
    values = dict(
        installation_id=9342,
        owner="SignalOwner",
        repository="website",
        base_branch="main",
        content_path="app/page.tsx",
    )
    values.update(changes)
    return GitHubRepositoryTarget(**values)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _snapshot(target, **changes):
    from datetime import UTC, datetime, timedelta

    values = dict(
        installation_id=target.installation_id,
        repository_id=245,
        owner=target.owner,
        repository=target.repository,
        full_name=f"{target.owner}/{target.repository}",
        private=True,
        default_branch="main",
        base_branch=target.base_branch,
        base_sha="a" * 40,
        protected=True,
        content_path=target.content_path,
        credential_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    values.update(changes)
    return GitHubRepositorySnapshot(**values)


def _prepare(identity, scope, context, *, target=None, key=None):
    return prepare_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        idempotency_key=key or uuid4(),
        target=target or _target(),
    )


def test_owner_selects_exact_repository_and_revokes_without_secret_storage(
    admin, identity, api, scopes, identity_context
):
    scope, context = _owner_site(admin, identity, scopes, identity_context)
    key = uuid4()
    target = _target()
    prepared = _prepare(identity, scope, context, target=target, key=key)
    assert prepared.status == "prepared"
    assert _prepare(identity, scope, context, target=target, key=key).replayed
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
    active = read_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=prepared.id,
    )
    assert active.repository_id == 245
    assert active.target == target
    assert not admin.execute(
        "SELECT has_table_privilege('signal_identity', 'app.github_read_bindings', 'SELECT')"
    ).fetchone()[0]
    assert not admin.execute(
        "SELECT has_table_privilege('signal_api', 'app.github_read_bindings', 'INSERT')"
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("SELECT count(*) FROM app.github_read_bindings")
    assert [
        row[0]
        for row in admin.execute(
            "SELECT event_kind FROM app.github_read_binding_events "
            "WHERE binding_id = %s ORDER BY recorded_at, event_kind",
            (prepared.id,),
        ).fetchall()
    ] == ["prepared", "activated"]
    revoke_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=prepared.id,
    )
    assert (
        read_github_read_binding(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            binding_id=prepared.id,
        ).status
        == "revoked"
    )
    assert _prepare(identity, scope, context).status == "prepared"


def test_wrong_target_site_and_owner_authority_fail_closed(
    admin, identity, scopes, identity_context
):
    scope, context = _owner_site(admin, identity, scopes, identity_context)
    key = uuid4()
    prepared = _prepare(identity, scope, context, key=key)
    with pytest.raises(GitHubBindingConflict):
        _prepare(identity, scope, context, key=key, target=_target(repository="other"))
    with pytest.raises(GitHubBindingConflict):
        _prepare(identity, scope, context, target=_target(repository="other"))
    with pytest.raises(GitHubBindingConflict):
        finish_github_read_binding(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=prepared,
            snapshot=_snapshot(_target(), repository_id=245, full_name="Other/website"),
        )
    with pytest.raises(GitHubBindingConflict):
        finish_github_read_binding(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=prepared,
            snapshot=_snapshot(_target(), protected=False),
        )
    with identity.transaction():
        assert (
            identity.execute(
                "SELECT control.finish_github_read_binding(%s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s)",
                (
                    hashlib.sha256(context["session_token"].encode()).digest(),
                    scope.site_id,
                    context["generation"],
                    prepared.id,
                    uuid4(),
                    "observed",
                    245,
                    "SignalOwner/website",
                    "main",
                    "a" * 40,
                    True,
                    False,
                ),
            ).fetchone()[0]
            == "invalid_observation"
        )
    with pytest.raises(AuthorizationDenied):
        prepare_github_read_binding(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scopes[1].site_id,
            idempotency_key=uuid4(),
            target=_target(),
        )
    admin.execute(
        "UPDATE app.memberships SET role_key = 'analyst', authorization_epoch = 3 WHERE id = %s",
        (context["membership_id"],),
    )
    with pytest.raises(AuthorizationDenied):
        finish_github_read_binding(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=prepared,
            snapshot=_snapshot(_target()),
        )
    assert (
        admin.execute(
            "SELECT status FROM app.github_read_bindings WHERE id = %s", (prepared.id,)
        ).fetchone()[0]
        == "prepared"
    )


def test_failure_receipt_and_revoked_intent_cannot_activate(
    admin, identity, scopes, identity_context
):
    scope, context = _owner_site(admin, identity, scopes, identity_context)
    failed = _prepare(identity, scope, context)
    assert (
        finish_github_read_binding(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=failed,
            failure_code="GITHUB_AUTHORIZATION_REJECTED",
        )
        == "failed"
    )
    assert admin.execute(
        "SELECT status, failure_code FROM app.github_read_bindings WHERE id = %s",
        (failed.id,),
    ).fetchone() == ("failed", "GITHUB_AUTHORIZATION_REJECTED")
    pending = _prepare(identity, scope, context)
    admin.execute(
        "UPDATE app.sites SET ownership_status = 'reverification_required' WHERE id = %s",
        (scope.site_id,),
    )
    with pytest.raises(AuthorizationDenied):
        finish_github_read_binding(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=pending,
            snapshot=_snapshot(_target()),
        )
    admin.execute(
        "UPDATE app.sites SET ownership_status = 'verified' WHERE id = %s",
        (scope.site_id,),
    )
    revoke_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=pending.id,
    )
    with pytest.raises(GitHubBindingConflict):
        finish_github_read_binding(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            prepared=pending,
            snapshot=_snapshot(_target()),
        )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.github_read_binding_events SET detail_code = 'changed' "
            "WHERE binding_id = %s",
            (pending.id,),
        )


def test_binding_intent_rolls_back_before_provider_call(admin, identity, scopes, identity_context):
    scope, context = _owner_site(admin, identity, scopes, identity_context)
    target = _target()
    request_id = uuid4()
    payload = "\n".join(
        (
            str(scope.site_id),
            str(target.installation_id),
            target.owner,
            target.repository,
            target.base_branch,
            target.content_path,
        )
    )
    with pytest.raises(RuntimeError, match="rollback"), identity.transaction():
        identity.execute(
            "SELECT * FROM control.prepare_github_read_binding(%s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s)",
            (
                hashlib.sha256(context["session_token"].encode()).digest(),
                scope.site_id,
                context["generation"],
                uuid4(),
                uuid4(),
                request_id,
                hashlib.sha256(payload.encode()).digest(),
                target.installation_id,
                target.owner,
                target.repository,
                target.base_branch,
                target.content_path,
            ),
        )
        raise RuntimeError("rollback")
    assert (
        admin.execute(
            "SELECT count(*) FROM app.github_read_bindings WHERE idempotency_key = %s",
            (request_id,),
        ).fetchone()[0]
        == 0
    )


@pytest.mark.anyio
async def test_binding_uses_openbao_and_current_provider_read_detects_revocation(
    admin, identity, scopes, identity_context
):
    scope, context = _owner_site(admin, identity, scopes, identity_context)
    target = _target()
    pem = (
        RSAKey.generate_key(parameters={"alg": "RS256", "use": "sig"})
        .as_pem(private=True)
        .decode("ascii")
    )
    credential = OpenBaoGitHubAppCredential(base_url="https://bao.example.invalid", token="a" * 32)

    async def bao_handler(request):
        assert request.url.path == "/v1/signal-github/data/github/app"
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "data": {
                    "data": {"app_id": 123, "private_key_pem": pem},
                    "metadata": {"version": 1, "destroyed": False, "deletion_time": ""},
                }
            },
        )

    revoked = False
    protected = True
    calls = []

    def github_request_json(**kwargs):
        url = kwargs["url"]
        calls.append(url)
        if revoked:
            return ProviderEgressResponse(403, "application/json", b"{}")
        if url.endswith("/access_tokens"):
            document = {
                "token": "x" * 32,
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "permissions": {"contents": "read", "metadata": "read"},
                "repository_selection": "selected",
                "repositories": [{"id": 245, "full_name": "SignalOwner/website"}],
            }
            status = 201
        elif url.endswith("/branches/main"):
            document = {"name": "main", "commit": {"sha": "a" * 40}, "protected": protected}
            status = 200
        else:
            document = {
                "id": 245,
                "full_name": "SignalOwner/website",
                "private": True,
                "default_branch": "main",
                "archived": False,
                "disabled": False,
            }
            status = 200
        return ProviderEgressResponse(status, "application/json", json.dumps(document).encode())

    bao_transport = httpx2.MockTransport(bao_handler)
    provider = Mock(spec=SharedEgressProvider)
    provider.purpose = "connector"
    provider.policy = Mock(
        allowed_origins=("https://api.github.com",),
        user_agent="SignalBot/1.0 (+https://signal.example/bot)",
        max_redirects=0,
        max_body_bytes=256 * 1024,
        request_timeout_seconds=5,
    )
    provider.request_json.side_effect = github_request_json
    github_transport = GitHubSharedEgressTransport(provider)
    with pytest.raises(GitHubBindingUnavailable) as missing:
        await bind_github_read_repository(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            idempotency_key=uuid4(),
            target=target,
            credential=credential,
        )
    assert missing.value.code == "GITHUB_EGRESS_UNAVAILABLE"
    with pytest.raises(GitHubBindingUnavailable) as bypass:
        await bind_github_read_repository(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            idempotency_key=uuid4(),
            target=target,
            credential=credential,
            github_transport=httpx2.MockTransport(bao_handler),
        )
    assert bypass.value.code == "GITHUB_EGRESS_UNAVAILABLE"
    assert (
        admin.execute(
            "SELECT count(*) FROM app.github_read_bindings WHERE site_id = %s",
            (scope.site_id,),
        ).fetchone()[0]
        == 0
    )

    protected = False
    rejected_key = uuid4()
    with pytest.raises(GitHubBindingUnavailable) as unprotected:
        await bind_github_read_repository(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            idempotency_key=rejected_key,
            target=target,
            credential=credential,
            github_transport=github_transport,
            openbao_transport=bao_transport,
        )
    assert unprotected.value.code == "GITHUB_REPOSITORY_STATE_REJECTED"
    assert len(calls) == 3
    assert admin.execute(
        "SELECT status, failure_code FROM app.github_read_bindings WHERE idempotency_key = %s",
        (rejected_key,),
    ).fetchone() == ("failed", "GITHUB_REPOSITORY_STATE_REJECTED")
    replay = await bind_github_read_repository(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        idempotency_key=rejected_key,
        target=target,
        credential=credential,
        github_transport=github_transport,
        openbao_transport=bao_transport,
    )
    assert replay.status == "failed" and len(calls) == 3
    protected = True
    calls.clear()
    active = await bind_github_read_repository(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        idempotency_key=uuid4(),
        target=target,
        credential=credential,
        github_transport=github_transport,
        openbao_transport=bao_transport,
    )
    assert active.status == "active"
    assert len(calls) == 3
    observed = await inspect_current_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=active.id,
        credential=credential,
        github_transport=github_transport,
        openbao_transport=bao_transport,
    )
    assert observed.repository_id == 245
    protected = False
    with pytest.raises(GitHubBindingUnavailable) as failure:
        await inspect_current_github_read_binding(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            binding_id=active.id,
            credential=credential,
            github_transport=github_transport,
            openbao_transport=bao_transport,
        )
    assert failure.value.code == "GITHUB_BINDING_CHANGED"
    revoked = True
    with pytest.raises(GitHubBindingUnavailable) as failure:
        await inspect_current_github_read_binding(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            binding_id=active.id,
            credential=credential,
            github_transport=github_transport,
            openbao_transport=bao_transport,
        )
    assert failure.value.code == "GITHUB_AUTHORIZATION_REJECTED"
    revoke_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=active.id,
    )
    call_count = len(calls)
    with pytest.raises(GitHubBindingUnavailable) as failure:
        await inspect_current_github_read_binding(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            binding_id=active.id,
            credential=credential,
            github_transport=github_transport,
            openbao_transport=bao_transport,
        )
    assert failure.value.code == "GITHUB_BINDING_INACTIVE"
    assert len(calls) == call_count
