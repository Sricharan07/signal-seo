"""Generic private identity composition, with unqualified provider work denied."""

import asyncio
import json
import os
import ssl
import time
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi.responses import JSONResponse
from psycopg.conninfo import make_conninfo
from signal_core.oidc_login import OidcClientRegistration
from signal_core.owner_bootstrap import BOOTSTRAP_POLICY
from signal_core.pkce_secrets import OpenBaoPkceClient
from signal_core.recovery_authority import OpenBaoRecoveryAuthority
from signal_core.self_host_config import CLIENT, SelfHostConfig, provider_projection, unique_object
from signal_core.session_issuance import SessionPolicy, _validate_verified_identity

from signal_api.authentication import ComposedBrowserLogin
from signal_api.browser_security import BrowserSecurity, runtime_readiness
from signal_api.config import ApiSettings
from signal_api.integration_runtime import (
    IDENTITY_ROUTES,
    PrivateIdentityTransport,
    WorkloadTokens,
    private_file,
)
from signal_api.main import create_app

DIRECTORY = Path("/run/signal-self-host")
PROOF = Path("/tmp/self-host-owner-proof.json")
BAO = "https://openbao:8200"


def document(path):
    return json.loads(private_file(path), object_pairs_hook=unique_object)


class OwnerProofCapture:
    """Capture only a fresh signed owner assertion, never grant bootstrap privileges."""

    def __init__(self, config, bootstrap, destination=PROOF, clock=time.time):
        if (
            not isinstance(bootstrap, dict)
            or bootstrap.get("status") != "armed"
            or type(bootstrap.get("approved_at")) is not int
            or type(bootstrap.get("expires_at")) is not int
            or not 0 < bootstrap["expires_at"] - bootstrap["approved_at"] <= 3600
        ):
            raise ValueError("Owner approval rejected.")
        self.config, self.bootstrap, self.destination, self.clock = (
            config,
            bootstrap,
            destination,
            clock,
        )

    def expire(self):
        if self.destination.exists() and (
            self.clock() >= self.bootstrap["expires_at"]
            or self.clock() - self.destination.stat().st_mtime >= 300
        ):
            self.destination.unlink()

    def __call__(self, attempt, response, identity):
        self.expire()
        now = self.clock()
        approval = self.bootstrap
        if not approval["approved_at"] <= now < approval["expires_at"]:
            self.destination.unlink(missing_ok=True)
            return
        if identity.subject != approval["subject"]:
            return
        if (
            identity.issuer != self.config.issuer
            or identity.client_id != CLIENT
            or attempt.registration
            != OidcClientRegistration(
                self.config.issuer, CLIENT, self.config.origin + "/auth/callback"
            )
            or attempt.purpose != "login"
            or identity.issued_at < approval["approved_at"]
        ):
            raise ValueError("Owner proof rejected.")
        from datetime import UTC, datetime

        _validate_verified_identity(identity, BOOTSTRAP_POLICY, datetime.fromtimestamp(now, UTC))
        content = json.dumps(
            {
                "schema_version": 1,
                "attempt_id": str(attempt.id),
                "id_token": response.id_token,
                "access_token": response.access_token,
                "expires_in": response.expires_in,
            }
        ).encode()
        if len(content) > 65536:
            raise ValueError("Owner proof exceeded its bound.")
        # Expired proofs cannot obstruct a later explicitly rearmed bootstrap.
        if self.destination.exists() and now - self.destination.stat().st_mtime > 300:
            self.destination.unlink()
        try:
            descriptor = os.open(
                self.destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError:
            return
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)


