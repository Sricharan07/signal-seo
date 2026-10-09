"""Dedicated test login composition; never imports the disposable local pilot."""

import asyncio
import json
import os
import ssl
import stat
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import httpx2
import psycopg
from fastapi.responses import JSONResponse
from psycopg.conninfo import make_conninfo
from signal_core.crawl_http import CrawlFetchRejected, PinnedHttpFetcher
from signal_core.crawl_urls import validate_public_addresses
from signal_core.github_read_binding import OpenBaoGitHubAppCredential
from signal_core.gsc_secrets import OpenBaoGscSecrets
from signal_core.integration_scope import (
    IntegrationScope,
    IntegrationScopeUnavailable,
    load_integration_scope,
)
from signal_core.oidc_login import OidcClientRegistration
from signal_core.origin_verification import InvalidOriginVerification
from signal_core.pkce_secrets import OpenBaoPkceClient
from signal_core.recovery_authority import OpenBaoRecoveryAuthority
from signal_core.session_issuance import SessionPolicy
from signal_core.site_onboarding import InvalidSiteOnboarding
from signal_core.slack_secrets import OpenBaoSlackSecrets

from signal_api.authentication import ComposedBrowserLogin
from signal_api.browser_security import BrowserSecurity, runtime_readiness
from signal_api.config import ApiSettings
from signal_api.integration_connectors import (
    ROLES,
    IntegrationProviderResolver,
    IntegrationSlackGateway,
    OwnerEgressFactory,
    admitted_connector_route,
    protected_artifact_store,
    read_owner_artifact_key,
)
from signal_api.main import create_app
from signal_api.owner_connector_http import ComposedGithubGateway, ComposedGscGateway
from signal_api.test_identity_proof import TestAssertionCapture

BAO = "https://openbao:8200"
DIRECTORY = Path("/run/signal-application")
IDENTITY_ROUTES = frozenset(
    {
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        ("GET", "/v1/capabilities"),
        ("GET", "/v1/session/login"),
        ("GET", "/v1/session/callback"),
        ("GET", "/v1/organizations"),
        ("GET", "/v1/session"),
        ("GET", "/v1/session/csrf"),
        ("GET", "/v1/session/logout-csrf"),
        ("GET", "/v1/session/tenant-csrf"),
        ("POST", "/v1/session/switch-tenant"),
        ("POST", "/v1/session/logout"),
        ("GET", "/v1/sites"),
        ("POST", "/v1/sites"),
        ("PUT", "/v1/session/site"),
    }
)


def admitted_identity_route(method: str, path: str) -> bool:
    if (method, path) in IDENTITY_ROUTES:
        return True
    parts = path.split("/")
    if (
        method != "POST"
        or len(parts) != 5
        or parts[:3] != ["", "v1", "sites"]
        or parts[4] not in {"origin-challenges", "verify-origin"}
    ):
        return False
    try:
        identifier = UUID(parts[3])
        return identifier.version == 4 and str(identifier) == parts[3]
    except ValueError:
        return False


@dataclass(frozen=True)
class IntegrationBrowserLogin(ComposedBrowserLogin):
    """The dedicated environment may onboard only its approved test origin."""

    integration_scope: IntegrationScope = field(
        default_factory=load_integration_scope, kw_only=True
    )

    async def onboard_site(
        self,
        *,
        session_token: str,
        name: str,
        primary_origin: str,
        timezone: str,
        reporting_currency: str,
        expected_session_version: int,
        idempotency_key: UUID,
    ):
        if primary_origin != self.integration_scope.origin:
            raise InvalidSiteOnboarding("Only the approved test origin may be onboarded.")
        return await super().onboard_site(
            session_token=session_token,
            name=name,
            primary_origin=primary_origin,
            timezone=timezone,
            reporting_currency=reporting_currency,
            expected_session_version=expected_session_version,
            idempotency_key=idempotency_key,
        )

    async def issue_origin_challenge(
        self,
        *,
        session_token: str,
        site_id: UUID,
        origin: str,
        idempotency_key: UUID,
    ):
        if origin != self.integration_scope.origin:
            raise InvalidOriginVerification("Only the approved test origin may be verified.")
        return await super().issue_origin_challenge(
            session_token=session_token,
            site_id=site_id,
            origin=origin,
            idempotency_key=idempotency_key,
        )

    async def verify_origin(
        self,
        *,
        session_token: str,
        site_id: UUID,
        challenge_id: UUID,
        origin: str,
        idempotency_key: UUID,
    ):
        if origin != self.integration_scope.origin:
            raise InvalidOriginVerification("Only the approved test origin may be verified.")
        return await super().verify_origin(
            session_token=session_token,
            site_id=site_id,
            challenge_id=challenge_id,
            origin=origin,
            idempotency_key=idempotency_key,
        )


