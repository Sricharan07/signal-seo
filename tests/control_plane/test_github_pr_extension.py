import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2
import psycopg
import pytest
from joserfc.jwk import RSAKey
from signal_core.authorization import AuthorizationDenied
from signal_core.github_pr_extension import (
    GitHubPrExtensionConflict,
    GitHubPrExtensionUnavailable,
    inspect_current_github_pr_extension,
    observe_github_pr_extension,
    prepare_github_pr_extension,
    read_github_pr_extension,
    revoke_github_pr_extension,
)
from signal_core.github_read_binding import (
    GitHubSharedEgressTransport,
    OpenBaoGitHubAppCredential,
    finish_github_read_binding,
    revoke_github_read_binding,
)
from signal_core.shared_egress import ProviderEgressResponse, SharedEgressProvider

from tests.control_plane.test_github_read_binding import _owner_site, _prepare, _snapshot, _target


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _active_binding(admin, identity, scopes, identity_context):
    scope, context = _owner_site(admin, identity, scopes, identity_context)
    binding = _prepare(identity, scope, context)
    finish_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        prepared=binding,
        snapshot=_snapshot(_target()),
    )
    return scope, context, binding


def _provider(*, pr_permissions=True, truncated=False, branch_sha="a" * 40):
    provider = Mock(spec=SharedEgressProvider)
    provider.purpose = "connector"
    provider.policy = Mock(
        allowed_origins=("https://api.github.com",),
        user_agent="SignalBot/1.0 (+https://signal.example/bot)",
        max_redirects=0,
        max_body_bytes=256 * 1024,
        request_timeout_seconds=5,
    )
    calls = []

    def request_json(**kwargs):
        calls.append(kwargs)
        path = urlsplit(kwargs["url"]).path
        status = 200
        if path.endswith("/access_tokens"):
            requested = json.loads(kwargs["body"])["permissions"]
            granted = {"contents": "read", "metadata": "read"}
            if "pull_requests" in requested and pr_permissions:
                granted["pull_requests"] = "write"
            document = {
                "token": "t" * 32,
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "permissions": granted,
                "repository_selection": "selected",
                "repositories": [{"id": 245, "full_name": "SignalOwner/website"}],
            }
            status = 201
        elif path.endswith("/branches/main"):
            document = {"name": "main", "commit": {"sha": branch_sha}, "protected": True}
        elif "/git/commits/" in path:
            document = {"sha": branch_sha, "tree": {"sha": "b" * 40}}
        elif "/git/trees/" in path:
            document = {
                "sha": "b" * 40,
                "truncated": truncated,
                "tree": [
                    {"path": "next.config.mjs", "mode": "100644", "type": "blob", "sha": "c" * 40},
                    {"path": "app/page.tsx", "mode": "100644", "type": "blob", "sha": "d" * 40},
                ],
            }
        else:
            document = {
                "id": 245,
                "full_name": "SignalOwner/website",
                "private": True,
                "default_branch": "main",
                "archived": False,
                "disabled": False,
            }
        return ProviderEgressResponse(status, "application/json", json.dumps(document).encode())

    provider.request_json.side_effect = request_json
    return GitHubSharedEgressTransport(provider), calls


def _credential():
    pem = (
        RSAKey.generate_key(parameters={"alg": "RS256", "use": "sig"})
        .as_pem(private=True)
        .decode("ascii")
    )
    credential = OpenBaoGitHubAppCredential("https://bao.example.invalid", "a" * 32)

    async def handler(request):
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

    return credential, httpx2.MockTransport(handler)


@pytest.mark.anyio
async def test_owner_observes_pr_permission_and_exact_format_without_write(
    admin, identity, api, scopes, identity_context
):
    scope, context, binding = _active_binding(admin, identity, scopes, identity_context)
    credential, bao = _credential()
    transport, calls = _provider()
    request_id = uuid4()
    prepared = prepare_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=binding.id,
        idempotency_key=request_id,
    )
    assert prepared.status == "prepared"
    assert prepare_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=binding.id,
        idempotency_key=request_id,
    ).replayed
    observed = await observe_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=binding.id,
        idempotency_key=request_id,
        credential=credential,
        github_transport=transport,
        openbao_transport=bao,
    )
    assert observed.id == prepared.id
    assert observed.status == "observed"
    assert observed.candidate_compatible
    assert (observed.framework, observed.content_format, observed.coverage) == (
        "nextjs",
        "tsx",
        "complete",
    )
    assert observed.marker_evidence == (("next.config.mjs", "c" * 40),)
    assert observed.content_sha == "d" * 40
    assert [call["method"] for call in calls] == [
        "POST",
        "GET",
        "GET",
        "POST",
        "GET",
        "GET",
        "GET",
        "GET",
    ]
    assert all("/pulls" not in call["url"] for call in calls)
    assert all(call["url"].startswith("https://api.github.com/") for call in calls)
    assert not admin.execute(
        "SELECT has_table_privilege('signal_identity', 'app.github_pr_extensions', 'SELECT')"
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute("SELECT count(*) FROM app.github_pr_extensions")
    stored = admin.execute(
        "SELECT status, repository_id, base_sha, tree_sha, marker_evidence "
        "FROM app.github_pr_extensions WHERE id = %s",
        (observed.id,),
    ).fetchone()
    assert stored == (
        "observed",
        245,
        "a" * 40,
        "b" * 40,
        [{"path": "next.config.mjs", "sha": "c" * 40}],
    )
    current = await inspect_current_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        extension_id=observed.id,
        credential=credential,
        github_transport=transport,
        openbao_transport=bao,
    )
    assert current.repository_id == 245
    revoke_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        extension_id=observed.id,
    )
    calls_before = len(calls)
    with pytest.raises(GitHubPrExtensionUnavailable, match="GITHUB_PR_EXTENSION_INACTIVE"):
        await inspect_current_github_pr_extension(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            extension_id=observed.id,
            credential=credential,
            github_transport=transport,
            openbao_transport=bao,
        )
    assert len(calls) == calls_before
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.github_pr_extension_events SET detail_code = 'changed' "
            "WHERE extension_id = %s",
            (observed.id,),
        )


