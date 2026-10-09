import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx2
import pytest
import signal_core.login_flow as login_flow_module
from joserfc import jwt
from joserfc.jwk import RSAKey
from signal_core.login_flow import (
    CompletedInvitationIdentityVerification,
    LoginFlowError,
    complete_oidc_login,
    initiate_oidc_login,
)
from signal_core.oidc_login import OidcClientRegistration, OidcLoginAuditError
from signal_core.pkce_secrets import OpenBaoPkceClient
from signal_core.recovery_authority import OpenBaoRecoveryAuthority

CLIENT_ID = "signal-dashboard"
REDIRECT_URI = "https://dashboard.example.test/auth/callback"
KEY = RSAKey.generate_key(parameters={"kid": "login-flow-test-key", "use": "sig", "alg": "RS256"})


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def session_context(admin):
    user_id = uuid4()
    issuer = "https://identity.example.test/realms/signal"
    subject = f"subject-{user_id}"
    generation = "generation-login-flow-tests-1"
    admin.execute(
        "INSERT INTO control.users "
        "(id, oidc_issuer, oidc_subject, display_name, contact_email) "
        "VALUES (%s, %s, %s, 'Login flow test user', %s)",
        (user_id, issuer, subject, f"{user_id}@example.invalid"),
    )
    return {
        "user_id": user_id,
        "issuer": issuer,
        "subject": subject,
        "generation": generation,
    }


@dataclass(frozen=True)
class Entropy:
    attempt_id: UUID = field(default_factory=uuid4)
    state: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    nonce: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    browser_binding: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(64))


class SecretServer:
    def __init__(self) -> None:
        self.verifier: str | None = None
        self.calls: list[tuple[str, str]] = []
        self.write_status = 200
        self.missing = False
        self.delete_status = 204

    def transport(self) -> httpx2.MockTransport:
        def handle(request: httpx2.Request) -> httpx2.Response:
            self.calls.append((request.method, request.url.path))
            if request.method == "POST":
                if self.write_status != 200:
                    return httpx2.Response(self.write_status, json={"errors": ["private"]})
                self.verifier = json.loads(request.content)["data"]["code_verifier"]
                return httpx2.Response(200, json={"data": {"version": 1}})
            if request.method == "GET":
                if self.missing or self.verifier is None:
                    return httpx2.Response(404, json={"errors": ["private"]})
                return httpx2.Response(
                    200,
                    json={
                        "data": {
                            "data": {"code_verifier": self.verifier},
                            "metadata": {
                                "version": 1,
                                "destroyed": False,
                                "deletion_time": "",
                            },
                        }
                    },
                )
            if request.method == "DELETE":
                response = httpx2.Response(self.delete_status)
                if self.delete_status == 204:
                    self.verifier = None
                return response
            return httpx2.Response(405)

        return httpx2.MockTransport(handle)


class ProviderServer:
    def __init__(
        self,
        registration: OidcClientRegistration,
        *,
        nonce: str | None = None,
        now: datetime | None = None,
        subject: str = "unused-subject",
        verified_email: str | None = None,
    ) -> None:
        self.registration = registration
        self.nonce = nonce
        self.now = now
        self.subject = subject
        self.verified_email = verified_email
        self.discovery_status = 200
        self.invalid_id_token = False
        self.calls: list[str] = []
        self.token_form: dict[str, list[str]] | None = None

    def transport(self) -> httpx2.MockTransport:
        async def handle(request: httpx2.Request) -> httpx2.Response:
            self.calls.append(request.url.path)
            metadata = keycloak_metadata(self.registration)
            if request.url.path.endswith("/.well-known/openid-configuration"):
                if self.discovery_status != 200:
                    return httpx2.Response(self.discovery_status, json={"error": "private"})
                return httpx2.Response(200, json=metadata)
            if request.url.path == urlsplit(metadata["jwks_uri"]).path:
                return httpx2.Response(200, json={"keys": [KEY.as_dict(private=False)]})
            if request.url.path == urlsplit(metadata["token_endpoint"]).path:
                self.token_form = parse_qs((await request.aread()).decode("ascii"))
                if self.invalid_id_token:
                    encoded_token = "not-a-jwt"
                else:
                    if self.nonce is None or self.now is None:
                        raise AssertionError("Callback provider requires nonce and time")
                    access_token = "flow-access-token"
                    digest = hashlib.sha256(access_token.encode("ascii")).digest()
                    at_hash = base64.urlsafe_b64encode(digest[:16]).rstrip(b"=").decode("ascii")
                    timestamp = int(self.now.timestamp())
                    claims = {
                        "iss": self.registration.issuer,
                        "sub": self.subject,
                        "aud": self.registration.client_id,
                        "exp": timestamp + 300,
                        "iat": timestamp,
                        "auth_time": timestamp - 5,
                        "nonce": self.nonce,
                        "at_hash": at_hash,
                        "acr": "1",
                    }
                    if self.verified_email is not None:
                        claims.update(
                            {
                                "email": self.verified_email,
                                "email_verified": True,
                            }
                        )
                    encoded_token = jwt.encode(
                        {"alg": "RS256", "kid": "login-flow-test-key"},
                        claims,
                        KEY,
                        algorithms=["RS256"],
                    )
                return httpx2.Response(
                    200,
                    json={
                        "id_token": encoded_token,
                        "access_token": "flow-access-token",
                        "expires_in": 300,
                        "token_type": "Bearer",
                    },
                )
            return httpx2.Response(404)

        return httpx2.MockTransport(handle)