def private_file(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or not 1 <= info.st_size <= 65536
        ):
            raise RuntimeError("Protected runtime material was rejected.")
        return os.read(descriptor, 65537)
    finally:
        os.close(descriptor)


class IntegrationOriginResolver:
    """Read the expiring protected public pin for the one owner-approved test host."""

    def __init__(self, path: Path, clock=time.time, *, integration_scope=None):
        self.path, self.clock = path, clock
        self.integration_scope = integration_scope or load_integration_scope()

    def __call__(self, host: str, port: int, timeout: float):
        try:
            pin = json.loads(private_file(self.path))
            now = self.clock()
            if (
                host != self.integration_scope.host
                or port != 443
                or set(pin) != {"origin", "address", "issued_at", "expires_at"}
                or pin["origin"] != self.integration_scope.origin
                or pin["address"] != self.integration_scope.public_ipv4
                or type(pin["issued_at"]) is not int
                or type(pin["expires_at"]) is not int
                or not pin["issued_at"] <= now < pin["expires_at"]
                or not 0 < pin["expires_at"] - pin["issued_at"] <= 3600
            ):
                raise ValueError
            return validate_public_addresses((pin["address"],))
        except Exception:
            raise CrawlFetchRejected("The dedicated public-origin pin is unavailable.") from None


class PrivateIdentityTransport(httpx2.AsyncBaseTransport):
    """Only three fixed issuer operations may reach the private TLS identity peer."""

    def __init__(self, context: ssl.SSLContext, integration_scope=None):
        self.context = context
        self.integration_scope = integration_scope or load_integration_scope()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        allowed = {
            ("GET", self.integration_scope.issuer + "/.well-known/openid-configuration"),
            ("GET", self.integration_scope.issuer + "/protocol/openid-connect/certs"),
            ("POST", self.integration_scope.issuer + "/protocol/openid-connect/token"),
        }
        if (request.method, str(request.url)) not in allowed:
            raise httpx2.TransportError("Unregistered identity operation.")
        transport = httpx2.AsyncHTTPTransport(verify=self.context, retries=0)
        private_request = httpx2.Request(
            request.method,
            request.url.copy_with(scheme="https", host="identity", port=8443),
            headers={
                **{key: value for key, value in request.headers.items() if key != "host"},
                "Host": "identity:8443",
            },
            stream=request.stream,
            extensions=request.extensions,
        )
        try:
            response = await transport.handle_async_request(private_request)
        except BaseException:
            await transport.aclose()
            raise
        if response.is_closed:
            await transport.aclose()
        else:
            response.stream = _ClosingStream(response.stream, transport)
        return response


class _ClosingStream(httpx2.AsyncByteStream):
    def __init__(self, stream, transport):
        self.stream, self.transport = stream, transport

    async def __aiter__(self):
        async for chunk in self.stream:
            yield chunk

    async def aclose(self):
        try:
            await self.stream.aclose()
        finally:
            await self.transport.aclose()


class WorkloadTokens:
    def __init__(
        self,
        context: ssl.SSLContext,
        connector_roles=(),
        *,
        directory=DIRECTORY,
        policy_prefix="signal-test-",
    ):
        if connector_roles not in ((), ROLES):
            raise ValueError("Exact connector workload roles are required.")
        self.context = context
        self.directory, self.policy_prefix = directory, policy_prefix
        self.roles = ("pkce-writer", "pkce-consumer", "recovery-reader") + connector_roles
        self.tokens: dict[str, str] = {}
        self.healthy = False

    async def authenticate(self):
        async with httpx2.AsyncClient(
            verify=self.context, trust_env=False, timeout=5, follow_redirects=False
        ) as client:
            for role in self.roles:
                credential = json.loads(private_file(self.directory / (role + ".json")))
                if set(credential) != {"role_id", "secret_id"}:
                    raise RuntimeError("Workload authentication configuration rejected.")
                response = await client.post(BAO + "/v1/auth/approle/login", json=credential)
                if response.status_code != 200 or len(response.content) > 65536:
                    raise RuntimeError("Workload authentication failed.")
                auth = response.json()["auth"]
                token = auth["client_token"]
                if (
                    not isinstance(token, str)
                    or not 1 <= len(token) <= 4096
                    or any(ord(character) < 33 or ord(character) > 126 for character in token)
                ):
                    raise RuntimeError("Workload credential rejected.")
                if (
                    auth["renewable"] is not True
                    or auth["lease_duration"] != 300
                    or auth["policies"] != [self.policy_prefix + role]
                ):
                    await client.post(
                        BAO + "/v1/auth/token/revoke-self", headers={"X-Vault-Token": token}
                    )
                    raise RuntimeError("Workload token authority rejected.")
                self.tokens[role] = token
        self.healthy = True

    async def renew(self):
        while True:
            await asyncio.sleep(60)
            try:
                async with httpx2.AsyncClient(
                    verify=self.context, trust_env=False, timeout=5, follow_redirects=False
                ) as client:
                    for token in self.tokens.values():
                        response = await client.post(
                            BAO + "/v1/auth/token/renew-self",
                            headers={"X-Vault-Token": token},
                            json={"increment": "300s"},
                        )
                        if response.status_code != 200 or len(response.content) > 65536:
                            raise RuntimeError("Workload token renewal failed.")
                        auth = response.json()["auth"]
                        if auth["client_token"] != token or auth["lease_duration"] != 300:
                            raise RuntimeError("Workload token renewal rejected.")
            except Exception:
                self.healthy = False
                return

    async def revoke(self):
        self.healthy = False
        async with httpx2.AsyncClient(
            verify=self.context, trust_env=False, timeout=5, follow_redirects=False
        ) as client:
            for token in self.tokens.values():
                try:
                    await client.post(
                        BAO + "/v1/auth/token/revoke-self", headers={"X-Vault-Token": token}
                    )
                except Exception:
                    pass
        self.tokens.clear()


