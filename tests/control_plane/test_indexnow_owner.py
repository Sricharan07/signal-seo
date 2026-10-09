from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from signal_api.indexnow_http import ComposedIndexNowReadGateway
from signal_core.authorization import AuthorizationDenied
from signal_core.indexnow import IndexNowUnavailable
from signal_core.recovery_authority import RecoveryGeneration
from signal_core.technical_recipe_service import SealedTechnicalRevision

from tests.control_plane.test_candidate_builds import _active_extension
from tests.control_plane.test_owner_paths import mfa_time


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_owner_key_entrypoint_reuses_current_extension_and_fresh_mfa(
    admin, identity, api, scopes, identity_context, monkeypatch
):
    scope, context, extension = await _active_extension(admin, identity, scopes, identity_context)
    for table, field, key in (
        ("control.identity_sessions", "authentication_level", "identity_session_id"),
        ("app.sessions", "mfa_level", "tenant_session_id"),
    ):
        admin.execute(f"UPDATE {table} SET {field}='mfa' WHERE id=%s", (context[key],))
    mfa_time(admin, context)
    release_id = uuid4()
    transport = object()
    transport_factory = AsyncMock(return_value=transport)
    revision = SealedTechnicalRevision(uuid4(), "a" * 64, uuid4(), False)
    seal = AsyncMock(return_value=revision)
    monkeypatch.setattr("signal_api.indexnow_http.seal_indexnow_key_recipe", seal)
    gateway = ComposedIndexNowReadGateway(
        lambda: nullcontext(identity),
        object(),
        SimpleNamespace(
            current_generation=AsyncMock(return_value=RecoveryGeneration(context["generation"], 1))
        ),
        lambda: nullcontext(api),
        release_id,
        object(),
        transport_factory,
        object(),
    )
    request_id = uuid4()
    result = await gateway.create(context["session_token"], scope.site_id, request_id)
    assert result["state"] == "sealed" and result["revision_id"] == revision.id
    assert seal.call_args.kwargs["extension_id"] == extension.id
    assert seal.call_args.kwargs["key_id"] == request_id
    assert seal.call_args.kwargs["recipe_release_id"] == release_id
    assert seal.call_args.kwargs["github_transport"] is transport
    mfa_time(admin, context, "-6 minutes")
    with pytest.raises(IndexNowUnavailable, match="INDEXNOW_STEP_UP_REQUIRED"):
        await gateway.create(context["session_token"], scope.site_id, uuid4())
    assert seal.call_count == transport_factory.call_count == 1
    mfa_time(admin, context)
    with pytest.raises(AuthorizationDenied):
        await gateway.create(context["session_token"], scopes[1].site_id, uuid4())
    assert seal.call_count == 1
    transport_factory.side_effect = RuntimeError("synthetic-provider-unavailable")
    with pytest.raises(RuntimeError, match="synthetic-provider-unavailable"):
        await gateway.create(context["session_token"], scope.site_id, uuid4())
    assert seal.call_count == 1
