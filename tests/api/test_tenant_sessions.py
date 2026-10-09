from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from signal_api.authentication import TenantSelectionDenied
from signal_api.browser_security import (
    CSRF_HEADER_NAME,
    IDENTITY_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    BrowserSecurity,
)
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.identity_memberships import IdentityMembership
from signal_core.session_issuance import InvalidIdentitySession, IssuedTenantSession

IDENTITY_TOKEN = "i" * 43
TENANT_TOKEN = "t" * 43
ORIGIN = "https://dashboard.example.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def browser_security() -> BrowserSecurity:
    return BrowserSecurity(
        csrf_hmac_key=b"account-csrf-key" * 4,
        allowed_origins=frozenset({ORIGIN}),
    )


@dataclass
class StubTenantGateway:
    membership_result: tuple[IdentityMembership, ...] = ()
    membership_error: Exception | None = None
    selection_result: IssuedTenantSession | None = None
    selection_error: Exception | None = None
    membership_tokens: list[str] = field(default_factory=list)
    selections: list[tuple[str, object]] = field(default_factory=list)

    async def memberships(self, *, identity_session_token: str):
        self.membership_tokens.append(identity_session_token)
        if self.membership_error is not None:
            raise self.membership_error
        return self.membership_result

    async def select_tenant(self, *, identity_session_token: str, tenant_id: object):
        self.selections.append((identity_session_token, tenant_id))
        if self.selection_error is not None:
            raise self.selection_error
        if self.selection_result is None:
            raise AssertionError("A selection result was not configured.")
        return self.selection_result


def issued_tenant_session(tenant_id: UUID, **overrides) -> IssuedTenantSession:
    values = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "user_id": uuid4(),
        "token": TENANT_TOKEN,
        "role_key": "editor",
        "authentication_level": "primary",
        "expires_at": datetime.now(UTC) + timedelta(minutes=30),
        **overrides,
    }
    return IssuedTenantSession(**values)


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


def identity_cookie() -> dict[str, str]:
    return {"Cookie": f"{IDENTITY_COOKIE_NAME}={IDENTITY_TOKEN}"}


def switch_headers(security: BrowserSecurity) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-site",
        "Cookie": f"{IDENTITY_COOKIE_NAME}={IDENTITY_TOKEN}",
        CSRF_HEADER_NAME: security.issue_csrf_token(IDENTITY_TOKEN),
        "Content-Type": "application/json",
    }


@pytest.mark.anyio
async def test_membership_directory_returns_only_gateway_authorized_summaries(
    browser_security: BrowserSecurity,
):
    tenant_id = uuid4()
    gateway = StubTenantGateway(
        membership_result=(
            IdentityMembership(tenant_id=tenant_id, tenant_name="Acme", role_key="editor"),
        )
    )
    async with client_for(gateway, browser_security) as api:
        response = await api.get("/v1/organizations", headers=identity_cookie())
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "organizations": [{"tenant_id": str(tenant_id), "name": "Acme", "role_key": "editor"}],
    }
    assert gateway.membership_tokens == [IDENTITY_TOKEN]
    assert IDENTITY_TOKEN not in response.text


@pytest.mark.anyio
async def test_empty_membership_directory_is_a_valid_identity_state(
    browser_security: BrowserSecurity,
):
    gateway = StubTenantGateway()
    async with client_for(gateway, browser_security) as api:
        response = await api.get("/v1/organizations", headers=identity_cookie())
    assert response.status_code == 200
    assert response.json() == {"schema_version": 1, "organizations": []}


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["missing", "malformed", "invalid_session"])
async def test_membership_directory_rejects_invalid_identity_without_detail(
    browser_security: BrowserSecurity, failure: str
):
    gateway = StubTenantGateway(
        membership_error=InvalidIdentitySession() if failure == "invalid_session" else None
    )
    headers = identity_cookie()
    if failure == "missing":
        headers = {}
    elif failure == "malformed":
        headers = {"Cookie": f"{IDENTITY_COOKIE_NAME}=short"}
    async with client_for(gateway, browser_security) as api:
        response = await api.get("/v1/organizations", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "IDENTITY_SESSION_REQUIRED"
    cookies = response.headers.get_list("set-cookie")
    assert any(value.startswith(f'{IDENTITY_COOKIE_NAME}="";') for value in cookies)
    assert any(value.startswith(f'{SESSION_COOKIE_NAME}="";') for value in cookies)


@pytest.mark.anyio
async def test_csrf_endpoint_returns_only_a_session_bound_proof(
    browser_security: BrowserSecurity,
):
    gateway = StubTenantGateway()
    async with client_for(gateway, browser_security) as api:
        response = await api.get("/v1/session/csrf", headers=identity_cookie())
    expected = browser_security.issue_csrf_token(IDENTITY_TOKEN)
    assert response.status_code == 200
    assert response.json() == {"schema_version": 1, "csrf_token": expected}
    assert IDENTITY_TOKEN not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.anyio
async def test_csrf_endpoint_requires_security_configuration_and_identity_cookie(
    browser_security: BrowserSecurity,
):
    gateway = StubTenantGateway()
    async with client_for(gateway, None) as api:
        unavailable = await api.get("/v1/session/csrf", headers=identity_cookie())
    async with client_for(gateway, browser_security) as api:
        unauthorized = await api.get("/v1/session/csrf")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "BROWSER_SECURITY_NOT_READY"
    assert unauthorized.status_code == 401


@pytest.mark.anyio
async def test_tenant_selection_sets_scoped_cookie_and_rotates_csrf(
    browser_security: BrowserSecurity,
):
    tenant_id = uuid4()
    issued = issued_tenant_session(tenant_id)
    gateway = StubTenantGateway(selection_result=issued)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/session/switch-tenant",
            headers=switch_headers(browser_security),
            content=f'{{"tenant_id":"{tenant_id}"}}',
        )
    assert response.status_code == 200
    assert gateway.selections == [(IDENTITY_TOKEN, tenant_id)]
    body = response.json()
    assert body["tenant_id"] == str(tenant_id)
    assert body["user_id"] == str(issued.user_id)
    assert body["role_key"] == "editor"
    assert body["authentication_level"] == "primary"
    assert body["csrf_token"] == browser_security.issue_csrf_token(TENANT_TOKEN)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE_NAME}={TENANT_TOKEN};")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Domain=" not in cookie
    assert IDENTITY_TOKEN not in response.text
    assert TENANT_TOKEN not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize("proof_failure", ["origin", "csrf", "identity_cookie"])
