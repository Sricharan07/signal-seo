import json
import ssl
from uuid import uuid4

import httpx2
import pytest
from signal_api.integration_runtime import (
    IntegrationBrowserLogin,
    IntegrationOriginResolver,
    PrivateIdentityTransport,
    WorkloadTokens,
    admitted_identity_route,
    private_file,
)

ORIGIN = "https://signal-test.example.invalid"
ISSUER = ORIGIN + "/identity/realms/signal"


def configured_gateway():
    from signal_core.integration_scope import load_integration_scope

    gateway = object.__new__(IntegrationBrowserLogin)
    object.__setattr__(gateway, "integration_scope", load_integration_scope())
    return gateway


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1/organizations"),
        ("GET", "/v1/session"),
        ("GET", "/v1/session/login"),
        ("GET", "/v1/session/callback"),
        ("GET", "/v1/session/tenant-csrf"),
        ("POST", "/v1/session/switch-tenant"),
        ("GET", "/v1/session/logout-csrf"),
        ("POST", "/v1/session/logout"),
        ("GET", "/v1/sites"),
        ("POST", "/v1/sites"),
        ("PUT", "/v1/session/site"),
    ],
)
def test_dedicated_identity_profile_admits_normal_owner_session_routes(method, path):
    assert admitted_identity_route(method, path)


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1/organizations"),
        ("POST", "/v1/session"),
        ("POST", "/v1/session/login"),
        ("GET", "/v1/session/switch-tenant"),
        ("GET", "/v1/session/unknown"),
        ("POST", "/v1/session/site"),
        ("POST", "/v1/commands"),
        ("DELETE", "/v1/sites"),
        ("GET", "/v1/sites/unknown"),
        ("POST", "/v1/sites/unknown/commands/snapshot"),
        ("POST", "/v1/standing-authorizations"),
        ("GET", "/v1/capabilities/unknown"),
    ],
)
def test_identity_profile_denies_wrong_methods_prefixes_and_product_authority(method, path):
    assert not admitted_identity_route(method, path)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "origin",
    [ORIGIN, "https://example.invalid", ORIGIN + "/", "http://signal-test.example.invalid"],
)
async def test_dedicated_onboarding_preserves_normal_authority_and_exact_origin(
    monkeypatch,
    origin,
):
    from signal_api.authentication import ComposedBrowserLogin
    from signal_core.site_onboarding import InvalidSiteOnboarding

    observed = []
    values = dict(
        session_token="opaque-test-fixture",
        name="Signal integration test",
        primary_origin=origin,
        timezone="America/Phoenix",
        reporting_currency="USD",
        expected_session_version=1,
        idempotency_key=uuid4(),
    )

    async def normal_onboarding(self, **kwargs):
        observed.append(kwargs)
        return "fixture-result"

    monkeypatch.setattr(ComposedBrowserLogin, "onboard_site", normal_onboarding)
    gateway = configured_gateway()
    if origin == ORIGIN:
        assert await gateway.onboard_site(**values) == "fixture-result"
        assert observed == [values]
    else:
        with pytest.raises(InvalidSiteOnboarding):
            await gateway.onboard_site(**values)
        assert not observed


@pytest.mark.anyio
async def test_dedicated_onboarding_never_converts_database_failure_to_success(monkeypatch):
    from signal_api.authentication import ComposedBrowserLogin

    async def unavailable(self, **kwargs):
        raise RuntimeError("fixture state unavailable")

    monkeypatch.setattr(ComposedBrowserLogin, "onboard_site", unavailable)
    with pytest.raises(RuntimeError, match="state unavailable"):
        await configured_gateway().onboard_site(
            session_token="opaque-test-fixture",
            name="Signal integration test",
            primary_origin=ORIGIN,
            timezone="America/Phoenix",
            reporting_currency="USD",
            expected_session_version=1,
            idempotency_key=uuid4(),
        )


@pytest.mark.parametrize("operation", ["origin-challenges", "verify-origin"])
def test_origin_routes_require_exact_canonical_v4_site_and_method(operation):
    site = str(uuid4())
    path = f"/v1/sites/{site}/{operation}"
    assert admitted_identity_route("POST", path)
    for method, candidate in (
        ("GET", path),
        ("POST", path + "/unknown"),
        ("POST", f"/v1/sites/{site.upper()}/{operation}"),
        ("POST", f"/v1/sites/unknown/{operation}"),
        ("POST", f"/v1/sites/{site}/commands/snapshot"),
    ):
        assert not admitted_identity_route(method, candidate)


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["issue_origin_challenge", "verify_origin"])
@pytest.mark.parametrize("origin", [ORIGIN, "https://example.invalid"])
async def test_origin_proof_preserves_normal_owner_checks(monkeypatch, operation, origin):
    from signal_api.authentication import ComposedBrowserLogin
    from signal_core.origin_verification import InvalidOriginVerification

    values = dict(
        session_token="opaque-test-fixture", site_id=uuid4(), origin=origin, idempotency_key=uuid4()
    )
    if operation == "verify_origin":
        values["challenge_id"] = uuid4()
    observed = []

    async def normal(self, **kwargs):
        observed.append(kwargs)
        return "fixture"

    monkeypatch.setattr(ComposedBrowserLogin, operation, normal)
    method = getattr(configured_gateway(), operation)
    if origin == ORIGIN:
        assert await method(**values) == "fixture"
        assert observed == [values]
    else:
        with pytest.raises(InvalidOriginVerification):
            await method(**values)
        assert not observed