def keycloak_metadata(registration: OidcClientRegistration) -> dict:
    prefix = f"{registration.issuer}/protocol/openid-connect"
    return {
        "issuer": registration.issuer,
        "authorization_endpoint": f"{prefix}/auth",
        "token_endpoint": f"{prefix}/token",
        "jwks_uri": f"{prefix}/certs",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


def registration(issuer: str) -> OidcClientRegistration:
    return OidcClientRegistration(
        issuer=issuer,
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
    )


def writer() -> OpenBaoPkceClient:
    return OpenBaoPkceClient("https://openbao.example.test", "hvs." + "w" * 43)


def consumer() -> OpenBaoPkceClient:
    return OpenBaoPkceClient("https://openbao.example.test", "hvs." + "c" * 43)


def login_failure_event(admin, attempt_id: UUID):
    return admin.execute(
        "SELECT event_type, actor_user_id, object_kind, facts, reason "
        "FROM control.platform_events WHERE object_id = %s",
        (attempt_id,),
    ).fetchone()


async def finish(
    identity,
    *,
    current_recovery_generation: str,
    recovery_transport: httpx2.AsyncBaseTransport | None = None,
    **arguments,
):
    if recovery_transport is None:

        def handle(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                json={
                    "data": {
                        "data": {"generation": current_recovery_generation},
                        "metadata": {
                            "version": 1,
                            "destroyed": False,
                            "deletion_time": "",
                        },
                    }
                },
            )

        recovery_transport = httpx2.MockTransport(handle)

    return await complete_oidc_login(
        identity,
        recovery_authority=OpenBaoRecoveryAuthority(
            "https://openbao.example.test",
            "hvs." + "r" * 43,
        ),
        recovery_transport=recovery_transport,
        **arguments,
    )


async def start(
    identity,
    configured_registration: OidcClientRegistration,
    entropy: Entropy,
    provider: ProviderServer,
    secret: SecretServer,
    *,
    return_path: str = "/sites?view=overview",
    purpose: str = "login",
):
    values = iter((entropy.state, entropy.nonce, entropy.browser_binding))
    return await initiate_oidc_login(
        identity,
        registration=configured_registration,
        pkce_writer=writer(),
        return_path=return_path,
        purpose=purpose,
        oidc_transport=provider.transport(),
        pkce_transport=secret.transport(),
        opaque_token_factory=lambda: next(values),
        verifier_factory=lambda: entropy.verifier,
        attempt_id_factory=lambda: entropy.attempt_id,
    )


@pytest.mark.anyio
async def test_initiation_persists_hashes_only_after_verifier_is_durable(
    identity, admin, session_context
):
    entropy = Entropy()
    configured_registration = registration(session_context["issuer"])
    provider = ProviderServer(configured_registration)
    secret = SecretServer()
    baseline = admin.execute("SELECT count(*) FROM control.oidc_login_attempts").fetchone()[0]

    initiated = await start(identity, configured_registration, entropy, provider, secret)

    assert initiated.attempt_id == entropy.attempt_id
    assert entropy.verifier not in initiated.authorization_url
    assert entropy.browser_binding not in repr(initiated)
    assert initiated.authorization_url not in repr(initiated)
    query = parse_qs(urlsplit(initiated.authorization_url).query)
    assert query["state"] == [entropy.state]
    assert query["nonce"] == [entropy.nonce]
    assert query["code_challenge_method"] == ["S256"]
    assert secret.calls == [
        (
            "POST",
            "/v1/signal-ephemeral/data/oidc-login/" + str(entropy.attempt_id),
        )
    ]
    assert admin.execute("SELECT count(*) FROM control.oidc_login_attempts").fetchone()[0] == (
        baseline + 1
    )
    stored = admin.execute(
        "SELECT id, state_hash, nonce_hash, browser_binding_hash, pkce_secret_reference, "
        "return_path FROM control.oidc_login_attempts WHERE id = %s",
        (entropy.attempt_id,),
    ).fetchone()
    assert stored == (
        entropy.attempt_id,
        hashlib.sha256(entropy.state.encode("ascii")).digest(),
        hashlib.sha256(entropy.nonce.encode("ascii")).digest(),
        hashlib.sha256(entropy.browser_binding.encode("ascii")).digest(),
        f"secret://oidc-login/{entropy.attempt_id}/1",
        "/sites?view=overview",
    )


@pytest.mark.anyio
async def test_complete_flow_consumes_all_proofs_and_issues_hash_only_session(
    identity, admin, session_context
):
    now = datetime.now(UTC).replace(microsecond=0)
    entropy = Entropy()
    configured_registration = registration(session_context["issuer"])
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    provider = ProviderServer(
        configured_registration,
        nonce=entropy.nonce,
        now=now,
        subject=session_context["subject"],
    )
    raw_session_token = "t" * 43
    observed = []

    completed = await finish(
        identity,
        state=entropy.state,
        browser_binding=entropy.browser_binding,
        code="one-time-code",
        pkce_consumer=consumer(),
        current_recovery_generation=session_context["generation"],
        now=now,
        oidc_transport=provider.transport(),
        pkce_transport=secret.transport(),
        session_token_factory=lambda: raw_session_token,
        verified_assertion_observer=lambda *values: observed.append(values),
    )

    assert completed.return_path == "/sites?view=overview"
    assert completed.identity_session.user_id == session_context["user_id"]
    assert completed.identity_session.token == raw_session_token
    assert len(observed) == 1
    assert observed[0][0].id == entropy.attempt_id
    assert observed[0][2].subject == session_context["subject"]
    assert observed[0][1].id_token
    assert raw_session_token not in repr(completed)
    assert provider.token_form == {
        "grant_type": ["authorization_code"],
        "redirect_uri": [REDIRECT_URI],
        "code": ["one-time-code"],
        "code_verifier": [entropy.verifier],
        "client_id": [CLIENT_ID],
    }
    assert secret.calls[-2:] == [
        (
            "GET",
            "/v1/signal-ephemeral/data/oidc-login/" + str(entropy.attempt_id),
        ),
        (
            "DELETE",
            "/v1/signal-ephemeral/metadata/oidc-login/" + str(entropy.attempt_id),
        ),
    ]
    assert secret.verifier is None
    assert admin.execute(
        "SELECT consumed_at IS NOT NULL FROM control.oidc_login_attempts WHERE id = %s",
        (entropy.attempt_id,),
    ).fetchone()[0]
    stored_session = admin.execute(
        "SELECT token_hash, recovery_generation FROM control.identity_sessions WHERE id = %s",
        (completed.identity_session.id,),
    ).fetchone()
    assert stored_session == (
        hashlib.sha256(raw_session_token.encode("ascii")).digest(),
        session_context["generation"],
    )


@pytest.mark.anyio
async def test_invitation_purpose_issues_proof_without_user_session_or_recovery_read(
    identity, admin, session_context
):
    now = datetime.now(UTC).replace(microsecond=0)
    entropy = Entropy()
    configured_registration = registration(session_context["issuer"])
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
        return_path="/invitations/accept",
        purpose="invitation_acceptance",
    )
    subject = "invitee-" + str(uuid4())
    provider = ProviderServer(
        configured_registration,
        nonce=entropy.nonce,
        now=now,
        subject=subject,
        verified_email="invitee@example.test",
    )
    proof_token = "p" * 43
    users_before = admin.execute("SELECT count(*) FROM control.users").fetchone()[0]
    sessions_before = admin.execute("SELECT count(*) FROM control.identity_sessions").fetchone()[0]
    no_recovery = httpx2.MockTransport(
        lambda _: pytest.fail("Invitation verification must not read recovery authority.")
    )

    completed = await finish(
        identity,
        state=entropy.state,
        browser_binding=entropy.browser_binding,
        code="invitation-code",
        pkce_consumer=consumer(),
        current_recovery_generation="unused",
        recovery_transport=no_recovery,
        now=now,
        oidc_transport=provider.transport(),
        pkce_transport=secret.transport(),
        invitation_proof_token_factory=lambda: proof_token,
    )

    assert isinstance(completed, CompletedInvitationIdentityVerification)
    assert completed.return_path == "/invitations/accept"
    assert completed.identity_proof.token == proof_token
    assert proof_token not in repr(completed)
    assert admin.execute("SELECT count(*) FROM control.users").fetchone()[0] == users_before
    assert admin.execute("SELECT count(*) FROM control.identity_sessions").fetchone()[0] == (
        sessions_before
    )
    assert admin.execute(
        "SELECT token_hash, oidc_subject, verified_email, consumed_at "
        "FROM control.invitation_identity_proofs WHERE id = %s",
        (completed.identity_proof.id,),
    ).fetchone() == (
        hashlib.sha256(proof_token.encode("ascii")).digest(),
        subject,
        "invitee@example.test",
        None,
    )
    assert login_failure_event(admin, entropy.attempt_id) is None


