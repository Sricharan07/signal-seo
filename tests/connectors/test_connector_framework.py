import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from signal_core.connector_framework import (
    OAuthBinding,
    begin_pkce,
    consume_pkce,
    external_revocation,
    pkce_challenge,
    require_restriction,
    retained_secret,
    state_digest,
)
from signal_core.json_objects import unique_object


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("document", ['{"x":1,"x":2}', '{"x":{"y":1,"y":2}}'])
def test_duplicate_object_hook_rejects_at_every_depth(document):
    with pytest.raises(ValueError):
        json.loads(document, object_pairs_hook=unique_object)
    assert json.loads('{"x":{"y":1}}', object_pairs_hook=unique_object) == {"x": {"y": 1}}


@pytest.mark.parametrize("state", [None, "", "x" * 42, "x" * 44, "\u00e9" * 43])
def test_state_rejected_without_consumption(state):
    with pytest.raises(ValueError, match="synthetic-state-rejected"):
        state_digest(state, ValueError("synthetic-state-rejected"))


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["created", "denied", "failure"])
async def test_pkce_begin_removes_only_unrecorded_verifier(outcome):
    store = SimpleNamespace(store_verifier=AsyncMock(), destroy_verifier=AsyncMock())
    attempt = uuid4()
    authorization = SimpleNamespace(verifier="synthetic-" + "v" * 43)
    persist = Mock(return_value=outcome)
    if outcome == "failure":
        persist.side_effect = RuntimeError("synthetic-database-failure")
    if outcome == "created":
        await begin_pkce(store, attempt, authorization, persist, ValueError("synthetic-denied"))
    else:
        with pytest.raises(RuntimeError if outcome == "failure" else ValueError):
            await begin_pkce(store, attempt, authorization, persist, ValueError("synthetic-denied"))
    assert store.destroy_verifier.await_count == int(outcome != "created")
    store.store_verifier.assert_awaited_once_with(attempt, authorization.verifier)


@pytest.mark.anyio
async def test_consumed_pkce_checks_challenge_before_provider_call():
    verifier = "synthetic-" + "v" * 43
    store = SimpleNamespace(consume_verifier=AsyncMock(return_value=verifier))
    assert (
        await consume_pkce(store, uuid4(), pkce_challenge(verifier), ValueError("synthetic-pkce"))
        == verifier
    )
    with pytest.raises(ValueError, match="synthetic-pkce"):
        await consume_pkce(store, uuid4(), "wrong", ValueError("synthetic-pkce"))


@pytest.mark.anyio
@pytest.mark.parametrize("failed", [False, True])
async def test_staged_credential_cleanup_on_record_failure(failed):
    store = AsyncMock(return_value="secret://synthetic/reference")
    remove = AsyncMock()
    if failed:
        with pytest.raises(RuntimeError):
            async with retained_secret(store, remove, "synthetic-token"):
                raise RuntimeError("synthetic-record-failure")
    else:
        async with retained_secret(store, remove, "synthetic-token") as reference:
            assert reference == "secret://synthetic/reference"
    assert remove.await_count == int(failed)


@pytest.mark.parametrize("outcome", ["revoked", "AUTHORITY_DURABILITY_PENDING"])
def test_restriction_outcomes_are_preserved_without_claiming_durability(outcome):
    row = (outcome, "secret://synthetic/reference")
    assert (
        require_restriction(
            row, {"revoked", "AUTHORITY_DURABILITY_PENDING"}, ValueError("synthetic-denied")
        )
        is row
    )
    with pytest.raises(ValueError):
        require_restriction(("denied",), {"revoked"}, ValueError("synthetic-denied"))


@pytest.mark.anyio
@pytest.mark.parametrize("caught", [False, True])
async def test_upstream_failure_always_removes_active_secret_and_keeps_policy(caught):
    invoke = AsyncMock(side_effect=RuntimeError("synthetic-provider-failure"))
    remove = AsyncMock()
    if caught:
        assert (
            await external_revocation(invoke, remove, caught=(RuntimeError,), failure="unknown")
            == "unknown"
        )
    else:
        with pytest.raises(RuntimeError):
            await external_revocation(invoke, remove)
    remove.assert_awaited_once()


@pytest.mark.anyio
async def test_callback_consumption_denial_precedes_credentials_and_provider_io():
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = ("denied",)
    secrets = Mock()
    binding = OAuthBinding(connection, secrets, "gsc", ValueError, lambda _: nullcontext(), True)
    with pytest.raises(ValueError, match="GSC_ATTEMPT_UNAVAILABLE"):
        await binding.complete(
            (b"synthetic-session", "synthetic-generation", uuid4()),
            uuid4(),
            "s" * 43,
            "https://dashboard.example.invalid/auth/gsc/callback",
            Mock(),
            Mock(),
            Mock(),
            Mock(),
        )
    assert not secrets.mock_calls
