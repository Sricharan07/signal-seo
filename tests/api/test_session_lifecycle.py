from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
from signal_core.authorization import InvalidSession
from signal_core.session_management import (
    AuthorizedSite,
    CurrentTenantSession,
    SelectedSiteContext,
    SessionContextConflict,
    SiteSelectionDenied,
    TenantSiteDirectory,
)
from signal_core.site_onboarding import (
    InvalidSiteOnboarding,
    OnboardedSite,
    SiteLimitReached,
    SiteOnboardingConflict,
    SiteOnboardingDenied,
)

IDENTITY_TOKEN = "i" * 43
TENANT_TOKEN = "t" * 43
ORIGIN = "https://dashboard.example.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def browser_security() -> BrowserSecurity:
    return BrowserSecurity(
        csrf_hmac_key=b"session-lifecycle-key" * 2,
        allowed_origins=frozenset({ORIGIN}),
    )


def current_session(**overrides) -> CurrentTenantSession:
    values = {
        "tenant_id": uuid4(),
        "user_id": uuid4(),
        "role_key": "analyst",
        "authentication_level": "primary",
        "expires_at": datetime.now(UTC) + timedelta(minutes=30),
        "session_version": 1,
        "active_site_id": None,
        **overrides,
    }
    return CurrentTenantSession(**values)


def current_directory(**overrides) -> TenantSiteDirectory:
    values = {
        "tenant_id": uuid4(),
        "tenant_name": "Example organization",
        "sites": (
            AuthorizedSite(
                id=uuid4(),
                name="Example site",
                primary_origin="https://example.test",
                timezone="UTC",
                reporting_currency="USD",
                state="onboarding",
                ownership_status="unverified",
            ),
        ),
        **overrides,
    }
    return TenantSiteDirectory(**values)


def duplicate_site_directory() -> TenantSiteDirectory:
    duplicate = AuthorizedSite(
        id=uuid4(),
        name="Duplicate",
        primary_origin="https://example.test",
        timezone="UTC",
        reporting_currency="USD",
        state="active",
        ownership_status="unverified",
    )
    return current_directory(sites=(duplicate, duplicate))


def selected_site(**overrides) -> SelectedSiteContext:
    values = {
        "tenant_id": uuid4(),
        "user_id": uuid4(),
        "site_id": uuid4(),
        "session_version": 2,
        "changed": True,
        **overrides,
    }
    return SelectedSiteContext(**values)


def onboarded_site(**overrides) -> OnboardedSite:
    values = {
        "tenant_id": uuid4(),
        "user_id": uuid4(),
        "site_id": uuid4(),
        "name": "New site",
        "primary_origin": "https://www.example.com",
        "timezone": "America/Phoenix",
        "reporting_currency": "USD",
        "session_version": 3,
        "replayed": False,
        **overrides,
    }
    return OnboardedSite(**values)


@dataclass
class StubSessionGateway:
    inspection: CurrentTenantSession = field(default_factory=current_session)
    inspection_error: Exception | None = None
    logout_result: bool = True
    logout_error: Exception | None = None
    directory: TenantSiteDirectory = field(default_factory=current_directory)
    directory_error: Exception | None = None
    selection: SelectedSiteContext = field(default_factory=selected_site)
    selection_error: Exception | None = None
    onboarding: OnboardedSite = field(default_factory=onboarded_site)
    onboarding_error: Exception | None = None
    inspected_tokens: list[str] = field(default_factory=list)
    logout_inputs: list[tuple[str, str]] = field(default_factory=list)
    directory_tokens: list[str] = field(default_factory=list)
    selection_inputs: list[tuple[str, object, object]] = field(default_factory=list)
    onboarding_inputs: list[tuple[object, ...]] = field(default_factory=list)

    async def current_session(self, *, session_token: str) -> CurrentTenantSession:
        self.inspected_tokens.append(session_token)
        if self.inspection_error is not None:
            raise self.inspection_error
        return self.inspection

    async def logout(self, *, session_token: str, presented_session_kind: str) -> bool:
        self.logout_inputs.append((session_token, presented_session_kind))
        if self.logout_error is not None:
            raise self.logout_error
        return self.logout_result

    async def site_directory(self, *, session_token: str) -> TenantSiteDirectory:
        self.directory_tokens.append(session_token)
        if self.directory_error is not None:
            raise self.directory_error
        return self.directory

    async def select_site(
        self,
        *,
        session_token: str,
        site_id: object,
        expected_session_version: object,
    ) -> SelectedSiteContext:
        self.selection_inputs.append((session_token, site_id, expected_session_version))
        if self.selection_error is not None:
            raise self.selection_error
        return self.selection

    async def onboard_site(
        self,
        *,
        session_token: str,
        name: str,
        primary_origin: str,
        timezone: str,
        reporting_currency: str,
        expected_session_version: int,
        idempotency_key: object,
    ) -> OnboardedSite:
        self.onboarding_inputs.append(
            (
                session_token,
                name,
                primary_origin,
                timezone,
                reporting_currency,
                expected_session_version,
                idempotency_key,
            )
        )
        if self.onboarding_error is not None:
            raise self.onboarding_error
        return self.onboarding


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
    )


