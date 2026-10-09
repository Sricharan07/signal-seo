from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY
from uuid import uuid4

import httpx2 as httpx
import pytest
import signal_api.authentication as authentication_module
from signal_api.authentication import (
    BrowserAuthenticationUnavailable,
    ComposedBrowserLogin,
    TenantSelectionDenied,
)
from signal_api.browser_security import (
    IDENTITY_COOKIE_NAME,
    INVITATION_IDENTITY_COOKIE_NAME,
    OIDC_BINDING_COOKIE_NAME,
    SESSION_COOKIE_NAME,
)
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.commands import AcceptedHumanCommand, HumanCommandStatus
from signal_core.crawl_http import PinnedHttpFetcher
from signal_core.identity_memberships import IdentityMembership
from signal_core.invitation_acceptance import AcceptedInvitation
from signal_core.login_flow import (
    CompletedOidcLogin,
    InitiatedOidcLogin,
    LoginFlowError,
)
from signal_core.oidc_login import OidcClientRegistration
from signal_core.origin_verification import (
    OriginProofObservation,
    PreparedOriginVerification,
    VerifiedOrigin,
)
from signal_core.pkce_secrets import OpenBaoPkceClient
from signal_core.recovery_authority import (
    OpenBaoRecoveryAuthority,
    RecoveryAuthorityError,
    RecoveryGeneration,
)
from signal_core.session_issuance import IssuedIdentitySession, IssuedTenantSession
from signal_core.session_management import (
    CurrentTenantSession,
    SelectedSiteContext,
    TenantSiteDirectory,
)

STATE = "s" * 43
BINDING = "b" * 43
IDENTITY_TOKEN = "i" * 43
CODE = "authorization-code"
AUTHORIZATION_URL = "https://identity.example.test/realms/signal/protocol/openid-connect/auth"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def completed_login(
    *,
    return_path: str = "/workspace",
    expires_at: datetime | None = None,
    token: str = IDENTITY_TOKEN,
) -> CompletedOidcLogin:
    return CompletedOidcLogin(
        identity_session=IssuedIdentitySession(
            id=uuid4(),
            audit_event_id=uuid4(),
            user_id=uuid4(),
            token=token,
            authentication_level="primary",
            expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=30),
        ),
        return_path=return_path,
    )


@dataclass
class StubLoginGateway:
    attempt_ttl_seconds: int = 300
    start_error: LoginFlowError | None = None
    complete_error: LoginFlowError | None = None
    completion: CompletedOidcLogin = field(default_factory=completed_login)
    initiated_inputs: list[tuple[str, str]] = field(default_factory=list)
    completed_inputs: list[tuple[str, str, str]] = field(default_factory=list)

    async def initiate(self, *, return_path: str, purpose: str = "login") -> InitiatedOidcLogin:
        self.initiated_inputs.append((return_path, purpose))
        if self.start_error is not None:
            raise self.start_error
        return InitiatedOidcLogin(
            attempt_id=uuid4(),
            authorization_url=AUTHORIZATION_URL,
            browser_binding=BINDING,
        )

    async def complete(self, *, state: str, browser_binding: str, code: str) -> CompletedOidcLogin:
        self.completed_inputs.append((state, browser_binding, code))
        if self.complete_error is not None:
            raise self.complete_error
        return self.completion


def client_for(gateway: StubLoginGateway | None) -> httpx.AsyncClient:
    application = create_app(
        settings=ApiSettings(environment="test"),
        browser_login=gateway,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="https://signal.example.test",
        follow_redirects=False,
    )


@pytest.mark.anyio
async def test_unconfigured_login_routes_fail_closed():
    async with client_for(None) as api:
        start = await api.get("/v1/session/login")
        callback = await api.get(f"/v1/session/callback?state={STATE}&code={CODE}")
    for response in (start, callback):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.anyio
