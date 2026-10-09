import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import (
    CSRF_HEADER_NAME,
    IDENTITY_COOKIE_NAME,
    INVITATION_IDENTITY_COOKIE_NAME,
    OIDC_BINDING_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    BrowserSecurity,
)
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.invitation_acceptance import AcceptedInvitation, InvitationAcceptanceDenied
from signal_core.invitation_identity_proofs import IssuedInvitationIdentityProof
from signal_core.login_flow import (
    CompletedInvitationIdentityVerification,
    CompletedOidcLogin,
    InitiatedOidcLogin,
)

STATE = "s" * 43
BINDING = "b" * 43
IDENTITY_PROOF = "p" * 43
INVITATION_TOKEN = "v" * 43
IDENTITY_TOKEN = "i" * 43
TENANT_TOKEN = "t" * 43
ORIGIN = "https://dashboard.example.test"
AUTHORIZATION_URL = "https://identity.example.test/realms/signal/protocol/openid-connect/auth"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def browser_security() -> BrowserSecurity:
    return BrowserSecurity(
        csrf_hmac_key=b"invitation-browser-security-key!!",
        allowed_origins=frozenset({ORIGIN}),
    )


def invitation_completion(**overrides) -> CompletedInvitationIdentityVerification:
    values = {
        "identity_proof": IssuedInvitationIdentityProof(
            id=uuid4(),
            token=IDENTITY_PROOF,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ),
        "return_path": "/invitations/accept",
        **overrides,
    }
    return CompletedInvitationIdentityVerification(**values)


def accepted_invitation(requested_invitation_id: UUID, **overrides) -> AcceptedInvitation:
    values = {
        "invitation_id": requested_invitation_id,
        "user_id": uuid4(),
        "membership_id": uuid4(),
        "site_membership_id": uuid4(),
        "audit_event_id": uuid4(),
        "tenant_id": uuid4(),
        "site_id": uuid4(),
        "role_key": "editor",
        "accepted_at": datetime.now(UTC).replace(microsecond=0),
        **overrides,
    }
    return AcceptedInvitation(**values)


@dataclass
class StubInvitationGateway:
    attempt_ttl_seconds: int = 300
    completion: object = field(default_factory=invitation_completion)
    acceptance: object | None = None
    acceptance_error: Exception | None = None
    initiated_inputs: list[tuple[str, str]] = field(default_factory=list)
    completed_inputs: list[tuple[str, str, str]] = field(default_factory=list)
    acceptance_inputs: list[tuple[str, UUID, str, str]] = field(default_factory=list)

    async def initiate(self, *, return_path: str, purpose: str = "login") -> InitiatedOidcLogin:
        self.initiated_inputs.append((return_path, purpose))
        return InitiatedOidcLogin(
            attempt_id=uuid4(),
            authorization_url=AUTHORIZATION_URL,
            browser_binding=BINDING,
        )

    async def complete(
        self, *, state: str, browser_binding: str, code: str
    ) -> CompletedOidcLogin | CompletedInvitationIdentityVerification:
        self.completed_inputs.append((state, browser_binding, code))
        return self.completion

    async def accept_invitation(
        self,
        *,
        identity_proof_token: str,
        invitation_id: UUID,
        token: str,
        display_name: str,
    ) -> AcceptedInvitation:
        self.acceptance_inputs.append((identity_proof_token, invitation_id, token, display_name))
        if self.acceptance_error is not None:
            raise self.acceptance_error
        return self.acceptance or accepted_invitation(invitation_id)


def client_for(
    gateway: object | None,
    security: BrowserSecurity | None,
) -> httpx.AsyncClient:
    application = create_app(
        settings=ApiSettings(environment="test"),
        browser_login=gateway,
        browser_security=security,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="https://signal.example.test",
        follow_redirects=False,
    )


def acceptance_headers(security: BrowserSecurity) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Cookie": f"{INVITATION_IDENTITY_COOKIE_NAME}={IDENTITY_PROOF}",
        CSRF_HEADER_NAME: security.issue_csrf_token(IDENTITY_PROOF),
        "Content-Type": "application/json",
    }


