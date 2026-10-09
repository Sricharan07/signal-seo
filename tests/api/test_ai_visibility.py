from dataclasses import replace
from uuid import uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.content_writer import ContentWriterUnavailable

ORIGIN = "https://dashboard.example.test"
SITE = uuid4()
TOKEN = "t" * 43


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Visibility:
    def __init__(self, denied=False):
        self.denied = denied
        self.calls = []

    async def read_ai_visibility(self, **kwargs):
        if self.denied:
            raise ContentWriterUnavailable("owner_access_denied")
        self.calls.append(kwargs)
        return {"state": "available", "gaps": [], "proposals": []}

    async def mutate_ai_visibility(self, **kwargs):
        if self.denied:
            raise ContentWriterUnavailable("owner_access_denied")
        self.calls.append(kwargs)
        return {"state": "prepared" if kwargs["action"] == "prepare" else "accepted"}


def body(action):
    if action == "questions-approve":
        return {
            "schema_version": 1,
            "request_id": str(uuid4()),
            "crawl_manifest_id": str(uuid4()),
            "supersedes_id": None,
            "questions": ["What do our buyers need?"],
        }
    if action == "seal":
        return {
            "schema_version": 1,
            "proposal_id": str(uuid4()),
            "digest": "a" * 64,
            "fact_fields": {"headline": str(uuid4())},
        }
    return {
        "schema_version": 1,
        **(
            {"proposal_id": str(uuid4()), "digest": "a" * 64, "decision": "accepted"}
            if action == "decide"
            else {}
        ),
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "action", ["prepare", "decide", "questions-propose", "questions-approve", "seal"]
)
@pytest.mark.parametrize("denied", [False, True])
async def test_owner_commands_and_denial(action, denied):
    port = Visibility(denied)
    security = BrowserSecurity(b"synthetic-visibility-browser-hmac", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"), browser_security=security, browser_visibility=port
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(
            f"/v1/sites/{SITE}/ai-visibility/{action}",
            json=body(action),
            headers={
                "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
                "Origin": ORIGIN,
                "Sec-Fetch-Site": "same-origin",
                CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
            },
        )
    assert response.status_code == (403 if denied else 200)
    assert len(port.calls) == (0 if denied else 1)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "action", ["prepare", "decide", "questions-propose", "questions-approve", "seal"]
)
async def test_closed_schema_csrf_and_no_observation_or_publish_port(action):
    port = Visibility()
    security = BrowserSecurity(b"synthetic-visibility-browser-hmac", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"), browser_security=security, browser_visibility=port
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        path = f"/v1/sites/{SITE}/ai-visibility/{action}"
        assert (await client.post(path, json=body(action))).status_code == 403
        assert (
            await client.post(path, json={**body(action), "publish": True}, headers=headers)
        ).status_code == 422
        assert (
            await client.post(
                f"/v1/sites/{SITE}/ai-visibility/schedule", json=body(action), headers=headers
            )
        ).status_code == 422
        for endpoint in ("observe", "publish", "candidates"):
            assert (
                await client.post(
                    f"/v1/sites/{SITE}/ai-visibility/{endpoint}", json=body(action), headers=headers
                )
            ).status_code == 404
    assert not port.calls


@pytest.mark.anyio
async def test_unconfigured_and_non_owner_reads():
    for service, expected in [(None, 503), (Visibility(True), 403), (Visibility(), 200)]:
        app = create_app(settings=ApiSettings(environment="test"), browser_visibility=service)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            result = await client.get(
                f"/v1/sites/{SITE}/ai-visibility",
                headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
            )
        assert result.status_code == expected


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["read", "prepare", "decide"])
async def test_composition_reuses_writer_database_role_and_closes(action, monkeypatch):
    from signal_api.authentication import ComposedBrowserLogin
    from signal_core import ai_visibility_agent
    from test_authentication import FakeConnection, composed_gateway

    connection = FakeConnection()
    gateway = replace(
        composed_gateway(lambda: (_ for _ in ()).throw(AssertionError("identity role"))),
        writer_connection_factory=lambda: connection,
    )

    async def generation(self):
        return "synthetic-visibility-generation"

    monkeypatch.setattr(ComposedBrowserLogin, "_current_generation", generation)
    captured = []

    def call(conn, **kwargs):
        assert conn is connection
        captured.append(kwargs)
        return {"state": "available"}

    monkeypatch.setattr(
        ai_visibility_agent,
        {"read": "read_agent", "prepare": "prepare_proposals", "decide": "decide_proposal"}[action],
        call,
    )
    if action == "read":
        await gateway.read_ai_visibility(session_token=TOKEN, site_id=SITE)
    else:
        await gateway.mutate_ai_visibility(
            session_token=TOKEN,
            site_id=SITE,
            action=action,
            values={k: v for k, v in body(action).items() if k != "schema_version"},
        )
    assert captured and connection.closed


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [False, True])
async def test_sealing_composition_reuses_existing_candidate_ports_and_closes_on_failure(
    monkeypatch, failure
):
    from contextlib import contextmanager

    from signal_api.authentication import ComposedBrowserLogin
    from signal_core import ai_visibility_recipe
    from signal_core.content_writer_service import ContentWriterService
    from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable
    from test_authentication import FakeConnection, composed_gateway

    connections = []

    def writer():
        connection = FakeConnection()
        connections.append(connection)
        return connection

    identity = FakeConnection()

    @contextmanager
    def candidates():
        try:
            yield identity
        finally:
            identity.close()

    service = ContentWriterService(
        None,
        None,
        None,
        None,
        None,
        github_credential=object(),
        github_transport=object(),
        runner=object(),
        candidate_connection_factory=candidates,
    )
    gateway = replace(
        composed_gateway(lambda: pytest.fail("wrong database role")),
        writer_connection_factory=writer,
        writer_service=service,
    )

    async def generation(self):
        return "synthetic-generation"

    monkeypatch.setattr(ComposedBrowserLogin, "_current_generation", generation)
    monkeypatch.setattr(
        ai_visibility_recipe, "structured_context", lambda *a, **k: {"state": "available"}
    )
    calls = []

    async def seal(connection, identity_connection, **kwargs):
        assert connection is connections[-1] and identity_connection is identity
        assert (
            kwargs["runner"] is service.runner and kwargs["credential"] is service.github_credential
        )
        calls.append(kwargs)
        if failure:
            raise TechnicalRecipeUnavailable("RECIPE_BUILD_FAILED")
        return {"state": "sealed", "revision_id": str(uuid4()), "revision_sha256": "a" * 64}

    monkeypatch.setattr(ai_visibility_recipe, "seal_visibility_recipe", seal)
    command = {k: v for k, v in body("seal").items() if k != "schema_version"}
    if failure:
        with pytest.raises(TechnicalRecipeUnavailable):
            await gateway.mutate_ai_visibility(
                session_token=TOKEN, site_id=SITE, action="seal", values=command
            )
    else:
        assert (
            await gateway.mutate_ai_visibility(
                session_token=TOKEN, site_id=SITE, action="seal", values=command
            )
        )["state"] == "sealed"
    assert len(calls) == 1 and identity.closed and all(c.closed for c in connections)


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["questions-propose", "questions-approve", "seal"])
async def test_new_actions_report_safe_dependency_failure_without_success(action):
    from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

    class Failed(Visibility):
        async def mutate_ai_visibility(self, **kwargs):
            raise TechnicalRecipeUnavailable("RECIPE_BUILD_FAILED")

    security = BrowserSecurity(b"synthetic-visibility-browser-hmac", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_security=security,
        browser_visibility=Failed(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.post(
            f"/v1/sites/{SITE}/ai-visibility/{action}",
            json=body(action),
            headers={
                "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
                "Origin": ORIGIN,
                "Sec-Fetch-Site": "same-origin",
                CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
            },
        )
    assert (
        response.status_code == 503
        and "sealed" not in response.text
        and "RECIPE_BUILD_FAILED" not in response.text
    )
