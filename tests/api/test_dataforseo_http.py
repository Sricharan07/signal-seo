from uuid import uuid4

import httpx2
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app

TOKEN = "synthetic-" + "t" * 33
ORIGIN = "https://dashboard.example.invalid"
SITE = uuid4()


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Gateway:
    def __init__(self):
        self.calls = []

    async def read(self, token, site):
        assert token == TOKEN and site == SITE
        return {"availability": "unconfigured"}

    async def control(self, token, site, command):
        assert token == TOKEN and site == SITE
        self.calls.append(command)
        assert "synthetic-password" not in repr(command)
        return {"availability": "unconfigured"}


@pytest.mark.anyio
async def test_unconfigured_optional_provider_is_visibly_unavailable_and_health_works():
    app = create_app(settings=ApiSettings(environment="test"))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.get(f"/v1/sites/{SITE}/dataforseo")
        assert response.status_code == 503
        assert set(response.json()["features"].values()) == {"unavailable"}
        assert (await api.get("/health/live")).status_code == 200


@pytest.mark.anyio
async def test_secret_safe_strict_setup_and_same_origin_denials():
    gateway = Gateway()
    security = BrowserSecurity(
        csrf_hmac_key=b"synthetic-dataforseo-browser-key-0076", allowed_origins=frozenset({ORIGIN})
    )
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_dataforseo=gateway,
        browser_security=security,
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": security.issue_csrf_token(TOKEN),
    }
    command = {
        "operation": "credential",
        "login": "synthetic-login@example.invalid",
        "password": "synthetic-password",
    }
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (
            await api.post(f"/v1/sites/{SITE}/dataforseo", headers=headers, json=command)
        ).status_code == 200
        for change in (
            {"Origin": "https://evil.invalid"},
            {"X-CSRF-Token": "x" * 43},
            {"Cookie": ""},
        ):
            assert (
                await api.post(
                    f"/v1/sites/{SITE}/dataforseo", headers={**headers, **change}, json=command
                )
            ).status_code == 403
        for change in (
            {"password": "short"},
            {"extra": "synthetic-password"},
            {"operation": "query"},
        ):
            response = await api.post(
                f"/v1/sites/{SITE}/dataforseo", headers=headers, json={**command, **change}
            )
            assert response.status_code == 422 and "synthetic-password" not in response.text
        response = await api.post(
            f"/v1/sites/{SITE}/dataforseo",
            headers={**headers, "Content-Type": "application/json"},
            content=b"x" * 2049,
        )
        assert response.status_code == 422
    assert len(gateway.calls) == 1