def tenant_cookie() -> dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"}


def logout_headers(
    security: BrowserSecurity,
    *,
    token: str = TENANT_TOKEN,
    cookie_name: str = SESSION_COOKIE_NAME,
) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Cookie": f"{cookie_name}={token}",
        CSRF_HEADER_NAME: security.issue_csrf_token(token),
    }


@pytest.mark.anyio
async def test_current_session_returns_only_server_verified_context(
    browser_security: BrowserSecurity,
):
    current = current_session()
    gateway = StubSessionGateway(inspection=current)
    async with client_for(gateway, browser_security) as api:
        response = await api.get("/v1/session", headers=tenant_cookie())
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 2,
        "tenant_id": str(current.tenant_id),
        "user_id": str(current.user_id),
        "role_key": "analyst",
        "authentication_level": "primary",
        "expires_at": current.expires_at.isoformat().replace("+00:00", "Z"),
        "session_version": 1,
        "active_site_id": None,
    }
    assert gateway.inspected_tokens == [TENANT_TOKEN]
    assert TENANT_TOKEN not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["missing", "malformed", "invalid_server_session"])
async def test_current_session_rejects_invalid_state_and_clears_tenant_cookie(
    browser_security: BrowserSecurity,
    failure: str,
):
    gateway = StubSessionGateway(
        inspection_error=InvalidSession() if failure == "invalid_server_session" else None
    )
    headers = tenant_cookie()
    if failure == "missing":
        headers = {}
    elif failure == "malformed":
        headers = {"Cookie": f"{SESSION_COOKIE_NAME}=short"}
    async with client_for(gateway, browser_security) as api:
        response = await api.get("/v1/session", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TENANT_SESSION_REQUIRED"
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 1
    assert cookies[0].startswith(f'{SESSION_COOKIE_NAME}="";')


@pytest.mark.anyio
@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": "not-a-uuid"},
        {"user_id": "not-a-uuid"},
        {"role_key": "platform-admin"},
        {"authentication_level": "bypass"},
        {"expires_at": "not-a-time"},
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        {"session_version": 0},
        {"session_version": True},
        {"active_site_id": "not-a-uuid"},
    ],
)
async def test_current_session_revalidates_gateway_projection(
    browser_security: BrowserSecurity,
    overrides: dict[str, object],
):
    gateway = StubSessionGateway(inspection=current_session(**overrides))
    async with client_for(gateway, browser_security) as api:
        response = await api.get("/v1/session", headers=tenant_cookie())
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "SESSION_INSPECTION_FAILED"


@pytest.mark.anyio
async def test_site_directory_returns_only_bounded_server_projection(
    browser_security: BrowserSecurity,
):
    directory = current_directory()
    gateway = StubSessionGateway(directory=directory)
    async with client_for(gateway, browser_security) as api:
        response = await api.get("/v1/sites", headers=tenant_cookie())
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "tenant_id": str(directory.tenant_id),
        "tenant_name": "Example organization",
        "sites": [
            {
                "id": str(directory.sites[0].id),
                "name": "Example site",
                "primary_origin": "https://example.test",
                "timezone": "UTC",
                "reporting_currency": "USD",
                "state": "onboarding",
                "ownership_status": "unverified",
            }
        ],
    }
    assert gateway.directory_tokens == [TENANT_TOKEN]
    assert TENANT_TOKEN not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["missing", "malformed", "invalid_server_session"])