def create_self_host_app():
    config = SelfHostConfig.parse(document(DIRECTORY / "config.json"))
    secret = document(DIRECTORY / "application.json")
    if set(secret) != {"identity_db_password", "csrf_key"}:
        raise RuntimeError("Private application configuration rejected.")
    configured = document(DIRECTORY / "providers.json")
    providers = provider_projection(configured)
    context = ssl.create_default_context(cafile=str(DIRECTORY / "tls/ca.pem"))
    dsn = make_conninfo(
        host="application-database",
        dbname="signal",
        user="signal_identity",
        password=secret["identity_db_password"],
        sslmode="verify-full",
        sslrootcert=str(DIRECTORY / "tls/ca.pem"),
        connect_timeout=5,
    )
    tokens = WorkloadTokens(context, directory=DIRECTORY, policy_prefix="signal-self-host-")

    async def readiness():
        return await runtime_readiness(
            application,
            tokens=tokens,
            connection_factory=lambda: psycopg.connect(dsn, autocommit=True),
            verify=context,
        )

    application = create_app(
        settings=ApiSettings(environment="production"),
        readiness_probe=readiness,
        browser_security=BrowserSecurity(
            bytes.fromhex(secret["csrf_key"]), frozenset({config.origin})
        ),
    )

    @asynccontextmanager
    async def lifespan(app):
        task = proof_task = None
        try:
            await tokens.authenticate()
            bootstrap_path = DIRECTORY / "bootstrap.json"
            observer = (
                OwnerProofCapture(config, document(bootstrap_path))
                if bootstrap_path.exists()
                else None
            )
            app.state.browser_login = ComposedBrowserLogin(
                connection_factory=lambda: psycopg.connect(dsn, autocommit=True),
                registration=OidcClientRegistration(
                    config.issuer, CLIENT, config.origin + "/auth/callback"
                ),
                pkce_writer=OpenBaoPkceClient(BAO, tokens.tokens["pkce-writer"]),
                pkce_consumer=OpenBaoPkceClient(BAO, tokens.tokens["pkce-consumer"]),
                recovery_authority=OpenBaoRecoveryAuthority(BAO, tokens.tokens["recovery-reader"]),
                oidc_transport=PrivateIdentityTransport(context, config),
                oidc_verify=context,
                pkce_verify=context,
                recovery_verify=context,
                policy=SessionPolicy(
                    primary_acr_values=frozenset({"0"}),
                    mfa_acr_values=frozenset({"1", "2"}),
                    required_mfa_methods=frozenset({"otp"}),
                ),
                verified_assertion_observer=observer,
            )
            task = asyncio.create_task(tokens.renew())
            if observer is not None:

                async def expire_proof():
                    while True:
                        await asyncio.sleep(1)
                        try:
                            observer.expire()
                        except Exception:
                            tokens.healthy = False
                            return

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
            PROOF.unlink(missing_ok=True)
            await tokens.revoke()

    application.router.lifespan_context = lifespan

    @application.middleware("http")
    async def admission(request, call_next):
        path = request.url.path
        if path == "/v1/capabilities" and request.method == "GET":
            return JSONResponse(
                {
                    "schema_version": 1,
                    "release_status": "self_host_not_certified",
                    "production_writes_enabled": False,
                    "capabilities": [
                        {"key": "provider." + item["provider"], "availability": "disabled"}
                        for item in providers
                    ]
                    + [{"key": "database.command_acceptance", "availability": "disabled"}]
                    + [
                        {
                            "key": key,
                            "availability": "internal_only" if tokens.healthy else "disabled",
                        }
                        for key in (
                            "identity.oidc_http_login",
                            "identity.session_issuance",
                            "dashboard.site_context",
                            "dashboard.site_onboarding",
                        )
                    ],
                }
            )
        if path == "/v1/self-host/providers" and request.method == "GET":
            return JSONResponse({"schema_version": 1, "providers": providers})
        if path != "/health/live" and not tokens.healthy:
            return JSONResponse(
                {"error": {"code": "SELF_HOST_IDENTITY_UNAVAILABLE"}}, status_code=503
            )
        if (request.method, path) not in IDENTITY_ROUTES:
            return JSONResponse(
                {"error": {"code": "SELF_HOST_CAPABILITY_UNAVAILABLE"}}, status_code=503
            )
        return await call_next(request)

    return application
