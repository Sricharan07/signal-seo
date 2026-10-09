from dataclasses import dataclass, field
from uuid import uuid4

import httpx2
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app

TOKEN = "synthetic-" + "t" * 33
ORIGIN = "https://dashboard.example.invalid"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass
class Gateway:
    seen: list = field(default_factory=list)
    deny: bool = False
    fail: bool = False

    async def read(self, token, site):
        if self.deny:
            raise PermissionError
        if self.fail:
            raise RuntimeError("synthetic-secret-not-for-output")
        return {"channels": [], "history": []}

    async def control(self, token, site, command):
        if self.deny:
            raise PermissionError
        if self.fail:
            raise RuntimeError("synthetic-secret-not-for-output")
        self.seen.append((token, site, command.channel, command.enabled))
        return {"state": "enabled" if command.enabled else "disabled"}


def client(gateway):
    security = BrowserSecurity(
        csrf_hmac_key=b"synthetic-chat-report-csrf-key" * 3, allowed_origins=frozenset({ORIGIN})
    )
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_security=security,
        browser_chat_reports=gateway,
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://api.example.invalid"
    ), headers


@pytest.mark.anyio
async def test_chat_report_http_csrf_closed_command_and_no_send_endpoint():
    gateway = Gateway()
    browser, headers = client(gateway)
    site = uuid4()
    path = f"/v1/sites/{site}/chat-reports"
    async with browser:
        assert (await browser.get(path, headers=headers)).status_code == 200
        assert (
            await browser.post(path, headers=headers, json={"channel": "telegram", "enabled": True})
        ).json() == {"state": "enabled"}
        assert (
            await browser.post(
                path,
                headers={**headers, "Origin": "https://other.example.invalid"},
                json={"channel": "telegram", "enabled": False},
            )
        ).status_code == 403
        assert (
            await browser.post(
                path,
                headers={k: v for k, v in headers.items() if k != CSRF_HEADER_NAME},
                json={"channel": "telegram", "enabled": False},
            )
        ).status_code == 403
        assert (await browser.post(path + "/send", headers=headers, json={})).status_code == 404
    assert gateway.seen == [(TOKEN, site, "telegram", True)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"channel": "telegram", "enabled": "true"},
        {"channel": "telegram", "enabled": 1},
        {"channel": "group", "enabled": True},
        {"channel": "telegram", "enabled": True, "chat_id": "-133"},
        {"channel": "telegram", "enabled": True, "text": "x" * 1025},
    ],
)
async def test_chat_report_http_no_forged_destinations_or_raw_payload(body):
    gateway = Gateway()
    browser, headers = client(gateway)
    async with browser:
        response = await browser.post(
            f"/v1/sites/{uuid4()}/chat-reports", headers=headers, json=body
        )
    assert response.status_code == 422 and not gateway.seen


@pytest.mark.anyio
@pytest.mark.parametrize(
    "gateway,status", [(None, 503), (Gateway(deny=True), 403), (Gateway(fail=True), 503)]
)
async def test_chat_report_http_unconfigured_denied_and_failure_are_explicit(gateway, status):
    browser, headers = client(gateway)
    async with browser:
        response = await browser.get(f"/v1/sites/{uuid4()}/chat-reports", headers=headers)
    assert response.status_code == status and "synthetic-secret" not in response.text
