from uuid import uuid4

import httpx2
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.pagespeed_collection import PageSpeedUnavailable


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Gateway:
    def __init__(self, denied=False, malformed=False):
        self.denied, self.malformed = denied, malformed
        self.calls = []

    async def read_pagespeed(self, **values):
        self.calls.append(values)
        if self.denied:
            raise PageSpeedUnavailable("PSI_ACCESS_DENIED")
        return {
            "outcome": "found",
            "samples": [],
            "daily_request_cap": 4,
            "weekly_page_cap": 5,
            "local_lighthouse": "available" if self.malformed else "unavailable",
        }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "status"), [("ready", 200), ("denied", 403), ("unconfigured", 503), ("malformed", 503)]
)
async def test_owner_read_only_performance_contract(mode, status):
    gateway = None if mode == "unconfigured" else Gateway(mode == "denied", mode == "malformed")
    app = create_app(settings=ApiSettings(environment="test"), browser_pagespeed=gateway)
    site = uuid4()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://dashboard.example.invalid"
    ) as client:
        response = await client.get(
            f"/v1/sites/{site}/performance",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}=" + "s" * 43},
        )
        assert response.status_code == status
        if status == 200:
            assert response.json()["local_lighthouse"] == "unavailable"
            assert response.json()["site_id"] == str(site)
        assert (await client.post(f"/v1/sites/{site}/performance", json={})).status_code == 405


@pytest.mark.anyio
async def test_missing_or_duplicate_session_does_not_reach_gateway():
    gateway = Gateway()
    app = create_app(settings=ApiSettings(environment="test"), browser_pagespeed=gateway)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://dashboard.example.invalid"
    ) as client:
        for cookie in (None, f"{SESSION_COOKIE_NAME}=a; {SESSION_COOKIE_NAME}=b"):
            response = await client.get(
                f"/v1/sites/{uuid4()}/performance", headers={"Cookie": cookie} if cookie else {}
            )
            assert response.status_code == 403
    assert not gateway.calls
