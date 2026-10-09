from uuid import uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.telegram_protocol import TelegramRejected

TOKEN = "synthetic-" + "t" * 33
BOT = "synthetic-telegram-bot-token-0092"
ORIGIN = "https://dashboard.example.test"
SITE, BINDING = uuid4(), uuid4()


class Gateway:
    def __init__(self):
        self.calls = []

    async def availability(self):
        return True

    async def read(self, token, site):
        assert token == TOKEN and site == SITE
        return {"availability": "unbound"}

    async def control(self, token, site, command):
        assert token == TOKEN and site == SITE
        assert BOT not in repr(command)
        self.calls.append(command)
        return {"binding_id": BINDING}

    async def interact(self, binding, body, secret):
        assert binding == BINDING
        self.calls.append((body, secret))
        raise TelegramRejected("TELEGRAM_SECRET_REJECTED")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_unconfigured_telegram_has_visible_disabled_contract():
    app = create_app(settings=ApiSettings(environment="test"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (await api.get(f"/v1/sites/{SITE}/telegram")).json()["availability"] == "unavailable"
        assert (await api.post(f"/v1/telegram/{BINDING}/webhook", json={})).status_code == 503
        capabilities = (await api.get("/v1/capabilities")).json()["capabilities"]
        assert (
            next(item for item in capabilities if item["key"] == "provider.telegram")[
                "availability"
            ]
            == "disabled"
        )


@pytest.mark.anyio
async def test_bot_secret_only_in_bounded_current_session_csrf_post(caplog):
    gateway = Gateway()
    security = BrowserSecurity(
        csrf_hmac_key=b"synthetic-telegram-browser-security-key-0092",
        allowed_origins=frozenset({ORIGIN}),
    )
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_telegram=gateway,
        browser_security=security,
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": security.issue_csrf_token(TOKEN),
    }
    command = {"operation": "install", "bot_token": BOT, "max_risk": 2}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.post(f"/v1/sites/{SITE}/telegram", headers=headers, json=command)
        assert response.status_code == 200 and BOT not in response.text
        for change in (
            {"Origin": "https://evil.invalid"},
            {"X-CSRF-Token": "x" * 43},
            {"Cookie": ""},
        ):
            assert (
                await api.post(
                    f"/v1/sites/{SITE}/telegram", headers={**headers, **change}, json=command
                )
            ).status_code == 403
        for change in (
            {"operation": "joinGroup"},
            {"bot_token": BOT + "/getUpdates"},
            {"max_risk": 3},
            {"extra": BOT},
        ):
            response = await api.post(
                f"/v1/sites/{SITE}/telegram", headers=headers, json={**command, **change}
            )
            assert response.status_code == 422 and BOT not in response.text
    assert BOT not in caplog.text and len(gateway.calls) == 1


@pytest.mark.anyio
async def test_ingress_missing_duplicate_secret_or_invalid_encoding_fails_closed():
    gateway = Gateway()
    app = create_app(settings=ApiSettings(environment="test"), browser_telegram=gateway)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (await api.post(f"/v1/telegram/{BINDING}/webhook", json={})).status_code == 401
        response = await api.post(
            f"/v1/telegram/{BINDING}/webhook",
            json={},
            headers=[
                ("x-telegram-bot-api-secret-token", "x" * 43),
                ("x-telegram-bot-api-secret-token", "y" * 43),
            ],
        )
        assert response.status_code == 401
        assert (
            await api.post(
                f"/v1/telegram/{BINDING}/webhook",
                content=b"x" * 32769,
                headers={
                    "content-type": "application/json",
                    "x-telegram-bot-api-secret-token": "x" * 43,
                },
            )
        ).status_code == 401
        assert (
            await api.post(
                f"/v1/telegram/{BINDING}/webhook",
                content=b"update=synthetic",
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
        ).status_code == 401
    assert gateway.calls == [(b"{}", ""), (b"{}", "")]
