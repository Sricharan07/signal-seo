from uuid import uuid4

import httpx2
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.webflow import WebflowUnavailable

ORIGIN = "https://dashboard.example.invalid"
TOKEN = "t" * 43
SITE = uuid4()


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Inbox:
    def __init__(self, denied=False):
        self.calls, self.denied = [], denied

    async def read_webflow(self, **kwargs):
        if self.denied:
            raise WebflowUnavailable("WEBFLOW_OWNER_ACCESS_OR_SOURCE_UNAVAILABLE")
        return {"bindings": [], "inbox": []}

    async def review_webflow(self, **kwargs):
        if self.denied:
            raise WebflowUnavailable("WEBFLOW_OWNER_ACCESS_OR_SOURCE_UNAVAILABLE")
        self.calls.append(kwargs)
        return {"state": "reviewed"}


@pytest.mark.anyio
@pytest.mark.parametrize("denied", [False, True])
async def test_current_owner_inbox_and_csrf_protected_exact_review(denied):
    port = Inbox(denied)
    security = BrowserSecurity(b"synthetic-webflow-browser-key-0098", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"), browser_security=security, browser_writer=port
    )
    payload = {
        "schema_version": 1,
        "id": str(uuid4()),
        "revision_sha256": "a" * 64,
        "decision": "approved",
    }
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        read = await client.get(f"/v1/sites/{SITE}/webflow", headers=headers)
        assert read.status_code == (403 if denied else 200)
        if not denied:
            assert "ATOMIC_PRECONDITION_UNAVAILABLE" in read.json()["capabilities"]["publish"]
        assert (
            await client.post(f"/v1/sites/{SITE}/webflow/review", json=payload)
        ).status_code == 403
        assert (
            await client.post(
                f"/v1/sites/{SITE}/webflow/review",
                json={**payload, "publish": True},
                headers=headers,
            )
        ).status_code == 422
        response = await client.post(
            f"/v1/sites/{SITE}/webflow/review", json=payload, headers=headers
        )
        assert response.status_code == (403 if denied else 200)
        for forbidden in ("publish", "update", "delete", "deliver"):
            assert (
                await client.post(
                    f"/v1/sites/{SITE}/webflow/{forbidden}", json=payload, headers=headers
                )
            ).status_code == 404
    assert len(port.calls) == (0 if denied else 1)


@pytest.mark.anyio
async def test_unconfigured_webflow_is_unavailable_not_ready():
    app = create_app(settings=ApiSettings(environment="test"))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        response = await client.get(
            f"/v1/sites/{SITE}/webflow", headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"}
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "WEBFLOW_CONNECTOR_UNCONFIGURED"


class Owner(Inbox):
    async def webflow_options(self, **kwargs):
        return {"drafts": [], "articles": []}

    async def mutate_webflow(self, **kwargs):
        if self.denied:
            raise WebflowUnavailable(self.denied)
        self.calls.append(kwargs)
        if kwargs["operation"] == "revoke":
            return {
                "state": "AUTHORITY_DURABILITY_PENDING",
                "event_id": str(uuid4()),
                "upstream": "OUTCOME_UNKNOWN",
            }
        return {"state": "sealed", "id": str(uuid4())}


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["begin", "complete", "seal", "revoke"])
@pytest.mark.parametrize(
    "denied", [False, "WEBFLOW_OWNER_ACCESS_OR_SOURCE_UNAVAILABLE", "WEBFLOW_STEP_UP_REQUIRED"]
)
async def test_owner_mutations_are_closed_and_browser_protected(operation, denied):
    port = Owner(denied)
    security = BrowserSecurity(b"synthetic-webflow-browser-key-0156", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"), browser_security=security, browser_webflow=port
    )
    identifier = str(uuid4())
    fields = {
        "begin": {"provider_site": "a" * 24, "collection_id": "b" * 24},
        "complete": {
            "attempt_id": identifier,
            "state": "s" * 43,
            "code": "synthetic-code",
            "field_mapping": {"title": "name", "description": "description", "body": "body"},
        },
        "seal": {"binding_id": identifier, "candidate_id": identifier, "source_sha256": "a" * 64},
        "revoke": {"binding_id": identifier},
    }
    payload = {"schema_version": 1, **fields[operation]}
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }
    path = f"/v1/sites/{SITE}/webflow/{operation}"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        assert (await client.post(path, json=payload)).status_code == 403
        for key, value in [
            ("Origin", "https://evil.example.invalid"),
            (CSRF_HEADER_NAME, "x" * 43),
            ("Sec-Fetch-Site", "cross-site"),
        ]:
            assert (
                await client.post(path, json=payload, headers={**headers, key: value})
            ).status_code == 403
        assert (
            await client.post(
                path, json={**payload, "access_token": "synthetic-token"}, headers=headers
            )
        ).status_code == 422
        result = await client.post(path, json=payload, headers=headers)
        assert result.status_code == (403 if denied else 200)
        if not denied and operation == "revoke":
            assert result.json()["state"] == "AUTHORITY_DURABILITY_PENDING"
    assert len(port.calls) == (0 if denied else 1)


@pytest.mark.anyio
async def test_owner_failures_do_not_disclose_credentials():
    class Broken(Owner):
        async def mutate_webflow(self, **kwargs):
            raise RuntimeError("synthetic-secret-never-returned")

    security = BrowserSecurity(b"synthetic-webflow-browser-key-0156", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_security=security,
        browser_webflow=Broken(),
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        result = await client.post(
            f"/v1/sites/{SITE}/webflow/begin",
            json={"schema_version": 1, "provider_site": "a" * 24, "collection_id": "b" * 24},
            headers=headers,
        )
        assert result.status_code == 503
        assert "synthetic-secret" not in result.text