@pytest.mark.anyio
async def test_invitation_purpose_requires_verified_email_and_records_closed_failure(
    identity, admin, session_context
):
    now = datetime.now(UTC).replace(microsecond=0)
    entropy = Entropy()
    configured_registration = registration(session_context["issuer"])
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
        purpose="invitation_acceptance",
    )
    provider = ProviderServer(
        configured_registration,
        nonce=entropy.nonce,
        now=now,
        subject="unverified-" + str(uuid4()),
    )
    no_recovery = httpx2.MockTransport(
        lambda _: pytest.fail("Invitation verification must not read recovery authority.")
    )

    with pytest.raises(LoginFlowError) as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="invitation-code",
            pkce_consumer=consumer(),
            current_recovery_generation="unused",
            recovery_transport=no_recovery,
            now=now,
            oidc_transport=provider.transport(),
            pkce_transport=secret.transport(),
        )

    assert failure.value.code == "LOGIN_CALLBACK_REJECTED"
    assert login_failure_event(admin, entropy.attempt_id)[-1] == (
        "invitation_identity_not_verified"
    )


@pytest.mark.anyio
async def test_invitation_proof_persistence_failure_is_audited_and_redacted(
    monkeypatch, identity, admin, session_context
):
    now = datetime.now(UTC).replace(microsecond=0)
    entropy = Entropy()
    configured_registration = registration(session_context["issuer"])
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
        purpose="invitation_acceptance",
    )
    provider = ProviderServer(
        configured_registration,
        nonce=entropy.nonce,
        now=now,
        subject="invitee-" + str(uuid4()),
        verified_email="invitee@example.test",
    )

    def fail_proof_issuance(*_args, **_kwargs):
        raise RuntimeError("private proof persistence detail")

    monkeypatch.setattr(
        login_flow_module,
        "issue_invitation_identity_proof",
        fail_proof_issuance,
    )
    no_recovery = httpx2.MockTransport(
        lambda _: pytest.fail("Invitation verification must not read recovery authority.")
    )
    with pytest.raises(LoginFlowError) as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="invitation-code",
            pkce_consumer=consumer(),
            current_recovery_generation="unused",
            recovery_transport=no_recovery,
            now=now,
            oidc_transport=provider.transport(),
            pkce_transport=secret.transport(),
        )
    assert failure.value.code == "LOGIN_CALLBACK_PROOF_FAILED"
    assert "private" not in str(failure.value)
    assert login_failure_event(admin, entropy.attempt_id)[-1] == (
        "invitation_proof_persistence_failed"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("opaque_values", "verifier_value"),
    [
        (("x" * 43, "x" * 43, "b" * 43), "v" * 64),
        (("short", "n" * 43, "b" * 43), "v" * 64),
        (("s" * 43, "n" * 43, "b" * 43), "short"),
    ],
)
async def test_bad_entropy_is_rejected_before_network_or_database(
    identity, admin, session_context, opaque_values, verifier_value
):
    configured_registration = registration(session_context["issuer"])
    baseline = admin.execute("SELECT count(*) FROM control.oidc_login_attempts").fetchone()[0]
    values = iter(opaque_values)
    no_network = httpx2.MockTransport(lambda _: pytest.fail("Network must not be called"))
    with pytest.raises(LoginFlowError) as failure:
        await initiate_oidc_login(
            identity,
            registration=configured_registration,
            pkce_writer=writer(),
            return_path="/",
            oidc_transport=no_network,
            pkce_transport=no_network,
            opaque_token_factory=lambda: next(values),
            verifier_factory=lambda: verifier_value,
        )
    assert failure.value.code == "LOGIN_START_ENTROPY_FAILED"
    assert (
        admin.execute("SELECT count(*) FROM control.oidc_login_attempts").fetchone()[0] == baseline
    )


