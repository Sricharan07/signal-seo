from uuid import uuid4

import httpx2
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app

TOKEN = "synthetic-" + "t" * 33
SITE = uuid4()
ORIGIN = "https://dashboard.example.invalid"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Gateway:
    def __init__(self):
        self.calls = []
        self.denied = False

    async def read(self, token, site):
        if self.denied:
            raise ValueError("synthetic-provider-secret")
        assert token == TOKEN and site == SITE
        return {"state": "disconnected"}

    async def control(self, token, site, command):
        if self.denied:
            raise ValueError("synthetic-provider-secret")
        assert token == TOKEN and site == SITE
        self.calls.append(command)
        return {"state": "imported"}


@pytest.mark.anyio
async def test_unconfigured_ga4_never_simulates_readiness():
    app = create_app(settings=ApiSettings(environment="test"))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.get(f"/v1/sites/{SITE}/ga4")
        assert response.status_code == 503 and response.json()["state"] == "unavailable"


@pytest.mark.anyio
async def test_ga4_csrf_and_closed_commands_and_secret_errors():
    gateway = Gateway()
    security = BrowserSecurity(
        csrf_hmac_key=b"synthetic-ga4-browser-security-key", allowed_origins=frozenset({ORIGIN})
    )
    app = create_app(
        settings=ApiSettings(environment="test"), browser_ga4=gateway, browser_security=security
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": security.issue_csrf_token(TOKEN),
    }
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as api:
        command = {"operation": "import", "start_date": "2026-09-01", "end_date": "2026-09-28"}
        assert (
            await api.post(f"/v1/sites/{SITE}/ga4", headers=headers, json=command)
        ).status_code == 200
        for change in (
            {"Cookie": ""},
            {"Origin": "https://evil.example.invalid"},
            {"X-CSRF-Token": "synthetic-wrong"},
        ):
            assert (
                await api.post(f"/v1/sites/{SITE}/ga4", headers={**headers, **change}, json=command)
            ).status_code == 403
        for change in (
            {"operation": "change_tracking"},
            {"access_token": "synthetic-token"},
            {"scope": "analytics.edit"},
        ):
            assert (
                await api.post(f"/v1/sites/{SITE}/ga4", headers=headers, json={**command, **change})
            ).status_code == 422
        assert (
            await api.post(
                f"/v1/sites/{SITE}/ga4",
                headers={**headers, "Content-Type": "application/json"},
                content=b"x" * 8193,
            )
        ).status_code == 422
        gateway.denied = True
        response = await api.get(f"/v1/sites/{SITE}/ga4", headers=headers)
        assert response.status_code == 403 and "synthetic-provider-secret" not in response.text
        response = await api.post(f"/v1/sites/{SITE}/ga4", headers=headers, json=command)
        assert response.status_code == 403 and "synthetic-provider-secret" not in response.text
    assert len(gateway.calls) == 1
