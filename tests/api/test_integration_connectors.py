import json
import ssl
from contextlib import contextmanager
from uuid import UUID, uuid4

import httpx2
import pytest
from signal_api.config import ApiSettings
from signal_api.integration_connectors import (
    HOSTS,
    ROLES,
    IntegrationProviderResolver,
    IntegrationSlackGateway,
    OwnerEgressFactory,
    OwnerOnboardingProvider,
    admitted_connector_route,
    read_owner_artifact_key,
)
from signal_api.integration_runtime import WorkloadTokens, private_file
from signal_api.main import create_app
from signal_api.slack_http import ComposedSlackGateway, SlackComplete, SlackInstall
from signal_core.crawl_http import CrawlFetchRejected
from signal_core.integration_scope import load_integration_scope
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider
from signal_core.slack_protocol import SlackRejected

TEST_SITE = UUID("00000000-0000-4000-8000-000000000002")
TEST_WORKSPACE = "T0000000000"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    "change",
    [
        None,
        {"issued_at": True},
        {"expires_at": 1000},
        {"issued_at": 1001},
        {"expires_at": 5000},
        {"extra": "denied"},
    ],
)
def test_provider_pins_are_screened_exact_and_expiring(tmp_path, change):
    path = tmp_path / "pin"
    document = {
        "hosts": {host: "93.184.216.34" for host in HOSTS},
        "issued_at": 900,
        "expires_at": 1100,
    }
    if change:
        document.update(change)
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    resolver = IntegrationProviderResolver(path, private_file, clock=lambda: 1000)
    if change is None:
        assert resolver("slack.com", 443, 5) == ("93.184.216.34",)
    else:
        with pytest.raises(CrawlFetchRejected):
            resolver("slack.com", 443, 5)
    for host, port in (("attacker.invalid", 443), ("slack.com", 80)):
        with pytest.raises(CrawlFetchRejected):
            resolver(host, port, 5)


@pytest.mark.parametrize("unsafe", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "invalid"])
def test_any_unsafe_provider_pin_rejects_entire_set(tmp_path, unsafe):
    path = tmp_path / "pin"
    path.write_text(
        json.dumps(
            {"hosts": {host: unsafe for host in HOSTS}, "issued_at": 900, "expires_at": 1100}
        )
    )
    path.chmod(0o600)
    with pytest.raises(CrawlFetchRejected):
        IntegrationProviderResolver(path, private_file, clock=lambda: 1000)("slack.com", 443, 5)


def test_runtime_roles_are_an_exact_separate_option():
    context = ssl.create_default_context()
    assert len(WorkloadTokens(context).roles) == 3
    assert len(WorkloadTokens(context, ROLES).roles) == 7
    for roles in (("root",), ("slack-connector",), list(ROLES)):
        with pytest.raises(ValueError):
            WorkloadTokens(context, roles)