async def test_login_start_redirects_only_after_setting_bounded_browser_binding():
    gateway = StubLoginGateway()
    async with client_for(gateway) as api:
        response = await api.get("/v1/session/login?return_path=%2Fprojects%3Fview%3Dactive")
    assert response.status_code == 303
    assert response.headers["location"] == AUTHORIZATION_URL
    assert gateway.initiated_inputs == [("/projects?view=active", "login")]
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 4
    cookie = next(
        value for value in cookies if value.startswith(f"{OIDC_BINDING_COOKIE_NAME}={BINDING};")
    )
    assert cookie.startswith(f"{OIDC_BINDING_COOKIE_NAME}={BINDING};")
    assert "HttpOnly" in cookie
    assert "Max-Age=300" in cookie
    assert "Path=/" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" in cookie
    assert "Domain=" not in cookie
    assert any(
        value.startswith(f'{IDENTITY_COOKIE_NAME}="";') and "Max-Age=0" in value
        for value in cookies
    )
    assert any(
        value.startswith(f'{SESSION_COOKIE_NAME}="";') and "Max-Age=0" in value for value in cookies
    )
    assert any(
        value.startswith(f'{INVITATION_IDENTITY_COOKIE_NAME}="";') and "Max-Age=0" in value
        for value in cookies
    )
    assert BINDING not in response.text
    assert BINDING not in response.headers["location"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query",
    [
        "?return_path=https%3A%2F%2Fattacker.example",
        "?return_path=%2Fone&return_path=%2Ftwo",
        "?return_path=%2F%2Fattacker.example",
    ],
)
async def test_login_start_rejects_external_or_ambiguous_return_paths(query: str):
    gateway = StubLoginGateway()
    async with client_for(gateway) as api:
        response = await api.get(f"/v1/session/login{query}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_LOGIN_REQUEST"
    assert gateway.initiated_inputs == []
    assert "attacker" not in response.text


@pytest.mark.anyio
async def test_login_start_failure_is_stable_and_contains_no_internal_code():
    gateway = StubLoginGateway(start_error=LoginFlowError("LOGIN_START_SECRET_FAILED"))
    async with client_for(gateway) as api:
        response = await api.get("/v1/session/login")
    assert response.status_code == 503
    assert response.json()["error"] | {"correlation_id": "ignored"} == {
        "code": "LOGIN_START_FAILED",
        "message": "Login could not be started.",
        "retryable": True,
        "correlation_id": "ignored",
    }
    assert "SECRET" not in response.text
    assert "set-cookie" not in response.headers


@pytest.mark.anyio
async def test_callback_uses_exact_browser_proofs_and_sets_only_identity_session():
    gateway = StubLoginGateway()
    async with client_for(gateway) as api:
        await api.get("/v1/session/login")
        response = await api.get(f"/v1/session/callback?state={STATE}&code={CODE}")
    assert response.status_code == 303
    assert response.headers["location"] == "/workspace"
    assert gateway.completed_inputs == [(STATE, BINDING, CODE)]
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 4
    assert any(cookie.startswith(f'{OIDC_BINDING_COOKIE_NAME}="";') for cookie in cookies)
    assert any(cookie.startswith(f'{SESSION_COOKIE_NAME}="";') for cookie in cookies)
    assert any(cookie.startswith(f'{INVITATION_IDENTITY_COOKIE_NAME}="";') for cookie in cookies)
    identity_cookie = next(
        cookie for cookie in cookies if cookie.startswith(f"{IDENTITY_COOKIE_NAME}=")
    )
    assert identity_cookie.startswith(f"{IDENTITY_COOKIE_NAME}={IDENTITY_TOKEN};")
    assert "HttpOnly" in identity_cookie
    assert "Secure" in identity_cookie
    assert "SameSite=lax" in identity_cookie
    assert "Path=/" in identity_cookie
    assert "Domain=" not in identity_cookie
    assert IDENTITY_TOKEN not in response.headers["location"]
    assert IDENTITY_TOKEN not in response.text


@pytest.mark.anyio
async def test_callback_query_credentials_are_removed_from_the_asgi_access_log_scope():
    gateway = StubLoginGateway()
    application = create_app(
        settings=ApiSettings(environment="test"),
        browser_login=gateway,
    )
    observed = {}

    async def inspect_after_response(scope, receive, send):
        await application(scope, receive, send)
        observed["query_string"] = scope["query_string"]

    headers = {"Cookie": f"{OIDC_BINDING_COOKIE_NAME}={BINDING}"}
    transport = httpx.ASGITransport(app=inspect_after_response)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://signal.example.test",
        follow_redirects=False,
    ) as api:
        response = await api.get(f"/v1/session/callback?state={STATE}&code={CODE}", headers=headers)
    assert response.status_code == 303
    assert gateway.completed_inputs == [(STATE, BINDING, CODE)]
    assert observed["query_string"] == b""


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["state", "code", "cookie", "missing_cookie"])
async def test_callback_rejects_missing_or_ambiguous_proofs_before_gateway(mutation: str):
    gateway = StubLoginGateway()
    query = f"state={STATE}&code={CODE}"
    headers: list[tuple[str, str]] = [("Cookie", f"{OIDC_BINDING_COOKIE_NAME}={BINDING}")]
    if mutation == "state":
        query += f"&state={'t' * 43}"
    elif mutation == "code":
        query += "&code=second-code"
    elif mutation == "cookie":
        headers.append(("Cookie", f"{OIDC_BINDING_COOKIE_NAME}={'c' * 43}"))
    else:
        headers = []
    async with client_for(gateway) as api:
        response = await api.get(f"/v1/session/callback?{query}", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "LOGIN_CALLBACK_REJECTED"
    assert gateway.completed_inputs == []
    assert any("Max-Age=0" in value for value in response.headers.get_list("set-cookie"))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("flow_code", "status", "retryable", "clears_binding"),
    [
        ("LOGIN_RECOVERY_AUTHORITY_FAILED", 503, True, False),
        ("LOGIN_CALLBACK_PROVIDER_FAILED", 503, False, True),
        ("LOGIN_CALLBACK_REJECTED", 401, False, True),
    ],
)
async def test_callback_maps_retryability_without_exposing_pipeline_stage(
    flow_code: str, status: int, retryable: bool, clears_binding: bool
):
    gateway = StubLoginGateway(complete_error=LoginFlowError(flow_code))
    headers = {"Cookie": f"{OIDC_BINDING_COOKIE_NAME}={BINDING}"}
    async with client_for(gateway) as api:
        response = await api.get(f"/v1/session/callback?state={STATE}&code={CODE}", headers=headers)
    assert response.status_code == status
    body = response.json()["error"]
    assert body["retryable"] is retryable
    if flow_code != "LOGIN_CALLBACK_REJECTED":
        assert flow_code not in response.text
    cleared = any("Max-Age=0" in value for value in response.headers.get_list("set-cookie"))
    assert cleared is clears_binding