@pytest.mark.anyio
@pytest.mark.parametrize(("return_path", "ttl"), [("https://attacker.test", 300), ("/", 601)])
async def test_invalid_start_policy_is_rejected_before_network(
    identity, session_context, return_path, ttl
):
    configured_registration = registration(session_context["issuer"])
    no_network = httpx2.MockTransport(lambda _: pytest.fail("Network must not be called"))
    with pytest.raises(ValueError):
        await initiate_oidc_login(
            identity,
            registration=configured_registration,
            pkce_writer=writer(),
            return_path=return_path,
            ttl_seconds=ttl,
            oidc_transport=no_network,
            pkce_transport=no_network,
        )


@pytest.mark.anyio
async def test_invalid_start_purpose_is_rejected_before_external_side_effects(
    identity, session_context
):
    configured_registration = registration(session_context["issuer"])
    no_network = httpx2.MockTransport(lambda _: pytest.fail("Network must not be called"))
    with pytest.raises(ValueError):
        await initiate_oidc_login(
            identity,
            registration=configured_registration,
            pkce_writer=writer(),
            return_path="/",
            purpose="password_reset",
            oidc_transport=no_network,
            pkce_transport=no_network,
        )


@pytest.mark.anyio
async def test_provider_or_secret_start_failure_creates_no_attempt(
    identity, admin, session_context
):
    configured_registration = registration(session_context["issuer"])
    baseline = admin.execute("SELECT count(*) FROM control.oidc_login_attempts").fetchone()[0]
    provider = ProviderServer(configured_registration)
    provider.discovery_status = 503
    secret = SecretServer()
    with pytest.raises(LoginFlowError) as provider_failure:
        await start(identity, configured_registration, Entropy(), provider, secret)
    assert provider_failure.value.code == "LOGIN_START_PROVIDER_FAILED"
    assert secret.calls == []

    provider = ProviderServer(configured_registration)
    secret = SecretServer()
    secret.write_status = 500
    with pytest.raises(LoginFlowError) as secret_failure:
        await start(identity, configured_registration, Entropy(), provider, secret)
    assert secret_failure.value.code == "LOGIN_START_SECRET_FAILED"
    assert (
        admin.execute("SELECT count(*) FROM control.oidc_login_attempts").fetchone()[0] == baseline
    )