async def test_site_directory_rejects_invalid_session_and_clears_cookie(
    browser_security: BrowserSecurity,
    failure: str,
):
    gateway = StubSessionGateway(
        directory_error=InvalidSession() if failure == "invalid_server_session" else None
    )
    headers = tenant_cookie()
    if failure == "missing":
        headers = {}
    elif failure == "malformed":
        headers = {"Cookie": f"{SESSION_COOKIE_NAME}=short"}
    async with client_for(gateway, browser_security) as api:
        response = await api.get("/v1/sites", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TENANT_SESSION_REQUIRED"
    assert response.headers.get_list("set-cookie")[0].startswith(f'{SESSION_COOKIE_NAME}="";')


@pytest.mark.anyio
@pytest.mark.parametrize(
    "directory",
    [
        current_directory(tenant_id="not-a-uuid"),
        current_directory(tenant_name=""),
        current_directory(tenant_name="Bad\ntenant"),
        current_directory(
            sites=(
                AuthorizedSite(
                    id=uuid4(),
                    name="Bad state",
                    primary_origin="https://example.test",
                    timezone="UTC",
                    reporting_currency="USD",
                    state="archived",
                    ownership_status="unverified",
                ),
            )
        ),
        current_directory(
            sites=(
                AuthorizedSite(
                    id=uuid4(),
                    name="Bad\nname",
                    primary_origin="https://example.test",
                    timezone="UTC",
                    reporting_currency="USD",
                    state="active",
                    ownership_status="unverified",
                ),
            )
        ),
        current_directory(
            sites=(
                AuthorizedSite(
                    id=uuid4(),
                    name="Bad origin",
                    primary_origin="https://example.test/path",
                    timezone="UTC",
                    reporting_currency="USD",
                    state="active",
                    ownership_status="unverified",
                ),
            )
        ),
        current_directory(
            sites=(
                AuthorizedSite(
                    id=uuid4(),
                    name="Bad timezone",
                    primary_origin="https://example.test",
                    timezone="Mars/Base",
                    reporting_currency="USD",
                    state="active",
                    ownership_status="unverified",
                ),
            )
        ),
        duplicate_site_directory(),
    ],
)
async def test_site_directory_revalidates_gateway_projection(
    browser_security: BrowserSecurity,
    directory: TenantSiteDirectory,
):
    gateway = StubSessionGateway(directory=directory)
    async with client_for(gateway, browser_security) as api:
        response = await api.get("/v1/sites", headers=tenant_cookie())
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "SITE_DIRECTORY_FAILED"
    assert "Bad state" not in response.text


@pytest.mark.anyio
async def test_tenant_csrf_returns_session_bound_proof(
    browser_security: BrowserSecurity,
):
    async with client_for(StubSessionGateway(), browser_security) as api:
        response = await api.get("/v1/session/tenant-csrf", headers=tenant_cookie())
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "csrf_token": browser_security.issue_csrf_token(TENANT_TOKEN),
    }
    assert TENANT_TOKEN not in response.text