@pytest.mark.anyio
@pytest.mark.parametrize(
    "completion",
    [
        completed_login(return_path="https://attacker.example"),
        completed_login(expires_at=datetime.now(UTC) - timedelta(seconds=1)),
        completed_login(token="malformed-session"),
    ],
)
async def test_callback_revalidates_service_output_before_issuing_cookie(
    completion: CompletedOidcLogin,
):
    gateway = StubLoginGateway(completion=completion)
    headers = {"Cookie": f"{OIDC_BINDING_COOKIE_NAME}={BINDING}"}
    async with client_for(gateway) as api:
        response = await api.get(f"/v1/session/callback?state={STATE}&code={CODE}", headers=headers)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "LOGIN_CALLBACK_FAILED"
    assert IDENTITY_TOKEN not in "\n".join(response.headers.get_list("set-cookie"))
    assert "attacker" not in response.text


class FakeConnection:
    autocommit = True

    def __init__(self, *, close_error: bool = False) -> None:
        self.closed = False
        self.close_error = close_error

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise RuntimeError("connection-close-detail")


def composed_gateway(connection_factory) -> ComposedBrowserLogin:
    return ComposedBrowserLogin(
        connection_factory=connection_factory,
        registration=OidcClientRegistration(
            issuer="https://identity.example.test/realms/signal",
            client_id="signal-web",
            redirect_uri="https://signal.example.test/v1/session/callback",
        ),
        pkce_writer=OpenBaoPkceClient(
            base_url="https://secrets.example.test", token="writer-token-value-for-tests"
        ),
        pkce_consumer=OpenBaoPkceClient(
            base_url="https://secrets.example.test", token="consumer-token-value-for-tests"
        ),
        recovery_authority=OpenBaoRecoveryAuthority(
            base_url="https://secrets.example.test", token="recovery-token-value-for-tests"
        ),
    )