@pytest.mark.anyio
async def test_state_conflict_leaves_only_a_bounded_unreachable_secret(
    identity, admin, session_context
):
    configured_registration = registration(session_context["issuer"])
    entropy = Entropy()
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    second_entropy = Entropy(state=entropy.state)
    with pytest.raises(LoginFlowError) as failure:
        await start(
            identity,
            configured_registration,
            second_entropy,
            ProviderServer(configured_registration),
            secret,
        )
    assert failure.value.code == "LOGIN_START_PERSISTENCE_FAILED"
    assert (
        admin.execute(
            "SELECT count(*) FROM control.oidc_login_attempts WHERE state_hash = %s",
            (hashlib.sha256(entropy.state.encode("ascii")).digest(),),
        ).fetchone()[0]
        == 1
    )
    assert secret.verifier == second_entropy.verifier


@pytest.mark.anyio
async def test_malformed_callback_and_wrong_binding_do_not_consume_attempt(
    identity, admin, session_context
):
    configured_registration = registration(session_context["issuer"])
    entropy = Entropy()
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    no_network = httpx2.MockTransport(lambda _: pytest.fail("Network must not be called"))
    for code, binding in [("", entropy.browser_binding), ("code", secrets.token_urlsafe(32))]:
        with pytest.raises(LoginFlowError) as failure:
            await finish(
                identity,
                state=entropy.state,
                browser_binding=binding,
                code=code,
                pkce_consumer=consumer(),
                current_recovery_generation=session_context["generation"],
                oidc_transport=no_network,
                pkce_transport=no_network,
            )
        assert failure.value.code == "LOGIN_CALLBACK_REJECTED"
    assert admin.execute(
        "SELECT consumed_at IS NULL FROM control.oidc_login_attempts WHERE id = %s",
        (entropy.attempt_id,),
    ).fetchone()[0]
    assert login_failure_event(admin, entropy.attempt_id) is None