@pytest.mark.parametrize(
    "code", ["EGRESS_DEFERRED", "EGRESS_DISPATCH_UNCERTAIN", "EGRESS_BODY_NOT_RETAINED"]
)
def test_only_predispatch_deferral_retries_same_operation(monkeypatch, code):
    calls = []
    operation = uuid4()
    provider = object.__new__(OwnerOnboardingProvider)

    def request(self, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ProviderEgressUnavailable(code, retryable=code == "EGRESS_DEFERRED")
        return "actual-response"

    monkeypatch.setattr(SharedEgressProvider, "request_json", request)
    monkeypatch.setattr("signal_api.integration_connectors.time.sleep", lambda *args: None)
    if code == "EGRESS_DEFERRED":
        assert provider.request_json(operation_id=operation) == "actual-response"
        assert calls == [{"operation_id": operation}] * 2
    else:
        with pytest.raises(ProviderEgressUnavailable):
            provider.request_json(operation_id=operation)
        assert len(calls) == 1


def test_only_exact_owner_slack_routes_are_admitted():
    assert admitted_connector_route("GET", f"/v1/sites/{TEST_SITE}/slack", load_integration_scope())
    assert admitted_connector_route(
        "POST", f"/v1/sites/{TEST_SITE}/slack", load_integration_scope()
    )
    for method, path in (
        ("DELETE", f"/v1/sites/{TEST_SITE}/slack"),
        ("POST", f"/v1/sites/{uuid4()}/slack"),
        ("POST", f"/v1/sites/{TEST_SITE}/commands/snapshot"),
        ("POST", f"/v1/sites/{TEST_SITE}/slack/unknown"),
    ):
        assert not admitted_connector_route(method, path, load_integration_scope())


@pytest.mark.anyio
async def test_late_lifespan_composition_is_used_and_removal_fails_closed():
    from tests.api.test_slack_http import SITE, TOKEN, Gateway

    app = create_app(settings=ApiSettings(environment="test"))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (await api.get(f"/v1/sites/{SITE}/slack")).status_code == 503
        app.state.browser_slack = Gateway()
        assert (
            await api.get(
                f"/v1/sites/{SITE}/slack",
                headers={
                    "Cookie": f"__Host-signal_session={TOKEN}",
                },
            )
        ).status_code == 200
        app.state.browser_slack = None
        assert (await api.get(f"/v1/sites/{SITE}/slack")).status_code == 503


@pytest.mark.anyio
@pytest.mark.parametrize(
    "site,workspace,channel",
    [
        (TEST_SITE, TEST_WORKSPACE, "C0000000000"),
        (uuid4(), TEST_WORKSPACE, "C0000000000"),
        (TEST_SITE, "T00000001", "C0000000000"),
        (TEST_SITE, TEST_WORKSPACE, "C1111111111"),
    ],
)
async def test_test_slack_binding_cannot_escape_approved_scope(
    monkeypatch, site, workspace, channel
):
    calls = []

    async def control(self, token, selected, command):
        calls.append((selected, command.workspace_id))
        return "fixture"

    monkeypatch.setattr(ComposedSlackGateway, "control", control)
    gateway = object.__new__(IntegrationSlackGateway)
    from signal_core.integration_scope import load_integration_scope

    object.__setattr__(gateway, "integration_scope", load_integration_scope())
    command = SlackInstall(
        operation="install", workspace_id=workspace, channel_id=channel, max_risk=0
    )
    if site == TEST_SITE and workspace == TEST_WORKSPACE and channel == "C0000000000":
        assert await gateway.control("fixture", site, command) == "fixture"
        assert len(calls) == 1
    else:
        with pytest.raises(SlackRejected):
            await gateway.control("fixture", site, command)
        assert not calls


@pytest.mark.anyio
@pytest.mark.parametrize("fails", [False, True])
async def test_owner_egress_context_closes_after_control_even_on_failure(monkeypatch, fails):
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("connection-closed")

    class Service:
        recovery_generation = "fixture-generation"

        async def complete_install(self, **kwargs):
            assert kwargs["egress"] == "provider"
            if fails:
                raise RuntimeError("fixture transport failure")
            return uuid4()

    @contextmanager
    def egress(token, site, generation):
        assert (token, site, generation) == ("fixture", TEST_SITE, "fixture-generation")
        calls.append("opened")
        try:
            yield "provider"
        finally:
            calls.append("closed")

    async def service(self, connection):
        return Service()

    monkeypatch.setattr(ComposedSlackGateway, "service", service)
    gateway = ComposedSlackGateway(Connection, None, None, "https://test.invalid", egress)
    command = SlackComplete(
        operation="complete", attempt_id=uuid4(), state="s" * 43, code="fixture"
    )
    if fails:
        with pytest.raises(RuntimeError):
            await gateway.control("fixture", TEST_SITE, command)
    else:
        assert "binding_id" in await gateway.control("fixture", TEST_SITE, command)
    assert calls == ["opened", "closed", "connection-closed"]


def test_unapproved_factory_site_fails_before_opening_connections():
    calls = []
    factory = OwnerEgressFactory(lambda: calls.append("opened"), None, None, None, "slack")
    with pytest.raises(ValueError), factory("fixture", uuid4(), "fixture-generation"):
        pass
    assert not calls


@pytest.mark.anyio
@pytest.mark.parametrize("version", [1, True, 2])
async def test_artifact_key_has_its_own_verified_tls_fixed_generation(monkeypatch, version):
    from signal_core.openbao_http import OpenBaoResponse

    context = ssl.create_default_context()
    calls = []

    async def request(**kwargs):
        calls.append(kwargs)
        return OpenBaoResponse(
            200,
            {"content-type": "application/json"},
            json.dumps(
                {
                    "data": {
                        "data": {"reference": "owner-robots:test-v1", "material_hex": "ab" * 32},
                        "metadata": {"version": version, "destroyed": False, "deletion_time": ""},
                    },
                }
            ).encode(),
        )

    monkeypatch.setattr("signal_core.openbao_http.request", request)
    if type(version) is int and version == 1:
        key = await read_owner_artifact_key("https://openbao:8200", "fixture", context)
        assert key.material == bytes.fromhex("ab" * 32) and key.reference == "owner-robots:test-v1"
    else:
        with pytest.raises(RuntimeError):
            await read_owner_artifact_key("https://openbao:8200", "fixture", context)
    assert calls[0]["verify"] is context
    assert calls[0]["path"] == "/signal-identity/data/platform/owner-artifacts"
