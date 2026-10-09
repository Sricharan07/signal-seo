from uuid import uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.wordpress_protocol import WordPressUnavailable

TOKEN = "t" * 43
ORIGIN = "https://dashboard.example.invalid"
SITE = uuid4()


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Gateway:
    def __init__(self, denied=False):
        self.calls = []
        self.denied = denied

    async def read_wordpress(self, **values):
        if self.denied:
            raise WordPressUnavailable("owner_access_denied")
        return {"provider_state": "unavailable"}

    async def mutate_wordpress(self, **values):
        if self.denied:
            raise WordPressUnavailable("owner_access_denied")
        self.calls.append(values)
        return {"state": "queued"}


def command(operation):
    identifier = str(uuid4())
    return {
        "schema_version": 1,
        "command": {
            "operation": operation,
            **{
                "connect": {"binding_id": identifier, "origin": "https://cms.example.invalid"},
                "seal": {"binding_id": identifier, "draft_id": identifier},
                "review": {
                    "candidate_id": identifier,
                    "revision_sha256": "a" * 64,
                    "decision": "approved",
                },
                "create": {
                    "candidate_id": identifier,
                    "intent_id": identifier,
                    "prior_intent_id": None,
                },
                "reconcile": {"intent_id": identifier},
                "observe": {"intent_id": identifier},
                "revoke": {"binding_id": identifier},
            }[operation],
        },
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation", ["connect", "seal", "review", "create", "reconcile", "observe", "revoke"]
)
@pytest.mark.parametrize("denied", [False, True])
async def test_closed_owner_commands_and_csrf(operation, denied):
    port = Gateway(denied)
    security = BrowserSecurity(b"synthetic-wordpress-browser-key!!", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"), browser_security=security, browser_wordpress=port
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        path = f"/v1/sites/{SITE}/wordpress"
        assert (await client.post(path, json=command(operation))).status_code == 403
        assert (await client.post(path, json=command(operation), headers=headers)).status_code == (
            403 if denied else 200
        )
        assert (
            await client.post(
                path,
                json={**command(operation), "credential": "synthetic-password"},
                headers=headers,
            )
        ).status_code == 422
        for forbidden in ["publish", "update", "delete", "trash"]:
            assert (
                await client.post(
                    path,
                    json={"schema_version": 1, "command": {"operation": forbidden}},
                    headers=headers,
                )
            ).status_code == 422
    assert len(port.calls) == (0 if denied else 1)


@pytest.mark.anyio
async def test_unconfigured_and_errors_never_expose_provider_secrets():
    app = create_app(settings=ApiSettings(environment="test"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        result = await client.get(
            f"/v1/sites/{SITE}/wordpress", headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"}
        )
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "WORDPRESS_UNAVAILABLE"