@pytest.mark.anyio
async def test_invalid_recovery_authority_data_does_not_consume_attempt(
    identity, admin, session_context
):
    configured_registration = registration(session_context["issuer"])
    entropy = Entropy()
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    with pytest.raises(LoginFlowError) as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="code",
            pkce_consumer=consumer(),
            current_recovery_generation="",
        )
    assert failure.value.code == "LOGIN_RECOVERY_AUTHORITY_FAILED"
    assert admin.execute(
        "SELECT consumed_at IS NULL FROM control.oidc_login_attempts WHERE id = %s",
        (entropy.attempt_id,),
    ).fetchone()[0]
    assert login_failure_event(admin, entropy.attempt_id) is None


@pytest.mark.anyio
async def test_recovery_authority_outage_does_not_consume_attempt(identity, admin, session_context):
    configured_registration = registration(session_context["issuer"])
    entropy = Entropy()
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )

    def unavailable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("private authority detail", request=request)

    no_provider = httpx2.MockTransport(lambda _: pytest.fail("Provider must not be called"))
    with pytest.raises(LoginFlowError) as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="code",
            pkce_consumer=consumer(),
            current_recovery_generation=session_context["generation"],
            recovery_transport=httpx2.MockTransport(unavailable),
            oidc_transport=no_provider,
            pkce_transport=no_provider,
        )
    assert failure.value.code == "LOGIN_RECOVERY_AUTHORITY_FAILED"
    assert "private" not in str(failure.value)
    assert admin.execute(
        "SELECT consumed_at IS NULL FROM control.oidc_login_attempts WHERE id = %s",
        (entropy.attempt_id,),
    ).fetchone()[0]
    assert login_failure_event(admin, entropy.attempt_id) is None


@pytest.mark.anyio
async def test_provider_failure_burns_attempt_without_reading_secret(
    identity, admin, session_context
):
    configured_registration = registration(session_context["issuer"])
    entropy = Entropy()
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    calls_before = list(secret.calls)
    provider = ProviderServer(configured_registration)
    provider.discovery_status = 503
    with pytest.raises(LoginFlowError) as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="code",
            pkce_consumer=consumer(),
            current_recovery_generation=session_context["generation"],
            oidc_transport=provider.transport(),
            pkce_transport=secret.transport(),
        )
    assert failure.value.code == "LOGIN_CALLBACK_PROVIDER_FAILED"
    assert secret.calls == calls_before
    assert admin.execute(
        "SELECT consumed_at IS NOT NULL FROM control.oidc_login_attempts WHERE id = %s",
        (entropy.attempt_id,),
    ).fetchone()[0]
    assert login_failure_event(admin, entropy.attempt_id) == (
        "identity.login.failed",
        None,
        "oidc_login_attempt",
        {"schema_version": 1},
        "provider_configuration_failed",
    )


