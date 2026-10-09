import ssl
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.integration_scope import load_integration_scope

from tests.api.test_slack_http import ORIGIN, SITE, TOKEN


class Gateway:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def read(self, token, site):
        assert token == TOKEN and site == SITE
        if self.fail:
            raise ValueError("private provider error must not escape")
        return {"availability": "unbound"}

    async def control(self, token, site, command):
        assert token == TOKEN and site == SITE
        if self.fail:
            raise ValueError("private provider error must not escape")
        self.calls.append(command)
        return {"state": "selecting"}

    async def read_pr(self, token, site):
        return await self.read(token, site)

    async def control_pr(self, token, site, command):
        return await self.control(token, site, command)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("connector", ["gsc", "github", "github-pr", "bing"])
async def test_owner_connector_late_composition_and_strict_csrf_command(connector):
    security = BrowserSecurity(b"synthetic-owner-connectors-0118-key", frozenset({ORIGIN}))
    app = create_app(settings=ApiSettings(environment="test"), browser_security=security)
    gateway = Gateway()
    route = f"/v1/sites/{SITE}/{connector}"
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": security.issue_csrf_token(TOKEN),
    }
    command = (
        {"operation": "authorize"}
        if connector in {"gsc", "bing"}
        else {
            "operation": "prepare" if connector == "github-pr" else "bind",
            "idempotency_key": str(uuid4()),
            **({"binding_id": str(uuid4())} if connector == "github-pr" else {}),
        }
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (await api.get(route)).status_code == 503
        state_key = "browser_" + ("github" if connector == "github-pr" else connector)
        setattr(app.state, state_key, gateway)
        response = await api.get(route, headers=headers)
        assert response.json() == {"availability": "unbound"}
        assert response.headers["cache-control"] == "no-store"
        assert (await api.post(route, headers=headers, json=command)).status_code == 200
        for changed in (
            {"Origin": "https://evil.invalid"},
            {"Cookie": ""},
            {"X-CSRF-Token": "x" * 43},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            assert (
                await api.post(route, headers={**headers, **changed}, json=command)
            ).status_code == 403
        for body, extra in (
            (b'{"operation":"authorize","operation":"bind"}', {}),
            (b"{}", {"Content-Encoding": "gzip"}),
            (b"x" * 4097, {}),
            (b'{"operation":"write","token":"private"}', {}),
        ):
            assert (
                await api.post(
                    route,
                    headers={**headers, "Content-Type": "application/json", **extra},
                    content=body,
                )
            ).status_code == 422
        gateway.fail = True
        response = await api.post(route, headers=headers, json=command)
        assert response.status_code == 403 and "private" not in response.text
        response = await api.get(route, headers=headers)
        assert response.status_code == 401 and "private" not in response.text
        setattr(app.state, state_key, None)
        assert (await api.get(route)).status_code == 503
    assert len(gateway.calls) == 1


@pytest.mark.parametrize("connector", ["gsc", "github"])
def test_exact_test_routes_are_admitted_without_other_work(connector):
    from signal_api.integration_connectors import admitted_connector_route

    TEST_SITE = load_integration_scope().site

    assert admitted_connector_route(
        "GET", f"/v1/sites/{TEST_SITE}/{connector}", load_integration_scope()
    )
    assert admitted_connector_route(
        "POST", f"/v1/sites/{TEST_SITE}/{connector}", load_integration_scope()
    )
    assert not admitted_connector_route(
        "POST", f"/v1/sites/{uuid4()}/{connector}", load_integration_scope()
    )
    assert not admitted_connector_route(
        "DELETE", f"/v1/sites/{TEST_SITE}/{connector}", load_integration_scope()
    )


@pytest.mark.anyio
@pytest.mark.parametrize("denied", [False, True])
async def test_github_status_checks_current_authority_before_private_tls_key_read(
    monkeypatch, denied
):
    from signal_api.owner_connector_http import ComposedGithubGateway

    context = ssl.create_default_context()
    credential = SimpleNamespace(credentials=AsyncMock(return_value="synthetic-credential"))
    recovery = SimpleNamespace(
        current_generation=AsyncMock(return_value=SimpleNamespace(value="synthetic-generation"))
    )
    connection = object()

    def scope(self, received, token, site, generation, connector):
        assert (
            received is connection
            and connector == "github"
            and generation == "synthetic-generation"
        )
        if denied:
            raise ValueError("current owner authority denied")
        return {"availability": "unbound"}

    monkeypatch.setattr(ComposedGithubGateway, "scope", scope)
    gateway = ComposedGithubGateway(
        lambda: nullcontext(connection),
        recovery,
        None,
        recovery_options={"verify": context},
        credential=credential,
        credential_options={"openbao_verify": context},
    )
    if denied:
        with pytest.raises(ValueError):
            await gateway.read(TOKEN, SITE)
        credential.credentials.assert_not_awaited()
    else:
        assert await gateway.read(TOKEN, SITE) == {"availability": "unbound"}
        credential.credentials.assert_awaited_once_with(transport=None, verify=context)
    recovery.current_generation.assert_awaited_once_with(verify=context)


@pytest.mark.anyio
async def test_unprotected_acceptance_is_explicit_strict_and_csrf_protected():
    security = BrowserSecurity(b"synthetic-unprotected-acceptance-key", frozenset({ORIGIN}))
    app = create_app(settings=ApiSettings(environment="test"), browser_security=security)
    gateway = Gateway()
    app.state.browser_github = gateway
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": security.issue_csrf_token(TOKEN),
    }
    route = f"/v1/sites/{SITE}/github"
    command = {
        "operation": "accept_unprotected",
        "binding_id": str(uuid4()),
        "accept_default_branch_unprotected": True,
    }
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as api:
        for value in (False, 1, "true", None):
            assert (
                await api.post(
                    route,
                    headers=headers,
                    json={**command, "accept_default_branch_unprotected": value},
                )
            ).status_code == 422
        assert (
            await api.post(route, headers=headers, json={**command, "owner_user_id": str(uuid4())})
        ).status_code == 422
        assert (await api.post(route, json=command)).status_code == 403
        assert not gateway.calls
        assert (await api.post(route, headers=headers, json=command)).status_code == 200
        assert len(gateway.calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("denied", [False, True])
async def test_bing_current_owner_check_precedes_openbao_and_options_stay_private(
    monkeypatch, denied
):
    from signal_api.owner_connector_http import ComposedBingGateway

    connection = object()
    context = ssl.create_default_context()
    secrets = SimpleNamespace(client_credentials=AsyncMock(return_value=object()))
    recovery = SimpleNamespace(
        current_generation=AsyncMock(return_value=SimpleNamespace(value="generation"))
    )

    def scope(self, received, token, site, generation, connector):
        assert received is connection and connector == "bing"
        if denied:
            raise ValueError("current owner denied")
        return {"availability": "unbound"}

    monkeypatch.setattr(ComposedBingGateway, "scope", scope)
    gateway = ComposedBingGateway(
        lambda: nullcontext(connection),
        recovery,
        None,
        secrets_store=secrets,
        secret_options={"verify": context},
    )
    if denied:
        with pytest.raises(ValueError):
            await gateway.read(TOKEN, SITE)
        secrets.client_credentials.assert_not_awaited()
    else:
        assert await gateway.read(TOKEN, SITE) == {"availability": "unbound"}
        secrets.client_credentials.assert_awaited_once_with(verify=context)


@pytest.mark.anyio
async def test_bing_confirmation_cannot_select_another_site(monkeypatch):
    from signal_api.owner_connector_http import BingConfirm, ComposedBingGateway

    scope = load_integration_scope()
    confirm = AsyncMock()
    monkeypatch.setattr("signal_api.owner_connector_http.confirm_bing_binding", confirm)
    monkeypatch.setattr(ComposedBingGateway, "scope", lambda *args: {"availability": "unbound"})
    gateway = ComposedBingGateway(
        lambda: nullcontext(object()),
        SimpleNamespace(
            current_generation=AsyncMock(return_value=SimpleNamespace(value="generation"))
        ),
        None,
        secrets_store=object(),
    )
    with pytest.raises(ValueError):
        await gateway.control(
            TOKEN,
            scope.site,
            BingConfirm(operation="confirm", attempt_id=uuid4(), site_url="https://other.invalid/"),
        )
    confirm.assert_not_called()