@pytest.mark.anyio
async def test_composed_gateway_uses_and_closes_one_clean_connection(
    monkeypatch: pytest.MonkeyPatch,
):
    connection = FakeConnection()
    captured = {}

    async def fake_initiate(supplied_connection, **arguments):
        captured["connection"] = supplied_connection
        captured.update(arguments)
        return InitiatedOidcLogin(
            attempt_id=uuid4(),
            authorization_url=AUTHORIZATION_URL,
            browser_binding=BINDING,
        )

    monkeypatch.setattr(authentication_module, "initiate_oidc_login", fake_initiate)
    gateway = composed_gateway(lambda: connection)
    result = await gateway.initiate(return_path="/workspace", purpose="invitation_acceptance")
    assert result.browser_binding == BINDING
    assert captured["connection"] is connection
    assert captured["return_path"] == "/workspace"
    assert captured["purpose"] == "invitation_acceptance"
    assert captured["ttl_seconds"] == 300
    assert connection.closed is True
    assert "token-value-for-tests" not in repr(gateway)
    assert "connection_factory" not in repr(gateway)


@pytest.mark.anyio
async def test_composed_gateway_rejects_connection_factory_failure_without_detail():
    def unavailable():
        raise RuntimeError("database-password-and-host")

    gateway = composed_gateway(unavailable)
    with pytest.raises(BrowserAuthenticationUnavailable) as error:
        await gateway.initiate(return_path="/")
    assert "database-password" not in str(error.value)


@pytest.mark.anyio
async def test_connection_close_failure_does_not_replace_a_terminal_flow_error(
    monkeypatch: pytest.MonkeyPatch,
):
    connection = FakeConnection(close_error=True)

    async def rejected(*args, **kwargs):
        del args, kwargs
        raise LoginFlowError("LOGIN_START_PROVIDER_FAILED")

    monkeypatch.setattr(authentication_module, "initiate_oidc_login", rejected)
    gateway = composed_gateway(lambda: connection)
    with pytest.raises(LoginFlowError) as error:
        await gateway.initiate(return_path="/")
    assert error.value.code == "LOGIN_START_PROVIDER_FAILED"
    assert connection.closed is True


@pytest.mark.anyio
async def test_composed_gateway_lists_and_selects_with_one_generation_per_operation(
    monkeypatch: pytest.MonkeyPatch,
):
    tenant_id = uuid4()
    connections = []
    calls = []

    def connection_factory():
        connection = FakeConnection()
        connections.append(connection)
        return connection

    async def current_generation(self, **arguments):
        del self
        calls.append(("generation", arguments))
        return RecoveryGeneration(value="generation-web-account-1", version=1)

    def list_memberships(connection, **arguments):
        calls.append(("memberships", connection, arguments))
        return (IdentityMembership(tenant_id=tenant_id, tenant_name="Acme", role_key="editor"),)

    def issue_tenant(connection, **arguments):
        calls.append(("selection", connection, arguments))
        return IssuedTenantSession(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=uuid4(),
            token="t" * 43,
            role_key="editor",
            authentication_level="primary",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )

    monkeypatch.setattr(OpenBaoRecoveryAuthority, "current_generation", current_generation)
    monkeypatch.setattr(authentication_module, "list_identity_memberships", list_memberships)
    monkeypatch.setattr(authentication_module, "issue_tenant_session", issue_tenant)
    gateway = composed_gateway(connection_factory)

    memberships = await gateway.memberships(identity_session_token=IDENTITY_TOKEN)
    selected = await gateway.select_tenant(
        identity_session_token=IDENTITY_TOKEN,
        tenant_id=tenant_id,
    )
    assert memberships[0].tenant_id == tenant_id
    assert selected.tenant_id == tenant_id
    assert [call[0] for call in calls] == [
        "generation",
        "memberships",
        "generation",
        "memberships",
        "selection",
    ]
    assert all(connection.closed for connection in connections)
    assert calls[-1][2]["requested_tenant_id"] == tenant_id
    assert calls[-1][2]["current_recovery_generation"] == "generation-web-account-1"