@pytest.mark.anyio
async def test_site_selection_sets_exact_server_context(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    selection = selected_site(site_id=site_id)
    gateway = StubSessionGateway(selection=selection)
    headers = logout_headers(browser_security)
    async with client_for(gateway, browser_security) as api:
        response = await api.put(
            "/v1/session/site",
            headers=headers,
            json={"site_id": str(site_id), "expected_session_version": 1},
        )
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "tenant_id": str(selection.tenant_id),
        "user_id": str(selection.user_id),
        "site_id": str(site_id),
        "session_version": 2,
        "changed": True,
    }
    assert gateway.selection_inputs == [(TENANT_TOKEN, site_id, 1)]
    assert "set-cookie" not in response.headers


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidSession(), 401, "TENANT_SESSION_REQUIRED"),
        (SiteSelectionDenied(), 403, "SITE_SELECTION_DENIED"),
        (SessionContextConflict(), 409, "SITE_CONTEXT_CONFLICT"),
    ],
)
async def test_site_selection_maps_closed_authority_outcomes(
    browser_security: BrowserSecurity,
    error: Exception,
    status: int,
    code: str,
):
    gateway = StubSessionGateway(selection_error=error)
    async with client_for(gateway, browser_security) as api:
        response = await api.put(
            "/v1/session/site",
            headers=logout_headers(browser_security),
            json={"site_id": str(uuid4()), "expected_session_version": 1},
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    if status == 401:
        assert "set-cookie" in response.headers
    else:
        assert "set-cookie" not in response.headers


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"site_id": "not-a-uuid", "expected_session_version": 1},
        {"site_id": str(uuid4()), "expected_session_version": 0},
        {"site_id": str(uuid4()), "expected_session_version": True},
        {"site_id": str(uuid4()), "expected_session_version": 1, "tenant_id": str(uuid4())},
    ],
)
async def test_site_selection_rejects_malformed_body_without_gateway_call(
    browser_security: BrowserSecurity,
    payload: dict[str, object],
):
    gateway = StubSessionGateway()
    async with client_for(gateway, browser_security) as api:
        response = await api.put(
            "/v1/session/site",
            headers=logout_headers(browser_security),
            json=payload,
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_SITE_SELECTION"
    assert gateway.selection_inputs == []


@pytest.mark.anyio
async def test_site_selection_rejects_bad_browser_proof_and_bad_gateway_projection(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    gateway = StubSessionGateway(selection=selected_site(site_id=uuid4()))
    rejected_headers = logout_headers(browser_security)
    rejected_headers[CSRF_HEADER_NAME] = "x" * 43
    async with client_for(gateway, browser_security) as api:
        rejected = await api.put(
            "/v1/session/site",
            headers=rejected_headers,
            json={"site_id": str(site_id), "expected_session_version": 1},
        )
        invalid = await api.put(
            "/v1/session/site",
            headers=logout_headers(browser_security),
            json={"site_id": str(site_id), "expected_session_version": 1},
        )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "BROWSER_REQUEST_REJECTED"
    assert invalid.status_code == 500
    assert invalid.json()["error"]["code"] == "SITE_SELECTION_FAILED"
    assert gateway.selection_inputs == [(TENANT_TOKEN, site_id, 1)]


@pytest.mark.anyio
async def test_site_onboarding_returns_one_unverified_server_created_site(
    browser_security: BrowserSecurity,
):
    request_id = uuid4()
    created = onboarded_site()
    gateway = StubSessionGateway(onboarding=created)
    payload = {
        "idempotency_key": str(request_id),
        "name": created.name,
        "primary_origin": created.primary_origin,
        "timezone": created.timezone,
        "reporting_currency": created.reporting_currency,
        "expected_session_version": 2,
    }
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/sites",
            headers=logout_headers(browser_security),
            json=payload,
        )
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "tenant_id": str(created.tenant_id),
        "user_id": str(created.user_id),
        "site_id": str(created.site_id),
        "name": "New site",
        "primary_origin": "https://www.example.com",
        "timezone": "America/Phoenix",
        "reporting_currency": "USD",
        "state": "onboarding",
        "ownership_status": "unverified",
        "session_version": 3,
        "replayed": False,
    }
    assert gateway.onboarding_inputs == [
        (
            TENANT_TOKEN,
            "New site",
            "https://www.example.com",
            "America/Phoenix",
            "USD",
            2,
            request_id,
        )
    ]
    assert "set-cookie" not in response.headers


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidSession(), 401, "TENANT_SESSION_REQUIRED"),
        (InvalidSiteOnboarding(), 422, "INVALID_SITE_ONBOARDING"),
        (SiteOnboardingDenied(), 403, "SITE_ONBOARDING_DENIED"),
        (SiteOnboardingConflict(), 409, "SITE_ONBOARDING_CONFLICT"),
        (SiteLimitReached(), 409, "SITE_LIMIT_REACHED"),
    ],
)
async def test_site_onboarding_maps_closed_authority_outcomes(
    browser_security: BrowserSecurity,
    error: Exception,
    status: int,
    code: str,
):
    gateway = StubSessionGateway(onboarding_error=error)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/sites",
            headers=logout_headers(browser_security),
            json={
                "idempotency_key": str(uuid4()),
                "name": "New site",
                "primary_origin": "https://www.example.com",
                "timezone": "UTC",
                "reporting_currency": "USD",
                "expected_session_version": 2,
            },
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert ("set-cookie" in response.headers) is (status == 401)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "idempotency_key": str(uuid4()),
            "name": "New site",
            "primary_origin": "http://www.example.com",
            "timezone": "UTC",
            "reporting_currency": "USD",
            "expected_session_version": 2,
        },
        {
            "idempotency_key": str(uuid4()),
            "name": " New site ",
            "primary_origin": "https://www.example.com",
            "timezone": "UTC",
            "reporting_currency": "USD",
            "expected_session_version": 2,
        },
        {
            "idempotency_key": str(uuid4()),
            "name": "New site",
            "primary_origin": "https://www.example.com",
            "timezone": "Mars/Base",
            "reporting_currency": "usd",
            "expected_session_version": True,
        },
        {
            "idempotency_key": str(uuid4()),
            "name": "New site",
            "primary_origin": "https://www.example.com",
            "timezone": "UTC",
            "reporting_currency": "USD",
            "expected_session_version": 2,
            "tenant_id": str(uuid4()),
        },
    ],
)
async def test_site_onboarding_rejects_invalid_body_without_gateway_call(
    browser_security: BrowserSecurity,
    payload: dict[str, object],
):
    gateway = StubSessionGateway()
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/sites",
            headers=logout_headers(browser_security),
            json=payload,
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_SITE_ONBOARDING"
    assert gateway.onboarding_inputs == []


