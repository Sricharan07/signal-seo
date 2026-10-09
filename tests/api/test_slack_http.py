from uuid import uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.slack_protocol import SlackRejected

TOKEN = "synthetic-" + "t" * 33
ORIGIN = "https://dashboard.example.test"
SITE = uuid4()
BINDING = uuid4()


class Gateway:
    def __init__(self):
        self.calls = []
        self.ready = True

    async def availability(self):
        return self.ready

    async def read(self, token, site):
        assert token == TOKEN and site == SITE
        return {"availability": "unbound"}

    async def control(self, token, site, command):
        assert token == TOKEN and site == SITE
        self.calls.append(command)
        return {"state": "queued", "outbox_id": uuid4()}

    async def interact(self, binding, body, timestamp, signature):
        assert binding == BINDING
        self.calls.append((body, timestamp, signature))
        raise SlackRejected("SLACK_SIGNATURE_REJECTED")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_unconfigured_slack_is_explicitly_unavailable():
    app = create_app(settings=ApiSettings(environment="test"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (await api.get(f"/v1/sites/{SITE}/slack")).json()["availability"] == "unavailable"
        response = await api.post(f"/v1/slack/{BINDING}/interactivity", content=b"payload=x")
    assert response.status_code == 503


@pytest.mark.anyio
async def test_slack_setup_requires_current_cookie_csrf_and_strict_command():
    gateway = Gateway()
    security = BrowserSecurity(
        csrf_hmac_key=b"synthetic-slack-browser-security-key-0091",
        allowed_origins=frozenset({ORIGIN}),
    )
    app = create_app(
        settings=ApiSettings(environment="test"), browser_slack=gateway, browser_security=security
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": security.issue_csrf_token(TOKEN),
    }
    command = {"operation": "link", "binding_id": str(BINDING), "slack_user_id": "U00000001"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (await api.get(f"/v1/sites/{SITE}/slack", headers=headers)).json() == {
            "availability": "unbound"
        }
        assert (
            await api.post(f"/v1/sites/{SITE}/slack", headers=headers, json=command)
        ).status_code == 200
        for changes in (
            {"Origin": "https://evil.invalid"},
            {"X-CSRF-Token": "x" * 43},
            {"Cookie": ""},
        ):
            response = await api.post(
                f"/v1/sites/{SITE}/slack", headers={**headers, **changes}, json=command
            )
            assert response.status_code == 403
        for changes in (
            {"token": "synthetic-hidden"},
            {"slack_user_id": "other"},
            {"operation": "history"},
        ):
            assert (
                await api.post(
                    f"/v1/sites/{SITE}/slack", headers=headers, json={**command, **changes}
                )
            ).status_code == 422
    assert len(gateway.calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body,extra",
    [
        (b'{"operation":"link","operation":"revoke"}', {}),
        (b"{}", {"Content-Encoding": "gzip"}),
        (b"x" * 4097, {}),
    ],
)
async def test_slack_rejects_duplicate_encoded_and_oversize_commands(body, extra):
    gateway = Gateway()
    security = BrowserSecurity(b"synthetic-slack-browser-security-key-0091", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"), browser_slack=gateway, browser_security=security
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": security.issue_csrf_token(TOKEN),
        "Content-Type": "application/json",
        **extra,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (
            await api.post(f"/v1/sites/{SITE}/slack", headers=headers, content=body)
        ).status_code == 422
    assert not gateway.calls


@pytest.mark.anyio
async def test_slack_ingress_does_not_use_browser_authority_or_response_url():
    gateway = Gateway()
    app = create_app(settings=ApiSettings(environment="test"), browser_slack=gateway)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Signature": "v0=" + "0" * 64,
        "X-Slack-Request-Timestamp": "1000",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.post(
            f"/v1/slack/{BINDING}/interactivity", headers=headers, content=b"payload=synthetic"
        )
        assert response.status_code == 401
        assert (
            await api.post(
                f"/v1/slack/{BINDING}/interactivity", headers=headers, content=b"x" * 32769
            )
        ).status_code == 401
        assert (
            await api.post(
                f"/v1/slack/{BINDING}/interactivity",
                headers={**headers, "Content-Type": "application/json"},
                json={},
            )
        ).status_code == 401
    assert len(gateway.calls) == 1