@pytest.mark.anyio
async def test_composed_gateway_denies_unlisted_tenant_before_session_issuance(
    monkeypatch: pytest.MonkeyPatch,
):
    connection = FakeConnection()

    async def current_generation(self, **arguments):
        del self, arguments
        return RecoveryGeneration(value="generation-web-account-1", version=1)

    def list_memberships(*args, **kwargs):
        del args, kwargs
        return ()

    def forbidden_issue(*args, **kwargs):
        del args, kwargs
        pytest.fail("Tenant issuance must not run for an unlisted tenant.")

    monkeypatch.setattr(OpenBaoRecoveryAuthority, "current_generation", current_generation)
    monkeypatch.setattr(authentication_module, "list_identity_memberships", list_memberships)
    monkeypatch.setattr(authentication_module, "issue_tenant_session", forbidden_issue)
    gateway = composed_gateway(lambda: connection)
    with pytest.raises(TenantSelectionDenied):
        await gateway.select_tenant(
            identity_session_token=IDENTITY_TOKEN,
            tenant_id=uuid4(),
        )
    assert connection.closed is True


@pytest.mark.anyio
async def test_composed_gateway_rejects_non_uuid_tenant_before_external_dependencies(
    monkeypatch: pytest.MonkeyPatch,
):
    touched = False

    async def current_generation(self, **arguments):
        del self, arguments
        nonlocal touched
        touched = True
        raise AssertionError("Recovery authority must not receive malformed tenant input.")

    monkeypatch.setattr(OpenBaoRecoveryAuthority, "current_generation", current_generation)
    gateway = composed_gateway(lambda: pytest.fail("Database must not be opened."))
    with pytest.raises(TenantSelectionDenied):
        await gateway.select_tenant(
            identity_session_token=IDENTITY_TOKEN,
            tenant_id="not-a-uuid",
        )
    assert touched is False


@pytest.mark.anyio
async def test_recovery_authority_failure_precedes_database_connection(
    monkeypatch: pytest.MonkeyPatch,
):
    opened = False

    async def unavailable(self, **arguments):
        del self, arguments
        raise RecoveryAuthorityError("RECOVERY_AUTHORITY_UNAVAILABLE")

    def connection_factory():
        nonlocal opened
        opened = True
        return FakeConnection()

    monkeypatch.setattr(OpenBaoRecoveryAuthority, "current_generation", unavailable)
    gateway = composed_gateway(connection_factory)
    with pytest.raises(BrowserAuthenticationUnavailable):
        await gateway.memberships(identity_session_token=IDENTITY_TOKEN)
    assert opened is False


@pytest.mark.anyio
async def test_composed_gateway_inspects_current_session_with_external_generation(
    monkeypatch: pytest.MonkeyPatch,
):
    connection = FakeConnection()
    tenant_id = uuid4()
    expected = CurrentTenantSession(
        tenant_id=tenant_id,
        user_id=uuid4(),
        role_key="analyst",
        authentication_level="primary",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        session_version=1,
        active_site_id=None,
    )
    calls = []

    async def current_generation(self, **arguments):
        del self, arguments
        calls.append("generation")
        return RecoveryGeneration(value="generation-web-account-1", version=1)

    def inspect(supplied_connection, **arguments):
        calls.append(("inspect", supplied_connection, arguments))
        return expected

    monkeypatch.setattr(OpenBaoRecoveryAuthority, "current_generation", current_generation)
    monkeypatch.setattr(authentication_module, "inspect_tenant_session", inspect)
    gateway = composed_gateway(lambda: connection)
    result = await gateway.current_session(session_token="t" * 43)
    assert result == expected
    assert calls == [
        "generation",
        (
            "inspect",
            connection,
            {
                "session_token": "t" * 43,
                "current_recovery_generation": "generation-web-account-1",
            },
        ),
    ]
    assert connection.closed is True