@pytest.mark.anyio
async def test_site_onboarding_rejects_bad_browser_proof_and_gateway_projection(
    browser_security: BrowserSecurity,
):
    created = onboarded_site(primary_origin="https://different.example.com")
    gateway = StubSessionGateway(onboarding=created)
    payload = {
        "idempotency_key": str(uuid4()),
        "name": "New site",
        "primary_origin": "https://www.example.com",
        "timezone": "America/Phoenix",
        "reporting_currency": "USD",
        "expected_session_version": 2,
    }
    bad_headers = logout_headers(browser_security)
    bad_headers[CSRF_HEADER_NAME] = "x" * 43
    async with client_for(gateway, browser_security) as api:
        rejected = await api.post("/v1/sites", headers=bad_headers, json=payload)
        failed = await api.post(
            "/v1/sites",
            headers=logout_headers(browser_security),
            json=payload,
        )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "BROWSER_REQUEST_REJECTED"
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "SITE_ONBOARDING_FAILED"
    assert len(gateway.onboarding_inputs) == 1


@pytest.mark.anyio
async def test_tenant_logout_revokes_server_session_and_clears_all_browser_authority(
    browser_security: BrowserSecurity,
):
    gateway = StubSessionGateway()
    headers = logout_headers(browser_security)
    headers["Cookie"] += f"; {IDENTITY_COOKIE_NAME}={IDENTITY_TOKEN}"
    async with client_for(gateway, browser_security) as api:
        response = await api.post("/v1/session/logout", headers=headers)
    assert response.status_code == 202
    assert response.json() == {"schema_version": 1, "status": "AUTHORITY_DURABILITY_PENDING"}
    assert gateway.logout_inputs == [(TENANT_TOKEN, "tenant")]
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 4
    for name in (
        OIDC_BINDING_COOKIE_NAME,
        IDENTITY_COOKIE_NAME,
        SESSION_COOKIE_NAME,
        INVITATION_IDENTITY_COOKIE_NAME,
    ):
        assert any(value.startswith(f'{name}="";') and "Max-Age=0" in value for value in cookies)


@pytest.mark.anyio
@pytest.mark.parametrize("session_kind", ["tenant", "identity"])
async def test_logout_csrf_returns_proof_for_the_session_logout_will_revoke(
    browser_security: BrowserSecurity,
    session_kind: str,
):
    if session_kind == "tenant":
        token = TENANT_TOKEN
        cookie_name = SESSION_COOKIE_NAME
    else:
        token = IDENTITY_TOKEN
        cookie_name = IDENTITY_COOKIE_NAME
    async with client_for(StubSessionGateway(), browser_security) as api:
        response = await api.get(
            "/v1/session/logout-csrf",
            headers={"Cookie": f"{cookie_name}={token}"},
        )
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "csrf_token": browser_security.issue_csrf_token(token),
    }
    assert token not in response.text