@pytest.mark.anyio
async def test_missing_secret_rejects_before_code_exchange(identity, admin, session_context):
    now = datetime.now(UTC).replace(microsecond=0)
    configured_registration = registration(session_context["issuer"])
    entropy = Entropy()
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    secret.missing = True
    provider = ProviderServer(
        configured_registration,
        nonce=entropy.nonce,
        now=now,
        subject=session_context["subject"],
    )
    with pytest.raises(LoginFlowError) as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="code",
            pkce_consumer=consumer(),
            current_recovery_generation=session_context["generation"],
            now=now,
            oidc_transport=provider.transport(),
            pkce_transport=secret.transport(),
        )
    assert failure.value.code == "LOGIN_CALLBACK_REJECTED"
    assert provider.token_form is None
    assert login_failure_event(admin, entropy.attempt_id)[-1] == "pkce_unavailable"


@pytest.mark.anyio
async def test_unconfirmed_secret_deletion_is_audited(identity, admin, session_context):
    now = datetime.now(UTC).replace(microsecond=0)
    configured_registration = registration(session_context["issuer"])
    entropy = Entropy()
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    secret.delete_status = 500
    provider = ProviderServer(
        configured_registration,
        nonce=entropy.nonce,
        now=now,
        subject=session_context["subject"],
    )

    with pytest.raises(LoginFlowError) as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="code",
            pkce_consumer=consumer(),
            current_recovery_generation=session_context["generation"],
            now=now,
            oidc_transport=provider.transport(),
            pkce_transport=secret.transport(),
        )

    assert failure.value.code == "LOGIN_CALLBACK_SECRET_FAILED"
    assert provider.token_form is None
    assert login_failure_event(admin, entropy.attempt_id)[-1] == "pkce_consume_failed"


@pytest.mark.anyio
async def test_invalid_provider_token_consumes_proofs_but_issues_no_session(
    identity, admin, session_context
):
    now = datetime.now(UTC).replace(microsecond=0)
    configured_registration = registration(session_context["issuer"])
    entropy = Entropy()
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    provider = ProviderServer(
        configured_registration,
        nonce=entropy.nonce,
        now=now,
        subject=session_context["subject"],
    )
    provider.invalid_id_token = True
    observed = []
    baseline = admin.execute("SELECT count(*) FROM control.identity_sessions").fetchone()[0]
    with pytest.raises(LoginFlowError) as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="code",
            pkce_consumer=consumer(),
            current_recovery_generation=session_context["generation"],
            now=now,
            oidc_transport=provider.transport(),
            pkce_transport=secret.transport(),
            verified_assertion_observer=lambda *values: observed.append(values),
        )
    assert failure.value.code == "LOGIN_CALLBACK_PROVIDER_FAILED"
    assert observed == []
    assert secret.verifier is None
    assert admin.execute("SELECT count(*) FROM control.identity_sessions").fetchone()[0] == baseline
    assert login_failure_event(admin, entropy.attempt_id)[-1] == "provider_assertion_failed"


@pytest.mark.anyio
async def test_unknown_identity_never_creates_user_or_session(identity, admin, session_context):
    now = datetime.now(UTC).replace(microsecond=0)
    configured_registration = registration(session_context["issuer"])
    entropy = Entropy()
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    provider = ProviderServer(
        configured_registration,
        nonce=entropy.nonce,
        now=now,
        subject="unknown-" + str(uuid4()),
    )
    users_before = admin.execute("SELECT count(*) FROM control.users").fetchone()[0]
    sessions_before = admin.execute("SELECT count(*) FROM control.identity_sessions").fetchone()[0]
    with pytest.raises(LoginFlowError) as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="code",
            pkce_consumer=consumer(),
            current_recovery_generation=session_context["generation"],
            now=now,
            oidc_transport=provider.transport(),
            pkce_transport=secret.transport(),
        )
    assert failure.value.code == "LOGIN_CALLBACK_REJECTED"
    assert admin.execute("SELECT count(*) FROM control.users").fetchone()[0] == users_before
    assert admin.execute("SELECT count(*) FROM control.identity_sessions").fetchone()[0] == (
        sessions_before
    )
    assert login_failure_event(admin, entropy.attempt_id)[-1] == "identity_not_authorized"