@pytest.mark.anyio
async def test_missing_pr_permission_and_partial_tree_fail_closed(
    admin, identity, scopes, identity_context
):
    scope, context, binding = _active_binding(admin, identity, scopes, identity_context)
    credential, bao = _credential()
    missing_permission, calls = _provider(pr_permissions=False)
    with pytest.raises(GitHubPrExtensionUnavailable, match="GITHUB_SCOPE_REJECTED"):
        await observe_github_pr_extension(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            binding_id=binding.id,
            idempotency_key=uuid4(),
            credential=credential,
            github_transport=missing_permission,
            openbao_transport=bao,
        )
    assert len(calls) == 1
    assert admin.execute(
        "SELECT status, failure_code FROM app.github_pr_extensions WHERE binding_id = %s",
        (binding.id,),
    ).fetchone() == ("failed", "GITHUB_SCOPE_REJECTED")
    partial, _ = _provider(truncated=True)
    observed = await observe_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=binding.id,
        idempotency_key=uuid4(),
        credential=credential,
        github_transport=partial,
        openbao_transport=bao,
    )
    assert observed.status == "observed"
    assert observed.coverage == "partial"
    assert observed.framework == "unknown"
    assert not observed.candidate_compatible
    with pytest.raises(GitHubPrExtensionUnavailable, match="GITHUB_PR_EXTENSION_INACTIVE"):
        await inspect_current_github_pr_extension(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            extension_id=observed.id,
            credential=credential,
            github_transport=partial,
            openbao_transport=bao,
        )


@pytest.mark.anyio
async def test_current_pr_permission_loss_or_branch_change_blocks_use(
    admin, identity, scopes, identity_context
):
    scope, context, binding = _active_binding(admin, identity, scopes, identity_context)
    credential, bao = _credential()
    transport, _ = _provider()
    extension = await observe_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=binding.id,
        idempotency_key=uuid4(),
        credential=credential,
        github_transport=transport,
        openbao_transport=bao,
    )
    for changed, expected in (
        (_provider(pr_permissions=False)[0], "GITHUB_SCOPE_REJECTED"),
        (_provider(branch_sha="e" * 40)[0], "GITHUB_BINDING_CHANGED"),
    ):
        with pytest.raises(GitHubPrExtensionUnavailable, match=expected):
            await inspect_current_github_pr_extension(
                identity,
                session_token=context["session_token"],
                current_recovery_generation=context["generation"],
                site_id=scope.site_id,
                extension_id=extension.id,
                credential=credential,
                github_transport=changed,
                openbao_transport=bao,
            )


def test_wrong_binding_conflict_and_authority_reduction_block_completion(
    admin, identity, scopes, identity_context
):
    scope, context, binding = _active_binding(admin, identity, scopes, identity_context)
    key = uuid4()
    prepared = prepare_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=binding.id,
        idempotency_key=key,
    )
    with pytest.raises(GitHubPrExtensionConflict):
        prepare_github_pr_extension(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            binding_id=uuid4(),
            idempotency_key=key,
        )
    with pytest.raises(AuthorizationDenied):
        prepare_github_pr_extension(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scopes[1].site_id,
            binding_id=binding.id,
            idempotency_key=uuid4(),
        )
    admin.execute(
        "UPDATE app.memberships SET role_key = 'analyst', authorization_epoch = 3 WHERE id = %s",
        (context["membership_id"],),
    )
    with pytest.raises(AuthorizationDenied):
        revoke_github_pr_extension(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            extension_id=prepared.id,
        )
    assert (
        admin.execute(
            "SELECT status FROM app.github_pr_extensions WHERE id = %s",
            (prepared.id,),
        ).fetchone()[0]
        == "prepared"
    )


def test_parent_binding_revocation_invalidates_extension_read(
    admin, identity, scopes, identity_context
):
    scope, context, binding = _active_binding(admin, identity, scopes, identity_context)
    extension = prepare_github_pr_extension(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=binding.id,
        idempotency_key=uuid4(),
    )
    revoke_github_read_binding(
        identity,
        session_token=context["session_token"],
        current_recovery_generation=context["generation"],
        site_id=scope.site_id,
        binding_id=binding.id,
    )
    with pytest.raises(GitHubPrExtensionUnavailable):
        read_github_pr_extension(
            identity,
            session_token=context["session_token"],
            current_recovery_generation=context["generation"],
            site_id=scope.site_id,
            extension_id=extension.id,
        )