def create_integration_app():
    try:
        scope = load_integration_scope()
    except IntegrationScopeUnavailable:
        application = create_app(settings=ApiSettings(environment="test", expose_docs=False))

        @application.middleware("http")
        async def unavailable(request, call_next):
            if request.url.path == "/v1/capabilities":
                return JSONResponse(
                    {
                        "schema_version": 1,
                        "release_status": "dedicated_test_unconfigured",
                        "production_writes_enabled": False,
                        "capabilities": [
                            {"key": key, "availability": "disabled"}
                            for key in (
                                "identity.oidc_http_login",
                                "identity.session_issuance",
                                "dashboard.site_context",
                                "dashboard.site_onboarding",
                                "dashboard.origin_verification",
                                "database.command_acceptance",
                                "provider.slack",
                                "provider.gsc",
                                "provider.github",
                            )
                        ],
                    }
                )
            if request.url.path not in {"/health/live", "/v1/capabilities"}:
                return JSONResponse(
                    {"error": {"code": "TEST_CONFIGURATION_UNAVAILABLE"}}, status_code=503
                )
            return await call_next(request)

        return application
    context = ssl.create_default_context(cafile="/run/signal-tls/ca.pem")
    config = json.loads(private_file(DIRECTORY / "application.json"))
    if set(config) != {"identity_db_password", "csrf_key"}:
        raise RuntimeError("Dedicated application configuration rejected.")
    dsn = make_conninfo(
        host="application-database",
        port=5432,
        dbname="signal",
        user="signal_identity",
        password=config["identity_db_password"],
        sslmode="verify-full",
        sslrootcert="/run/signal-tls/ca.pem",
        connect_timeout=5,
    )
    connector_path = DIRECTORY / "connector-workloads.json"
    connector_enabled = connector_path.exists()
    if connector_enabled and json.loads(private_file(connector_path)) != {"roles": list(ROLES)}:
        raise RuntimeError("Dedicated connector workload configuration rejected.")
    tokens = WorkloadTokens(context, ROLES if connector_enabled else ())
    approval_path = DIRECTORY / "owner-bootstrap-approval.json"

    def read_approval():
        return json.loads(private_file(approval_path))

    observer = (
        TestAssertionCapture(
            read_approval(), approval_reader=read_approval, integration_scope=scope
        )
        if approval_path.exists()
        else None
    )

    async def readiness():
        return await runtime_readiness(
            application,
            tokens=tokens,
            connection_factory=lambda: psycopg.connect(dsn, autocommit=True),
            verify=context,
        )

    application = create_app(
        settings=ApiSettings(environment="test", expose_docs=False),
        readiness_probe=readiness,
        browser_security=BrowserSecurity(
            bytes.fromhex(config["csrf_key"]), frozenset({scope.origin})
        ),
    )

    @asynccontextmanager
    async def lifespan(app):
        task = None
        proof_task = None

        async def expire_proof():
            while True:
                await asyncio.sleep(15)
                await asyncio.to_thread(observer.expire)

        try:
            await tokens.authenticate()
            app.state.browser_login = IntegrationBrowserLogin(
                integration_scope=scope,
                connection_factory=lambda: psycopg.connect(dsn, autocommit=True),
                registration=OidcClientRegistration(
                    scope.issuer, "signal-test-dashboard", scope.origin + "/auth/callback"
                ),
                pkce_writer=OpenBaoPkceClient(BAO, tokens.tokens["pkce-writer"]),
                pkce_consumer=OpenBaoPkceClient(BAO, tokens.tokens["pkce-consumer"]),
                recovery_authority=OpenBaoRecoveryAuthority(BAO, tokens.tokens["recovery-reader"]),
                oidc_transport=PrivateIdentityTransport(context, scope),
                oidc_verify=context,
                pkce_verify=context,
                recovery_verify=context,
                policy=SessionPolicy(
                    primary_acr_values=frozenset({"0"}),
                    mfa_acr_values=frozenset({"1", "2"}),
                    required_mfa_methods=frozenset({"otp"}),
                ),
                verified_assertion_observer=observer,
                origin_fetcher=PinnedHttpFetcher(
                    IntegrationOriginResolver(
                        DIRECTORY / "origin-pin.json", integration_scope=scope
                    )
                ),
            )
            if connector_enabled:
                artifact_key = await read_owner_artifact_key(
                    BAO,
                    tokens.tokens["owner-artifact-reader"],
                    context,
                )
                app.state.browser_slack = IntegrationSlackGateway(
                    integration_scope=scope,
                    connection_factory=lambda: psycopg.connect(dsn, autocommit=True),
                    secrets_store=OpenBaoSlackSecrets(BAO, tokens.tokens["slack-connector"]),
                    recovery_authority=app.state.browser_login.recovery_authority,
                    dashboard_origin=scope.origin,
                    egress_factory=OwnerEgressFactory(
                        lambda: psycopg.connect(dsn, autocommit=True),
                        protected_artifact_store(),
                        artifact_key,
                        IntegrationProviderResolver(DIRECTORY / "provider-pins.json", private_file),
                        "slack",
                        integration_scope=scope,
                    ),
                    secret_options={"verify": context},
                    recovery_options={"verify": context},
                )
                common = {
                    "integration_scope": scope,
                    "connection_factory": lambda: psycopg.connect(dsn, autocommit=True),
                    "recovery_authority": app.state.browser_login.recovery_authority,
                    "recovery_options": {"verify": context},
                }

                def owner_egress(connector):
                    return OwnerEgressFactory(
                        common["connection_factory"],
                        protected_artifact_store(),
                        artifact_key,
                        IntegrationProviderResolver(DIRECTORY / "provider-pins.json", private_file),
                        connector,
                        integration_scope=scope,
                    )

                app.state.browser_gsc = ComposedGscGateway(
                    **common,
                    egress_factory=owner_egress("gsc"),
                    secrets_store=OpenBaoGscSecrets(BAO, tokens.tokens["gsc-connector"]),
                    secret_options={"verify": context},
                )
                app.state.browser_github = ComposedGithubGateway(
                    **common,
                    egress_factory=owner_egress("github"),
                    credential=OpenBaoGitHubAppCredential(BAO, tokens.tokens["github-reader"]),
                    credential_options={"openbao_verify": context},
                )
            task = asyncio.create_task(tokens.renew())
            if observer is not None:
                proof_task = asyncio.create_task(expire_proof())
            yield
        finally:
            for running in (task, proof_task):
                if running is not None:
                    running.cancel()
                    try:
                        await running
                    except asyncio.CancelledError:
                        pass
            await tokens.revoke()

    application.router.lifespan_context = lifespan

    @application.middleware("http")
    async def admission(request, call_next):
        if not tokens.healthy and request.url.path != "/health/live":
            return JSONResponse({"error": {"code": "TEST_IDENTITY_UNAVAILABLE"}}, status_code=503)
        # No worker is deployed by this login profile, so commands cannot be queued.
        if request.url.path == "/v1/capabilities":
            return JSONResponse(
                {
                    "schema_version": 1,
                    "release_status": "dedicated_test_onboarding",
                    "production_writes_enabled": False,
                    "capabilities": [
                        {"key": key, "availability": availability}
                        for key, availability in (
                            ("identity.oidc_http_login", "internal_only"),
                            ("identity.session_issuance", "internal_only"),
                            ("dashboard.site_context", "internal_only"),
                            ("dashboard.site_onboarding", "internal_only"),
                            ("dashboard.origin_verification", "internal_only"),
                            ("database.command_acceptance", "disabled"),
                            (
                                "provider.slack",
                                "internal_only" if connector_enabled else "disabled",
                            ),
                            ("provider.gsc", "internal_only" if connector_enabled else "disabled"),
                            (
                                "provider.github",
                                "internal_only" if connector_enabled else "disabled",
                            ),
                        )
                    ],
                }
            )
        if not admitted_identity_route(request.method, request.url.path) and not (
            connector_enabled and admitted_connector_route(request.method, request.url.path, scope)
        ):
            return JSONResponse({"error": {"code": "TEST_CAPABILITY_UNAVAILABLE"}}, status_code=503)
        return await call_next(request)

    return application
