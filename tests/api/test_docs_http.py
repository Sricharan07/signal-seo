from uuid import uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.docs_protocol import NOTION_UNAVAILABLE_REASON, DocsRejected

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
        if token != TOKEN or site != SITE:
            raise DocsRejected("DOCS_OWNER_DENIED")
        return {"availability": "unbound", "sources": []}

    async def control(self, token, site, command):
        assert token == TOKEN and site == SITE
        self.calls.append(command)
        return {"binding_id": uuid4()}


@pytest.mark.anyio
async def test_docs_unconfigured_and_notion_deferred_without_any_bindable_route():
    app = create_app(settings=ApiSettings(environment="test"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (await api.get(f"/v1/sites/{SITE}/google-docs")).status_code == 503
        notion = await api.get(f"/v1/sites/{SITE}/notion")
        assert notion.status_code == 503 and notion.json() == {
            "availability": "unavailable",
            "reason": NOTION_UNAVAILABLE_REASON,
        }
        assert (
            await api.post(f"/v1/sites/{SITE}/notion", json={"operation": "connect"})
        ).status_code == 405
        assert (await api.get(f"/v1/sites/{SITE}/notion/callback")).status_code == 404


@pytest.mark.anyio
async def test_docs_cookie_csrf_strict_command_and_bounded_fixed_errors():
    gateway = Gateway()
    security = BrowserSecurity(
        csrf_hmac_key=b"synthetic-docs-browser-security-key-0100",
        allowed_origins=frozenset({ORIGIN}),
    )
    app = create_app(
        settings=ApiSettings(environment="test"), browser_docs=gateway, browser_security=security
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": security.issue_csrf_token(TOKEN),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (await api.get(f"/v1/sites/{SITE}/google-docs", headers=headers)).json() == {
            "availability": "unbound",
            "sources": [],
        }
        assert (
            await api.get(f"/v1/sites/{uuid4()}/google-docs", headers=headers)
        ).status_code == 403
        assert (
            await api.post(
                f"/v1/sites/{SITE}/google-docs", headers=headers, json={"operation": "sync"}
            )
        ).status_code == 200
        for change in (
            {"Origin": "https://evil.example.invalid"},
            {"X-CSRF-Token": "x" * 43},
            {"Cookie": ""},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            assert (
                await api.post(
                    f"/v1/sites/{SITE}/google-docs",
                    headers={**headers, **change},
                    json={"operation": "sync"},
                )
            ).status_code == 403
        for bad in (
            {"operation": "write"},
            {"operation": "sync", "access_token": "synthetic-sensitive-token"},
            {"operation": "disconnect", "binding_id": "bad"},
        ):
            result = await api.post(f"/v1/sites/{SITE}/google-docs", headers=headers, json=bad)
            assert result.status_code == 422 and "synthetic-sensitive-token" not in result.text
        result = await api.post(
            f"/v1/sites/{SITE}/google-docs",
            headers={**headers, "Content-Type": "application/json"},
            content=b"x" * 12289,
        )
        assert result.status_code == 409
    assert len(gateway.calls) == 1
