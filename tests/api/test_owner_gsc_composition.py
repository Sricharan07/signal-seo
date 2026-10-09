import base64
import hashlib
import ssl
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from signal_core.gsc_binding import GscBindingError, complete_gsc_authorization
from signal_core.gsc_properties import GscProperty

GSC_PROPERTY = "https://signal-test.example.invalid/"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["staged", "denied", "missing_property", "transport_failure"])
async def test_gsc_callback_keeps_private_tls_and_only_exact_eligible_resource(
    monkeypatch, outcome
):
    context = ssl.create_default_context()
    verifier = "v" * 43
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    connection = Mock()
    connection.execute.return_value.fetchone.side_effect = [
        ("consumed", GSC_PROPERTY[:-1], challenge),
        (outcome,),
    ]
    secrets = SimpleNamespace(
        consume_verifier=AsyncMock(return_value=verifier),
        client_credentials=AsyncMock(return_value="synthetic-client"),
        store_refresh_token=AsyncMock(return_value="secret://synthetic-refresh"),
        destroy_refresh_token=AsyncMock(),
    )
    monkeypatch.setattr(
        "signal_core.gsc_binding._clean_transaction", lambda connection: nullcontext()
    )
    exchange = Mock(
        return_value=SimpleNamespace(
            access_token="synthetic-access", refresh_token="synthetic-refresh"
        )
    )
    if outcome == "transport_failure":
        exchange.side_effect = RuntimeError("synthetic transport failure")
    monkeypatch.setattr("signal_core.gsc_binding.exchange_gsc_code", exchange)
    properties = (
        GscProperty(GSC_PROPERTY, "url_prefix", "siteOwner", True, outcome != "missing_property"),
        GscProperty("sc-domain:example.invalid", "domain", "siteOwner", True, True),
        GscProperty("https://private-other.invalid/", "url_prefix", "siteOwner", True, True),
    )
    monkeypatch.setattr(
        "signal_core.gsc_binding.discover_gsc_properties_via_egress", Mock(return_value=properties)
    )
    args = dict(
        session_token="synthetic-" + "t" * 33,
        recovery_generation="synthetic-generation",
        site_id=uuid4(),
        attempt_id=uuid4(),
        state="s" * 43,
        code="synthetic-code",
        redirect_uri=GSC_PROPERTY + "auth/gsc/callback",
        token_operation_id=uuid4(),
        discovery_operation_id=uuid4(),
        openbao_options={"verify": context},
        allowed_property_resources=frozenset({GSC_PROPERTY}),
    )
    if outcome == "staged":
        selection = await complete_gsc_authorization(
            connection, secrets, "synthetic-egress", **args
        )
        assert selection.properties == (properties[0],)
        candidates = connection.execute.call_args.args[1][-1].obj
        assert candidates == [
            {"resource_name": GSC_PROPERTY, "property_type": "url_prefix", "eligible": True}
        ]
    else:
        with pytest.raises(RuntimeError if outcome == "transport_failure" else GscBindingError):
            await complete_gsc_authorization(connection, secrets, "synthetic-egress", **args)
    secrets.consume_verifier.assert_awaited_once_with(args["attempt_id"], verify=context)
    secrets.client_credentials.assert_awaited_once_with(verify=context)
    if outcome in {"staged", "denied"}:
        secrets.store_refresh_token.assert_awaited_once_with(
            args["attempt_id"], "synthetic-refresh", verify=context
        )
        assert secrets.destroy_refresh_token.await_count == int(outcome == "denied")
    else:
        secrets.store_refresh_token.assert_not_awaited()