@pytest.mark.anyio
async def test_logout_csrf_rejects_missing_authority_and_unconfigured_security(
    browser_security: BrowserSecurity,
):
    async with client_for(StubSessionGateway(), browser_security) as api:
        missing = await api.get("/v1/session/logout-csrf")
    async with client_for(StubSessionGateway(), None) as api:
        unavailable = await api.get(
            "/v1/session/logout-csrf",
            headers=tenant_cookie(),
        )
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "TENANT_SESSION_REQUIRED"
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "BROWSER_SECURITY_NOT_READY"


@pytest.mark.anyio
async def test_pretenant_identity_can_logout_without_a_tenant_session(
    browser_security: BrowserSecurity,
):
    gateway = StubSessionGateway(logout_result=False)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/session/logout",
            headers=logout_headers(
                browser_security,
                token=IDENTITY_TOKEN,
                cookie_name=IDENTITY_COOKIE_NAME,
            ),
        )
    assert response.status_code == 202
    assert gateway.logout_inputs == [(IDENTITY_TOKEN, "identity")]
    assert len(response.headers.get_list("set-cookie")) == 4


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["wrong_csrf", "missing_origin", "duplicate_tenant"])
async def test_logout_rejects_missing_or_ambiguous_browser_proof(
    browser_security: BrowserSecurity,
    failure: str,
):
    gateway = StubSessionGateway()
    headers = list(logout_headers(browser_security).items())
    if failure == "wrong_csrf":
        headers[-1] = (CSRF_HEADER_NAME, "x" * 43)
    elif failure == "missing_origin":
        headers = [item for item in headers if item[0] != "Origin"]
    else:
        headers.append(("Cookie", f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"))
    async with client_for(gateway, browser_security) as api:
        response = await api.post("/v1/session/logout", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "BROWSER_REQUEST_REJECTED"
    assert gateway.logout_inputs == []


@pytest.mark.anyio
async def test_tenant_cookie_prevents_fallback_to_weaker_identity_csrf(
    browser_security: BrowserSecurity,
):
    gateway = StubSessionGateway()
    headers = logout_headers(
        browser_security,
        token=IDENTITY_TOKEN,
        cookie_name=IDENTITY_COOKIE_NAME,
    )
    headers["Cookie"] += f"; {SESSION_COOKIE_NAME}={TENANT_TOKEN}"
    async with client_for(gateway, browser_security) as api:
        response = await api.post("/v1/session/logout", headers=headers)
    assert response.status_code == 403
    assert gateway.logout_inputs == []


@pytest.mark.anyio
async def test_logout_does_not_claim_success_when_server_revocation_fails(
    browser_security: BrowserSecurity,
):
    gateway = StubSessionGateway(logout_error=RuntimeError("database secret detail"))
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/session/logout",
            headers=logout_headers(browser_security),
        )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "set-cookie" not in response.headers
    assert "secret" not in response.text


@pytest.mark.anyio
async def test_logout_revalidates_gateway_result_before_clearing_cookies(
    browser_security: BrowserSecurity,
):
    gateway = StubSessionGateway(logout_result="not-a-boolean")
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            "/v1/session/logout",
            headers=logout_headers(browser_security),
        )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "SESSION_LOGOUT_FAILED"
    assert "set-cookie" not in response.headers


@pytest.mark.anyio
async def test_session_lifecycle_routes_fail_closed_without_gateway(
    browser_security: BrowserSecurity,
):
    async with client_for(None, browser_security) as api:
        current = await api.get("/v1/session", headers=tenant_cookie())
        sites = await api.get("/v1/sites", headers=tenant_cookie())
        selection = await api.put(
            "/v1/session/site",
            headers=logout_headers(browser_security),
            json={"site_id": str(uuid4()), "expected_session_version": 1},
        )
        logout = await api.post(
            "/v1/session/logout",
            headers=logout_headers(browser_security),
        )
    assert current.status_code == 503
    assert sites.status_code == 503
    assert selection.status_code == 503
    assert logout.status_code == 503
    assert current.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"
    assert sites.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"
    assert selection.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"
    assert logout.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"