@pytest.mark.anyio
async def test_composed_gateway_selects_site_with_external_generation(
    monkeypatch: pytest.MonkeyPatch,
):
    connection = FakeConnection()
    site_id = uuid4()
    expected = SelectedSiteContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        site_id=site_id,
        session_version=2,
        changed=True,
    )
    calls = []

    async def current_generation(self, **arguments):
        del self, arguments
        calls.append("generation")
        return RecoveryGeneration(value="generation-web-account-1", version=1)

    def select(supplied_connection, **arguments):
        calls.append(("select", supplied_connection, arguments))
        return expected

    monkeypatch.setattr(OpenBaoRecoveryAuthority, "current_generation", current_generation)
    monkeypatch.setattr(authentication_module, "select_session_site", select)
    gateway = composed_gateway(lambda: connection)
    result = await gateway.select_site(
        session_token="t" * 43,
        site_id=site_id,
        expected_session_version=1,
    )
    assert result == expected
    assert calls == [
        "generation",
        (
            "select",
            connection,
            {
                "session_token": "t" * 43,
                "current_recovery_generation": "generation-web-account-1",
                "requested_site_id": site_id,
                "expected_session_version": 1,
            },
        ),
    ]
    assert connection.closed is True


@pytest.mark.anyio
async def test_composed_gateway_lists_sites_with_external_generation(
    monkeypatch: pytest.MonkeyPatch,
):
    connection = FakeConnection()
    expected = TenantSiteDirectory(tenant_id=uuid4(), tenant_name="Acme", sites=())
    calls = []

    async def current_generation(self, **arguments):
        del self, arguments
        calls.append("generation")
        return RecoveryGeneration(value="generation-web-account-1", version=1)

    def list_sites(supplied_connection, **arguments):
        calls.append(("sites", supplied_connection, arguments))
        return expected

    monkeypatch.setattr(OpenBaoRecoveryAuthority, "current_generation", current_generation)
    monkeypatch.setattr(authentication_module, "list_tenant_sites", list_sites)
    gateway = composed_gateway(lambda: connection)
    result = await gateway.site_directory(session_token="t" * 43)
    assert result == expected
    assert calls == [
        "generation",
        (
            "sites",
            connection,
            {
                "session_token": "t" * 43,
                "current_recovery_generation": "generation-web-account-1",
            },
        ),
    ]
    assert connection.closed is True