def acceptance_body(invitation_id: UUID) -> dict[str, str]:
    return {
        "invitation_id": str(invitation_id),
        "token": INVITATION_TOKEN,
        "display_name": "Invitee User",
    }


@pytest.mark.anyio
async def test_invitation_routes_fail_closed_without_required_composition(
    browser_security: BrowserSecurity,
):
    invitation_id = uuid4()
    async with client_for(None, browser_security) as api:
        start = await api.get("/v1/invitations/verify")
        accept = await api.post(
            "/v1/invitations/accept",
            headers=acceptance_headers(browser_security),
            json=acceptance_body(invitation_id),
        )
    async with client_for(None, None) as api:
        csrf = await api.get(
            "/v1/invitations/csrf",
            headers={"Cookie": f"{INVITATION_IDENTITY_COOKIE_NAME}={IDENTITY_PROOF}"},
        )
    assert start.status_code == 503
    assert accept.status_code == 503
    assert csrf.status_code == 503
    assert start.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"
    assert accept.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"
    assert csrf.json()["error"]["code"] == "BROWSER_SECURITY_NOT_READY"


@pytest.mark.anyio
async def test_verification_start_is_purpose_bound_without_clearing_sessions(
    browser_security: BrowserSecurity,
):
    gateway = StubInvitationGateway()
    existing = (
        f"{IDENTITY_COOKIE_NAME}={IDENTITY_TOKEN}; "
        f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}; "
        f"{INVITATION_IDENTITY_COOKIE_NAME}={'x' * 43}"
    )
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            "/v1/invitations/verify?return_path=%2Finvitations%2Faccept",
            headers={"Cookie": existing},
        )
    assert response.status_code == 303
    assert response.headers["location"] == AUTHORIZATION_URL
    assert gateway.initiated_inputs == [("/invitations/accept", "invitation_acceptance")]
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert any(cookie.startswith(f'{INVITATION_IDENTITY_COOKIE_NAME}="";') for cookie in cookies)
    assert any(cookie.startswith(f"{OIDC_BINDING_COOKIE_NAME}={BINDING};") for cookie in cookies)
    assert not any(cookie.startswith(f"{IDENTITY_COOKIE_NAME}=") for cookie in cookies)
    assert not any(cookie.startswith(f"{SESSION_COOKIE_NAME}=") for cookie in cookies)
    assert INVITATION_TOKEN not in response.headers["location"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query",
    [
        "?return_path=https%3A%2F%2Fattacker.example",
        "?return_path=%2Fone&return_path=%2Ftwo",
        "?return_path=%2F%2Fattacker.example",
    ],
)
async def test_verification_start_rejects_unsafe_or_ambiguous_return_paths(
    browser_security: BrowserSecurity,
    query: str,
):
    gateway = StubInvitationGateway()
    async with client_for(gateway, browser_security) as api:
        response = await api.get(f"/v1/invitations/verify{query}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == ("INVALID_INVITATION_VERIFICATION_REQUEST")
    assert gateway.initiated_inputs == []
    assert "attacker" not in response.text


@pytest.mark.anyio
async def test_invitation_callback_sets_only_short_lived_proof_cookie(
    browser_security: BrowserSecurity,
):
    gateway = StubInvitationGateway()
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/session/callback?state={STATE}&code=authorization-code",
            headers={"Cookie": f"{OIDC_BINDING_COOKIE_NAME}={BINDING}"},
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/invitations/accept"
    assert gateway.completed_inputs == [(STATE, BINDING, "authorization-code")]
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert any(cookie.startswith(f'{OIDC_BINDING_COOKIE_NAME}="";') for cookie in cookies)
    proof_cookie = next(
        cookie for cookie in cookies if cookie.startswith(f"{INVITATION_IDENTITY_COOKIE_NAME}=")
    )
    assert proof_cookie.startswith(f"{INVITATION_IDENTITY_COOKIE_NAME}={IDENTITY_PROOF};")
    assert "HttpOnly" in proof_cookie
    assert "Secure" in proof_cookie
    assert "SameSite=lax" in proof_cookie
    assert "Max-Age=" in proof_cookie
    assert not any(cookie.startswith(f"{IDENTITY_COOKIE_NAME}=") for cookie in cookies)
    assert not any(cookie.startswith(f"{SESSION_COOKIE_NAME}=") for cookie in cookies)
    assert IDENTITY_PROOF not in response.text
    assert IDENTITY_PROOF not in response.headers["location"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "completion",
    [
        invitation_completion(return_path="https://attacker.example"),
        invitation_completion(
            identity_proof=IssuedInvitationIdentityProof(
                id=uuid4(),
                token="short",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        ),
        invitation_completion(
            identity_proof=IssuedInvitationIdentityProof(
                id=uuid4(),
                token=IDENTITY_PROOF,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        ),
    ],
)
async def test_invitation_callback_revalidates_output_before_setting_proof_cookie(
    browser_security: BrowserSecurity,
    completion: CompletedInvitationIdentityVerification,
):
    gateway = StubInvitationGateway(completion=completion)
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/session/callback?state={STATE}&code=authorization-code",
            headers={"Cookie": f"{OIDC_BINDING_COOKIE_NAME}={BINDING}"},
        )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "LOGIN_CALLBACK_FAILED"
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 1
    assert cookies[0].startswith(f'{OIDC_BINDING_COOKIE_NAME}="";')
    assert not any(
        cookie.startswith(f"{INVITATION_IDENTITY_COOKIE_NAME}={IDENTITY_PROOF}")
        for cookie in cookies
    )
    assert "attacker" not in response.text


@pytest.mark.anyio
async def test_invitation_csrf_is_bound_to_exact_proof_cookie(
    browser_security: BrowserSecurity,
):
    gateway = StubInvitationGateway()
    async with client_for(gateway, browser_security) as api:
        accepted = await api.get(
            "/v1/invitations/csrf",
            headers={"Cookie": f"{INVITATION_IDENTITY_COOKIE_NAME}={IDENTITY_PROOF}"},
        )
        rejected = await api.get(
            "/v1/invitations/csrf",
            headers={"Cookie": f"{INVITATION_IDENTITY_COOKIE_NAME}=short"},
        )
    assert accepted.status_code == 200
    assert accepted.json() == {
        "schema_version": 1,
        "csrf_token": browser_security.issue_csrf_token(IDENTITY_PROOF),
    }
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "INVITATION_IDENTITY_REQUIRED"
    assert any("Max-Age=0" in cookie for cookie in rejected.headers.get_list("set-cookie"))


@pytest.mark.anyio
async def test_acceptance_uses_body_only_credentials_and_clears_proof_on_success(
    browser_security: BrowserSecurity,
):
    invitation_id = uuid4()
    expected = accepted_invitation(invitation_id)
    gateway = StubInvitationGateway(acceptance=expected)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/invitations/accept",
            headers=acceptance_headers(browser_security),
            json=acceptance_body(invitation_id),
        )
    assert response.status_code == 200
    assert gateway.acceptance_inputs == [
        (IDENTITY_PROOF, invitation_id, INVITATION_TOKEN, "Invitee User")
    ]
    assert response.json() == {
        "schema_version": 1,
        "invitation_id": str(invitation_id),
        "tenant_id": str(expected.tenant_id),
        "site_id": str(expected.site_id),
        "user_id": str(expected.user_id),
        "role_key": "editor",
        "accepted_at": expected.accepted_at.isoformat().replace("+00:00", "Z"),
    }
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 1
    assert cookies[0].startswith(f'{INVITATION_IDENTITY_COOKIE_NAME}="";')
    assert INVITATION_TOKEN not in str(response.url)
    assert INVITATION_TOKEN not in response.text
    assert IDENTITY_PROOF not in response.text


@pytest.mark.anyio
async def test_acceptance_denial_is_generic_and_preserves_proof_for_correction(
    browser_security: BrowserSecurity,
):
    invitation_id = uuid4()
    gateway = StubInvitationGateway(acceptance_error=InvitationAcceptanceDenied())
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/invitations/accept",
            headers=acceptance_headers(browser_security),
            json=acceptance_body(invitation_id),
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INVITATION_ACCEPTANCE_DENIED"
    assert "set-cookie" not in response.headers
    assert INVITATION_TOKEN not in response.text
    assert IDENTITY_PROOF not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        "duplicate_field",
        "extra_field",
        "wrong_content_type",
        "content_encoding",
        "invalid_utf8",
        "oversized",
        "wrong_uuid_version",
        "unsafe_display_name",
    ],
)
async def test_acceptance_rejects_noncanonical_request_bodies(
    browser_security: BrowserSecurity,
    case: str,
):
    invitation_id = uuid4()
    gateway = StubInvitationGateway()
    headers = acceptance_headers(browser_security)
    body = (
        f'{{"invitation_id":"{invitation_id}","token":"{INVITATION_TOKEN}",'
        '"display_name":"Invitee User"}'
    ).encode()
    if case == "duplicate_field":
        body = (
            f'{{"invitation_id":"{invitation_id}","token":"{INVITATION_TOKEN}",'
            f'"token":"{"z" * 43}","display_name":"Invitee User"}}'
        ).encode()
    elif case == "extra_field":
        body = body[:-1] + b',"authority":"owner"}'
    elif case == "wrong_content_type":
        headers["Content-Type"] = "text/plain"
    elif case == "content_encoding":
        headers["Content-Encoding"] = "identity"
    elif case == "invalid_utf8":
        body = b"\xff"
    elif case == "oversized":
        body = b"{" + b" " * 2048 + b"}"
    elif case == "wrong_uuid_version":
        body = body.replace(str(invitation_id).encode(), str(UUID(int=0)).encode())
    elif case == "unsafe_display_name":
        body = body.replace(b"Invitee User", b" Invitee User")

    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/invitations/accept",
            headers=headers,
            content=body,
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INVITATION_ACCEPTANCE"
    assert gateway.acceptance_inputs == []
    assert INVITATION_TOKEN not in response.text