@pytest.mark.anyio
async def test_verified_assertion_observer_failure_never_issues_a_session(
    identity,
    admin,
    session_context,
):
    now = datetime.now(UTC).replace(microsecond=0)
    configured_registration = registration(session_context["issuer"])
    entropy, secret = Entropy(), SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    provider = ProviderServer(
        configured_registration,
        nonce=entropy.nonce,
        now=now,
        subject=session_context["subject"],
    )
    baseline = admin.execute("SELECT count(*) FROM control.identity_sessions").fetchone()[0]

    def unavailable(*args):
        raise RuntimeError("fixture private assertion must not enter an error response")

    with pytest.raises(LoginFlowError, match="LOGIN_CALLBACK_REJECTED") as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="code",
            pkce_consumer=consumer(),
            current_recovery_generation=session_context["generation"],
            now=now,
            oidc_transport=provider.transport(),
            pkce_transport=secret.transport(),
            verified_assertion_observer=unavailable,
        )
    assert "private assertion" not in str(failure.value)
    assert admin.execute("SELECT count(*) FROM control.identity_sessions").fetchone()[0] == baseline
    assert login_failure_event(admin, entropy.attempt_id)[-1] == "identity_not_authorized"


@pytest.mark.anyio
async def test_session_persistence_failure_is_audited(
    monkeypatch, identity, admin, session_context
):
    now = datetime.now(UTC).replace(microsecond=0)
    configured_registration = registration(session_context["issuer"])
    entropy = Entropy()
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    provider = ProviderServer(
        configured_registration,
        nonce=entropy.nonce,
        now=now,
        subject=session_context["subject"],
    )

    def fail_session_issuance(*_args, **_kwargs):
        raise RuntimeError("private database detail")

    monkeypatch.setattr(login_flow_module, "issue_identity_session", fail_session_issuance)
    with pytest.raises(LoginFlowError) as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="code",
            pkce_consumer=consumer(),
            current_recovery_generation=session_context["generation"],
            now=now,
            oidc_transport=provider.transport(),
            pkce_transport=secret.transport(),
        )

    assert failure.value.code == "LOGIN_CALLBACK_SESSION_FAILED"
    assert "private" not in str(failure.value)
    assert login_failure_event(admin, entropy.attempt_id)[-1] == "session_persistence_failed"


@pytest.mark.anyio
async def test_required_failure_audit_rejects_callback_when_append_fails(
    monkeypatch, identity, session_context
):
    configured_registration = registration(session_context["issuer"])
    entropy = Entropy()
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    provider = ProviderServer(configured_registration)
    provider.discovery_status = 503

    def fail_audit(*_args, **_kwargs):
        raise OidcLoginAuditError("private audit detail")

    monkeypatch.setattr(login_flow_module, "record_consumed_login_failure", fail_audit)
    with pytest.raises(LoginFlowError) as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="code",
            pkce_consumer=consumer(),
            current_recovery_generation=session_context["generation"],
            oidc_transport=provider.transport(),
            pkce_transport=secret.transport(),
        )

    assert failure.value.code == "LOGIN_CALLBACK_AUDIT_FAILED"
    assert "private" not in str(failure.value)


@pytest.mark.anyio
async def test_successful_callback_replay_stops_before_provider_and_secret_calls(
    identity, admin, session_context
):
    now = datetime.now(UTC).replace(microsecond=0)
    configured_registration = registration(session_context["issuer"])
    entropy = Entropy()
    secret = SecretServer()
    await start(
        identity,
        configured_registration,
        entropy,
        ProviderServer(configured_registration),
        secret,
    )
    provider = ProviderServer(
        configured_registration,
        nonce=entropy.nonce,
        now=now,
        subject=session_context["subject"],
    )
    await finish(
        identity,
        state=entropy.state,
        browser_binding=entropy.browser_binding,
        code="code",
        pkce_consumer=consumer(),
        current_recovery_generation=session_context["generation"],
        now=now,
        oidc_transport=provider.transport(),
        pkce_transport=secret.transport(),
    )
    no_network = httpx2.MockTransport(lambda _: pytest.fail("Replay must not call a provider"))
    with pytest.raises(LoginFlowError) as failure:
        await finish(
            identity,
            state=entropy.state,
            browser_binding=entropy.browser_binding,
            code="code",
            pkce_consumer=consumer(),
            current_recovery_generation=session_context["generation"],
            now=now + timedelta(seconds=1),
            oidc_transport=no_network,
            pkce_transport=no_network,
        )
    assert failure.value.code == "LOGIN_CALLBACK_REJECTED"
    assert login_failure_event(admin, entropy.attempt_id) is None