@pytest.mark.anyio
async def test_composed_gateway_verifies_origin_between_two_closed_transactions(
    monkeypatch: pytest.MonkeyPatch,
):
    connections = [FakeConnection(), FakeConnection()]
    all_connections = tuple(connections)
    site_id = uuid4()
    challenge_id = uuid4()
    request_id = uuid4()
    now = datetime.now(UTC)
    prepared = PreparedOriginVerification(
        tenant_id=uuid4(),
        user_id=uuid4(),
        site_id=site_id,
        challenge_id=challenge_id,
        origin="https://www.example.com",
        proof_url=("https://www.example.com/.well-known/signal-site-verification.txt"),
        proof_content=f"signal-site-verification={challenge_id}\n",
        proof_sha256=b"p" * 32,
        expires_at=now + timedelta(minutes=30),
    )
    observation = OriginProofObservation(outcome="transport_unavailable")
    expected = VerifiedOrigin(
        tenant_id=prepared.tenant_id,
        user_id=prepared.user_id,
        site_id=site_id,
        challenge_id=challenge_id,
        origin=prepared.origin,
        proof_method="http_well_known",
        verified_at=now,
        recheck_at=now + timedelta(days=30),
        replayed=False,
    )
    calls = []

    async def current_generation(self, **arguments):
        del self, arguments
        calls.append("generation")
        return RecoveryGeneration(value="generation-origin-1", version=1)

    def prepare_verification(connection, **arguments):
        calls.append(("prepare", connection, arguments))
        return prepared

    def observe(fetcher, proof):
        assert all_connections[0].closed is True
        assert all_connections[1].closed is False
        calls.append(("observe", fetcher, proof))
        return observation

    def record_verification(connection, **arguments):
        assert all_connections[0].closed is True
        calls.append(("record", connection, arguments))
        return expected

    monkeypatch.setattr(OpenBaoRecoveryAuthority, "current_generation", current_generation)
    monkeypatch.setattr(
        authentication_module,
        "prepare_origin_verification",
        prepare_verification,
    )
    monkeypatch.setattr(authentication_module, "observe_origin_proof", observe)
    monkeypatch.setattr(
        authentication_module,
        "record_origin_verification",
        record_verification,
    )
    boundary = PinnedHttpFetcher(lambda host, port, timeout: ["93.184.216.34"])
    gateway = replace(
        composed_gateway(lambda: connections.pop(0)),
        origin_fetcher=boundary,
    )

    result = await gateway.verify_origin(
        session_token="t" * 43,
        site_id=site_id,
        challenge_id=challenge_id,
        origin=prepared.origin,
        idempotency_key=request_id,
    )

    assert result == expected
    assert [item if isinstance(item, str) else item[0] for item in calls] == [
        "generation",
        "prepare",
        "observe",
        "record",
    ]
    assert calls[1][2]["current_recovery_generation"] == "generation-origin-1"
    assert calls[3][2]["observation"] is observation
    assert all(connection.closed for connection in all_connections)


@pytest.mark.anyio
async def test_origin_verification_requires_a_configured_pinned_fetcher():
    gateway = composed_gateway(lambda: pytest.fail("Database must not be opened."))
    with pytest.raises(BrowserAuthenticationUnavailable):
        await gateway.verify_origin(
            session_token="t" * 43,
            site_id=uuid4(),
            challenge_id=uuid4(),
            origin="https://www.example.com",
            idempotency_key=uuid4(),
        )


@pytest.mark.anyio
async def test_composed_gateway_logout_does_not_depend_on_recovery_authority(
    monkeypatch: pytest.MonkeyPatch,
):
    connection = FakeConnection()
    captured = {}

    async def forbidden_generation(*args, **kwargs):
        del args, kwargs
        pytest.fail("Logout must remain available during recovery-authority outages.")

    def revoke(supplied_connection, **arguments):
        captured["connection"] = supplied_connection
        captured.update(arguments)
        return True

    monkeypatch.setattr(OpenBaoRecoveryAuthority, "current_generation", forbidden_generation)
    monkeypatch.setattr(authentication_module, "revoke_browser_session", revoke)
    gateway = composed_gateway(lambda: connection)
    result = await gateway.logout(
        session_token="t" * 43,
        presented_session_kind="tenant",
    )
    assert result is True
    assert captured == {
        "connection": connection,
        "session_token": "t" * 43,
        "presented_session_kind": "tenant",
    }
    assert connection.closed is True


