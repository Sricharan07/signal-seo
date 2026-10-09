from uuid import uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.authorization import AuthorizationDenied
from signal_core.seo_strategy_service import StrategyConflict, StrategyUnavailable

ORIGIN = "https://dashboard.example.invalid"
TOKEN = "synthetic-" + "t" * 33
SITE = uuid4()


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Strategy:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def read_seo_strategy(self, **kwargs):
        if self.error:
            raise self.error
        self.calls.append(kwargs)
        return {"snapshot": None, "decisions": []}

    async def mutate_seo_strategy(self, **kwargs):
        if self.error:
            raise self.error
        self.calls.append(kwargs)
        return {"state": "recorded", "snapshot_id": str(uuid4())}


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["refresh", "ideas"])
@pytest.mark.parametrize(
    "error,status",
    [
        (None, 200),
        (AuthorizationDenied(), 403),
        (StrategyUnavailable(), 503),
        (StrategyConflict(), 409),
    ],
)
async def test_strategy_existing_authority_success_negative_and_failure(error, status, action):
    service = Strategy(error)
    security = BrowserSecurity(b"synthetic-strategy-browser-hmac!", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_strategy=service,
        browser_security=security,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        read = await client.get(
            f"/v1/sites/{SITE}/seo-strategy", headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"}
        )
        assert read.status_code == status
        response = await client.post(
            f"/v1/sites/{SITE}/seo-strategy/{action}",
            json={
                "schema_version": 1,
                **({"snapshot_id": str(uuid4())} if action == "ideas" else {}),
            },
            headers={
                "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
                "Origin": ORIGIN,
                "Sec-Fetch-Site": "same-origin",
                CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
            },
        )
        assert response.status_code == status


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["refresh", "ideas"])
async def test_no_csrf_model_candidates_and_unknown_fields_cannot_reach_port(action):
    service = Strategy()
    security = BrowserSecurity(b"synthetic-strategy-browser-hmac!", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_strategy=service,
        browser_security=security,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        url = f"/v1/sites/{SITE}/seo-strategy/{action}"
        assert (await client.post(url, json={"schema_version": 1})).status_code == 403
        headers = {
            "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
            "Origin": ORIGIN,
            "Sec-Fetch-Site": "same-origin",
            CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
        }
        assert (
            await client.post(
                url, json={"schema_version": 1, "model_items": [{"ship": True}]}, headers=headers
            )
        ).status_code == 422
        assert not service.calls


@pytest.mark.anyio
async def test_unconfigured_read_is_visibly_unavailable_and_evidence_requires_exact_snapshot():
    app = create_app(settings=ApiSettings(environment="test"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        assert (await client.get(f"/v1/sites/{SITE}/seo-strategy")).status_code == 503
    service = Strategy()
    app = create_app(settings=ApiSettings(environment="test"), browser_strategy=service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        sid, eid = uuid4(), uuid4()
        assert (
            await client.get(
                f"/v1/sites/{SITE}/seo-strategy/{sid}/evidence/{eid}",
                headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
            )
        ).status_code == 404
        assert service.calls[0]["snapshot_id"] == sid