async def test_tenant_selection_requires_identity_bound_browser_proof(
    browser_security: BrowserSecurity, proof_failure: str
):
    tenant_id = uuid4()
    gateway = StubTenantGateway(selection_result=issued_tenant_session(tenant_id))
    headers = switch_headers(browser_security)
    if proof_failure == "origin":
        headers.pop("Origin")
    elif proof_failure == "csrf":
        headers[CSRF_HEADER_NAME] = "x" * 43
    else:
        headers["Cookie"] = f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/session/switch-tenant",
            headers=headers,
            content=f'{{"tenant_id":"{tenant_id}"}}',
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "BROWSER_REQUEST_REJECTED"
    assert gateway.selections == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "header_overrides"),
    [
        (b"{}", {}),
        (b'{"tenant_id":"bad"}', {}),
        (b'{"tenant_id":"00000000-0000-4000-8000-000000000001","tenant_id":"x"}', {}),
        (b'{"tenant_id":"00000000-0000-4000-8000-000000000001","extra":true}', {}),
        (b"x" * 1025, {}),
        (b'{"tenant_id":"00000000-0000-4000-8000-000000000001"}', {"Content-Encoding": "gzip"}),
    ],
)
async def test_tenant_selection_body_is_bounded_strict_json(
    browser_security: BrowserSecurity,
    body: bytes,
    header_overrides: dict[str, str],
):
    gateway = StubTenantGateway()
    headers = switch_headers(browser_security) | header_overrides
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/session/switch-tenant",
            headers=headers,
            content=body,
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_TENANT_SELECTION"
    assert gateway.selections == []


@pytest.mark.anyio
async def test_inaccessible_tenant_is_forbidden_without_changing_cookies(
    browser_security: BrowserSecurity,
):
    tenant_id = uuid4()
    gateway = StubTenantGateway(selection_error=TenantSelectionDenied())
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/session/switch-tenant",
            headers=switch_headers(browser_security),
            content=f'{{"tenant_id":"{tenant_id}"}}',
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TENANT_SELECTION_DENIED"
    assert "set-cookie" not in response.headers


@pytest.mark.anyio
async def test_invalid_identity_during_selection_clears_both_session_levels(
    browser_security: BrowserSecurity,
):
    tenant_id = uuid4()
    gateway = StubTenantGateway(selection_error=InvalidIdentitySession())
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/session/switch-tenant",
            headers=switch_headers(browser_security),
            content=f'{{"tenant_id":"{tenant_id}"}}',
        )
    assert response.status_code == 401
    cookies = response.headers.get_list("set-cookie")
    assert any(value.startswith(f'{IDENTITY_COOKIE_NAME}="";') for value in cookies)
    assert any(value.startswith(f'{SESSION_COOKIE_NAME}="";') for value in cookies)


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_output", ["tenant", "token", "expiry", "role"])
async def test_tenant_selection_revalidates_gateway_output_before_cookie(
    browser_security: BrowserSecurity, invalid_output: str
):
    requested_tenant = uuid4()
    overrides = {}
    if invalid_output == "tenant":
        tenant_id = uuid4()
    else:
        tenant_id = requested_tenant
    if invalid_output == "token":
        overrides["token"] = "short"
    elif invalid_output == "expiry":
        overrides["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    elif invalid_output == "role":
        overrides["role_key"] = "platform-admin"
    gateway = StubTenantGateway(selection_result=issued_tenant_session(tenant_id, **overrides))
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/session/switch-tenant",
            headers=switch_headers(browser_security),
            content=f'{{"tenant_id":"{requested_tenant}"}}',
        )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "TENANT_SESSION_FAILED"
    assert TENANT_TOKEN not in "\n".join(response.headers.get_list("set-cookie"))


@pytest.mark.anyio
async def test_tenant_routes_fail_closed_without_tenant_gateway(
    browser_security: BrowserSecurity,
):
    async with client_for(None, browser_security) as api:
        organizations = await api.get("/v1/organizations", headers=identity_cookie())
        selection = await api.post(
            "/v1/session/switch-tenant",
            headers=switch_headers(browser_security),
            content=f'{{"tenant_id":"{uuid4()}"}}',
        )
    assert organizations.status_code == 503
    assert selection.status_code == 503
    assert organizations.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"
    assert selection.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"