@pytest.mark.anyio
async def test_acceptance_rejects_browser_proof_before_gateway(
    browser_security: BrowserSecurity,
):
    invitation_id = uuid4()
    gateway = StubInvitationGateway()
    headers = acceptance_headers(browser_security)
    headers[CSRF_HEADER_NAME] = "x" * 43
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/invitations/accept",
            headers=headers,
            json=acceptance_body(invitation_id),
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "BROWSER_REQUEST_REJECTED"
    assert gateway.acceptance_inputs == []


@pytest.mark.anyio
async def test_invalid_gateway_projection_never_clears_browser_proof(
    browser_security: BrowserSecurity,
):
    invitation_id = uuid4()
    gateway = StubInvitationGateway(
        acceptance=accepted_invitation(invitation_id, invitation_id=uuid4())
    )
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/invitations/accept",
            headers=acceptance_headers(browser_security),
            json=acceptance_body(invitation_id),
        )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INVITATION_ACCEPTANCE_FAILED"
    assert "set-cookie" not in response.headers


@pytest.mark.anyio
async def test_unexpected_acceptance_failure_is_redacted_from_response_and_log(
    browser_security: BrowserSecurity,
    caplog: pytest.LogCaptureFixture,
):
    invitation_id = uuid4()
    gateway = StubInvitationGateway(
        acceptance_error=RuntimeError("database detail " + INVITATION_TOKEN)
    )
    with caplog.at_level(logging.ERROR, logger="signal.api"):
        async with client_for(gateway, browser_security) as api:
            response = await api.post(
                "/v1/invitations/accept",
                headers=acceptance_headers(browser_security),
                json=acceptance_body(invitation_id),
            )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "set-cookie" not in response.headers
    assert INVITATION_TOKEN not in response.text
    assert INVITATION_TOKEN not in caplog.text