@pytest.mark.anyio
async def test_composed_gateway_accepts_invitation_without_recovery_authority(
    monkeypatch: pytest.MonkeyPatch,
):
    connection = FakeConnection()
    invitation_id = uuid4()
    expected = AcceptedInvitation(
        invitation_id=invitation_id,
        user_id=uuid4(),
        membership_id=uuid4(),
        site_membership_id=uuid4(),
        audit_event_id=uuid4(),
        tenant_id=uuid4(),
        site_id=uuid4(),
        role_key="editor",
        accepted_at=datetime.now(UTC),
    )
    captured = {}

    async def forbidden_generation(*args, **kwargs):
        del args, kwargs
        pytest.fail("Invitation acceptance must not read recovery authority.")

    def accept(supplied_connection, **arguments):
        captured["connection"] = supplied_connection
        captured.update(arguments)
        return expected

    monkeypatch.setattr(OpenBaoRecoveryAuthority, "current_generation", forbidden_generation)
    monkeypatch.setattr(authentication_module, "accept_site_invitation", accept)
    gateway = composed_gateway(lambda: connection)
    result = await gateway.accept_invitation(
        identity_proof_token="p" * 43,
        invitation_id=invitation_id,
        token="v" * 43,
        display_name="Invitee User",
    )
    assert result == expected
    assert captured == {
        "connection": connection,
        "identity_proof_token": "p" * 43,
        "invitation_id": invitation_id,
        "token": "v" * 43,
        "display_name": "Invitee User",
    }
    assert connection.closed is True


@pytest.mark.anyio
async def test_composed_gateway_binds_snapshot_commands_to_current_recovery_authority(
    monkeypatch: pytest.MonkeyPatch,
):
    connections = [FakeConnection(), FakeConnection()]
    site_id = uuid4()
    command_id = uuid4()
    actor_user_id = uuid4()
    accepted_at = datetime.now(UTC).replace(microsecond=0)
    expected_acceptance = AcceptedHumanCommand(
        id=command_id,
        reused=False,
        status="accepted",
        accepted_at=accepted_at,
    )
    expected_status = HumanCommandStatus(
        id=command_id,
        actor_user_id=actor_user_id,
        kind="site.snapshot",
        status="accepted",
        accepted_at=accepted_at,
    )
    calls = []

    async def current_generation(self, **arguments):
        del self, arguments
        calls.append("generation")
        return RecoveryGeneration(value="generation-human-command-1", version=1)

    def accept(supplied_connection, **arguments):
        calls.append(("accept", supplied_connection, arguments))
        return expected_acceptance

    def read_status(supplied_connection, **arguments):
        calls.append(("status", supplied_connection, arguments))
        return expected_status

    monkeypatch.setattr(OpenBaoRecoveryAuthority, "current_generation", current_generation)
    monkeypatch.setattr(authentication_module, "accept_authenticated_snapshot", accept)
    monkeypatch.setattr(
        authentication_module,
        "read_authenticated_snapshot_command",
        read_status,
    )
    gateway = composed_gateway(lambda: connections.pop(0))

    acceptance = await gateway.accept_snapshot_command(
        session_token="t" * 43,
        site_id=site_id,
        idempotency_key="snapshot-request-1",
    )
    status = await gateway.snapshot_command_status(
        session_token="t" * 43,
        site_id=site_id,
        command_id=command_id,
    )

    assert acceptance == expected_acceptance
    assert status == expected_status
    assert calls == [
        "generation",
        (
            "accept",
            ANY,
            {
                "session_token": "t" * 43,
                "requested_site_id": site_id,
                "current_recovery_generation": "generation-human-command-1",
                "idempotency_key": "snapshot-request-1",
            },
        ),
        "generation",
        (
            "status",
            ANY,
            {
                "session_token": "t" * 43,
                "requested_site_id": site_id,
                "current_recovery_generation": "generation-human-command-1",
                "command_id": command_id,
            },
        ),
    ]


@pytest.mark.anyio
async def test_snapshot_command_recovery_failure_precedes_database_connection(
    monkeypatch: pytest.MonkeyPatch,
):
    opened = False

    async def unavailable(self, **arguments):
        del self, arguments
        raise RecoveryAuthorityError("RECOVERY_AUTHORITY_UNAVAILABLE")

    def connection_factory():
        nonlocal opened
        opened = True
        return FakeConnection()

    monkeypatch.setattr(OpenBaoRecoveryAuthority, "current_generation", unavailable)
    gateway = composed_gateway(connection_factory)
    with pytest.raises(BrowserAuthenticationUnavailable):
        await gateway.accept_snapshot_command(
            session_token="t" * 43,
            site_id=uuid4(),
            idempotency_key="snapshot-request-1",
        )
    assert opened is False
