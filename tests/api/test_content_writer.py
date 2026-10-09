from dataclasses import replace
from uuid import uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.content_writer import ContentWriterUnavailable

TOKEN = "t" * 43
SITE = uuid4()
ORIGIN = "https://dashboard.example.test"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Writer:
    content_writer_configured = False
    content_candidate_configured = False

    def __init__(self, denied=False):
        self.denied = denied
        self.calls = []

    async def read_content_writer(self, **kwargs):
        if self.denied:
            raise ContentWriterUnavailable("owner_access_denied")
        return {
            "cap": 2,
            "used": 0,
            "cap_reached": False,
            "platform_maximum": 5,
            "briefs": [],
            "drafts": [],
            "candidates": [],
        }

    async def mutate_content_writer(self, **kwargs):
        if self.denied:
            raise ContentWriterUnavailable("owner_access_denied")
        self.calls.append(kwargs)
        return {
            "state": "unavailable" if kwargs["action"] in {"drafts", "candidates"} else "created"
        }


def body(action):
    bid = str(uuid4())
    return {
        "schema_version": 1,
        **{
            "briefs": {
                "payload": {
                    "topic": "Topic",
                    "query": "owner query",
                    "intent": "informational",
                    "kind": "new_article",
                    "source_ids": [bid],
                    "fact_ids": [bid],
                    "internal_links": [],
                },
                "proposal": False,
                "supersedes_id": None,
            },
            "accept-brief": {"brief_id": bid},
            "caps": {"cap": 2},
            "drafts": {"brief_id": bid},
            "candidates": {"draft_id": bid, "extension_id": bid, "destination": "new.html"},
            "review": {"candidate_id": bid, "revision_sha256": "a" * 64, "decision": "approved"},
            "approve-delivery": {
                "candidate_id": bid,
                "revision_sha256": "a" * 64,
                "acknowledged_sentences": [],
            },
        }[action],
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "action",
    ["briefs", "accept-brief", "caps", "drafts", "candidates", "review", "approve-delivery"],
)
@pytest.mark.parametrize("denied", [False, True])
async def test_owner_routes_and_non_owner_negatives(action, denied):
    writer = Writer(denied)
    security = BrowserSecurity(b"synthetic-writer-browser-hmac-key!", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"), browser_security=security, browser_writer=writer
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(
            f"/v1/sites/{SITE}/content-writer/{action}",
            json=body(action),
            headers={
                "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
                "Origin": ORIGIN,
                "Sec-Fetch-Site": "same-origin",
                CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
            },
        )
    assert response.status_code == (403 if denied else 200)
    assert len(writer.calls) == (0 if denied else 1)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "action",
    ["briefs", "accept-brief", "caps", "drafts", "candidates", "review", "approve-delivery"],
)
async def test_csrf_and_closed_mutations(action):
    writer = Writer()
    security = BrowserSecurity(b"synthetic-writer-browser-hmac-key!", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"), browser_security=security, browser_writer=writer
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        path = f"/v1/sites/{SITE}/content-writer/{action}"
        assert (await client.post(path, json=body(action))).status_code == 403
        response = await client.post(
            path,
            json={**body(action), "publish": True},
            headers={
                "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
                "Origin": ORIGIN,
                "Sec-Fetch-Site": "same-origin",
                CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
            },
        )
        assert response.status_code == 422
    assert not writer.calls


@pytest.mark.anyio
async def test_unavailable_model_and_current_owner_read():
    writer = Writer()
    app = create_app(settings=ApiSettings(environment="test"), browser_writer=writer)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get(
            f"/v1/sites/{SITE}/content-writer", headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"}
        )
        assert response.json()["model_state"] == "unavailable"
        writer.denied = True
        assert (
            await client.get(
                f"/v1/sites/{SITE}/content-writer",
                headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
            )
        ).status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize(
    "action", ["briefs", "accept-brief", "caps", "drafts", "review", "approve-delivery"]
)
async def test_composed_gateway_routes_to_existing_api_role_and_closes(action, monkeypatch):
    from signal_api.authentication import ComposedBrowserLogin
    from signal_core import content_writer_service
    from test_authentication import FakeConnection, composed_gateway

    connection = FakeConnection()
    captured = []
    gateway = replace(
        composed_gateway(lambda: (_ for _ in ()).throw(AssertionError("identity role"))),
        writer_connection_factory=lambda: connection,
    )

    async def generation(self):
        return "synthetic-writer-generation"

    monkeypatch.setattr(ComposedBrowserLogin, "_current_generation", generation)

    def call(conn, *args, **kwargs):
        assert conn is connection
        name = kwargs.get("name") or args[3]
        captured.append(name)
        return {
            "read": {},
            "accept_brief": "accepted",
            "set_cap": "updated",
            "review": "reviewed",
            "approve_delivery": "approved",
        }[name]

    def brief(conn, **kwargs):
        assert conn is connection
        captured.append("brief")
        return {"state": "created"}

    monkeypatch.setattr(content_writer_service, "writer_call", call)
    monkeypatch.setattr(content_writer_service, "create_brief", brief)
    values = {k: v for k, v in body(action).items() if k != "schema_version"}
    result = await gateway.mutate_content_writer(
        session_token=TOKEN, site_id=SITE, action=action, values=values
    )
    assert result["state"] in {
        "created",
        "accepted",
        "updated",
        "reviewed",
        "unavailable",
        "approved",
    }
    assert captured and connection.closed


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["foreign_scope", "build_unavailable"])
async def test_existing_candidate_boundaries_return_closed_errors(failure):
    from signal_core.authorization import AuthorizationDenied
    from signal_core.candidate_build_service import CandidateBuildUnavailable

    class FailedWriter(Writer):
        async def mutate_content_writer(self, **kwargs):
            if failure == "foreign_scope":
                raise AuthorizationDenied()
            raise CandidateBuildUnavailable("synthetic-build-failure")

    security = BrowserSecurity(b"synthetic-writer-browser-hmac-key!", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_security=security,
        browser_writer=FailedWriter(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(
            f"/v1/sites/{SITE}/content-writer/candidates",
            json=body("candidates"),
            headers={
                "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
                "Origin": ORIGIN,
                "Sec-Fetch-Site": "same-origin",
                CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
            },
        )
    assert response.status_code == (403 if failure == "foreign_scope" else 503)
    assert "synthetic-build-failure" not in response.text