@pytest.mark.parametrize(
    "change",
    [
        None,
        {"address": "127.0.0.1"},
        {"address": "8.8.8.8"},
        {"origin": "https://example.invalid"},
        {"issued_at": 1001},
        {"expires_at": 1000},
        {"expires_at": 5000},
        {"issued_at": True},
        {"extra": "denied"},
    ],
)
def test_public_test_origin_resolver_is_exact_protected_and_expiring(tmp_path, change):
    from signal_core.crawl_http import CrawlFetchRejected

    path = tmp_path / "pin"
    pin = dict(origin=ORIGIN, address="93.184.216.34", issued_at=900, expires_at=1100)
    if change:
        pin.update(change)
    path.write_text(json.dumps(pin))
    path.chmod(0o600)
    resolver = IntegrationOriginResolver(path, clock=lambda: 1000)
    if change is None:
        assert resolver("signal-test.example.invalid", 443, 5) == ("93.184.216.34",)
    else:
        with pytest.raises(CrawlFetchRejected):
            resolver("signal-test.example.invalid", 443, 5)
    for host, port in (("attacker.invalid", 443), ("signal-test.example.invalid", 80)):
        with pytest.raises(CrawlFetchRejected):
            resolver(host, port, 5)
    path.chmod(0o644)
    with pytest.raises(CrawlFetchRejected):
        resolver("signal-test.example.invalid", 443, 5)
    path.unlink()
    with pytest.raises(CrawlFetchRejected):
        resolver("signal-test.example.invalid", 443, 5)


@pytest.mark.parametrize("mode", [0o644, 0o666, 0o755])
def test_runtime_rejects_broad_material(tmp_path, mode):
    path = tmp_path / "secret"
    path.write_text("private fixture")
    path.chmod(mode)
    with pytest.raises(RuntimeError):
        private_file(path)


def test_runtime_rejects_symlink_and_accepts_owner_only(tmp_path):
    path = tmp_path / "secret"
    path.write_text("private fixture")
    path.chmod(0o600)
    assert private_file(path) == b"private fixture"
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        private_file(link)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "method,url",
    [
        ("GET", "https://attacker.invalid/identity"),
        ("GET", ISSUER + "/protocol/openid-connect/token"),
        ("POST", ISSUER + "/protocol/openid-connect/certs"),
        ("GET", ISSUER + "/.well-known/openid-configuration?redirect=bad"),
        ("GET", "https://identity:8443/identity/admin/realms"),
    ],
)
async def test_private_identity_transport_rejects_unregistered_operation(method, url):
    transport = PrivateIdentityTransport(ssl.create_default_context())
    with pytest.raises(httpx2.TransportError, match="Unregistered"):
        await transport.handle_async_request(httpx2.Request(method, url))


@pytest.mark.anyio
async def test_private_identity_transport_preserves_issuer_and_verifies_private_peer(monkeypatch):
    context = ssl.create_default_context()
    observed = []

    class Transport(httpx2.AsyncBaseTransport):
        def __init__(self, **options):
            assert options == {"verify": context, "retries": 0}

        async def handle_async_request(self, request):
            observed.append(request)
            return httpx2.Response(200, json={"issuer": ISSUER})

        async def aclose(self):
            observed.append("closed")

    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", Transport)
    transport = PrivateIdentityTransport(context)
    async with httpx2.AsyncClient(transport=transport) as client:
        response = await client.get(ISSUER + "/.well-known/openid-configuration")
    assert response.json()["issuer"] == ISSUER
    assert (
        str(observed[0].url)
        == "https://identity:8443/identity/realms/signal/.well-known/openid-configuration"
    )
    assert observed[0].headers["host"] == "identity:8443"
    assert "closed" in observed


@pytest.mark.anyio
async def test_workload_authentication_rejects_unexpected_policy(monkeypatch):
    context = ssl.create_default_context()
    monkeypatch.setattr(
        "signal_api.integration_runtime.private_file",
        lambda path: json.dumps({"role_id": "fixture", "secret_id": "fixture"}).encode(),
    )

    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            calls.append((args, kwargs))
            return httpx2.Response(
                200,
                json={
                    "auth": {
                        "renewable": True,
                        "lease_duration": 300,
                        "policies": ["root"],
                        "client_token": "fixture",
                    }
                },
            )

    monkeypatch.setattr(httpx2, "AsyncClient", lambda **kwargs: Client())
    tokens = WorkloadTokens(context)
    with pytest.raises(RuntimeError, match="authority rejected"):
        await tokens.authenticate()
    assert not tokens.healthy
    assert not tokens.tokens
    assert calls[-1][0][0].endswith("/auth/token/revoke-self")


@pytest.mark.anyio
async def test_renewal_failure_disables_admission_without_reauthentication(monkeypatch):
    tokens = WorkloadTokens(ssl.create_default_context())
    tokens.tokens = {"pkce-writer": "fixture"}
    tokens.healthy = True
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            calls.append(url)
            return httpx2.Response(403, json={})

    async def immediate_sleep(delay):
        assert delay == 60

    monkeypatch.setattr("signal_api.integration_runtime.asyncio.sleep", immediate_sleep)
    monkeypatch.setattr(httpx2, "AsyncClient", lambda **kwargs: Client())
    await tokens.renew()
    assert not tokens.healthy
    assert calls == ["https://openbao:8200/v1/auth/token/renew-self"]
