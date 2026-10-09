from uuid import uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.ai_visibility_schedule import VisibilityScheduleUnavailable

SITE = uuid4()
TOKEN = "synthetic-" + "t" * 33
ORIGIN = "https://dashboard.example.test"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Gateway:
    def __init__(self, *, denied=False):
        self.denied = denied
        self.calls = []

    async def read_visibility_schedule(self, **kwargs):
        self.calls.append(kwargs)
        if self.denied or kwargs["site_id"] != SITE:
            raise VisibilityScheduleUnavailable("owner_access_denied")
        return dict(
            schema_version=1,
            site_id=str(SITE),
            settings_id=None,
            cadence_days=7,
            monthly_cap_micros=1_000_000,
            enabled=False,
            held_micros=0,
            spent_micros=None,
            currency="USD",
            month_start="2026-10-01",
            cap_reached=False,
            authority_current=False,
            runtime_state="unavailable",
            runs=[],
        )

    async def update_visibility_schedule(self, **kwargs):
        self.calls.append(kwargs)
        return await self.read_visibility_schedule(
            session_token=kwargs["session_token"], site_id=kwargs["site_id"]
        )


def setup(gateway):
    security = BrowserSecurity(b"synthetic-visibility-hmac-key0000", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_security=security,
        browser_visibility=gateway,
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }
    return app, headers


@pytest.mark.anyio
async def test_owner_read_update_and_unavailable_runtime_projection():
    gateway = Gateway()
    app, headers = setup(gateway)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        path = f"/v1/sites/{SITE}/ai-visibility/schedule"
        result = await client.get(path, headers=headers)
        assert result.status_code == 200 and result.json()["runtime_state"] == "unavailable"
        assert result.headers["cache-control"] == "no-store"
        body = dict(
            schema_version=1,
            request_id=str(uuid4()),
            cadence_days=7,
            monthly_cap_micros=1_000_000,
            enabled=True,
        )
        assert (await client.post(path, headers=headers, json=body)).status_code == 200
        assert gateway.calls[-2]["values"]["monthly_cap_micros"] == 1_000_000
        assert (
            await client.get(f"/v1/sites/{uuid4()}/ai-visibility/schedule", headers=headers)
        ).status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad",
    [
        {"cadence_days": 0},
        {"cadence_days": 31},
        {"cadence_days": True},
        {"monthly_cap_micros": 1.5},
        {"monthly_cap_micros": -1},
        {"enabled": "true"},
        {"extra": "no"},
    ],
)
async def test_strict_request_and_csrf_role_failures(bad):
    gateway = Gateway()
    app, headers = setup(gateway)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        path = f"/v1/sites/{SITE}/ai-visibility/schedule"
        body = dict(
            schema_version=1,
            request_id=str(uuid4()),
            cadence_days=7,
            monthly_cap_micros=1_000_000,
            enabled=True,
        )
        assert (await client.post(path, headers=headers, json={**body, **bad})).status_code == 422
        assert not gateway.calls
        assert (
            await client.post(
                path, headers={**headers, "Origin": "https://offsite.test"}, json=body
            )
        ).status_code == 403
        assert not gateway.calls
        gateway.denied = True
        assert (await client.post(path, headers=headers, json=body)).status_code == 403


@pytest.mark.anyio
async def test_absent_gateway_is_explicitly_unavailable():
    app, headers = setup(None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        assert (
            await client.get(f"/v1/sites/{SITE}/ai-visibility/schedule", headers=headers)
        ).status_code == 503
