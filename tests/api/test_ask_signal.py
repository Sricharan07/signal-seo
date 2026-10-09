from uuid import uuid4

import httpx2 as httpx
import psycopg
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.ask_signal_service import AssistantConflict
from signal_core.authorization import AuthorizationDenied

ORIGIN = "https://dashboard.example.invalid"
TOKEN = "synthetic-" + "t" * 33
SITE, CONVERSATION, MEMORY = uuid4(), uuid4(), uuid4()


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Assistant:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    async def assistant(self, **kwargs):
        if self.error:
            raise self.error
        self.calls.append(kwargs)
        action = kwargs["action"]
        message = {
            "message_id": str(uuid4()),
            "role": "owner",
            "text": "Hello",
            "created_at": "2026-10-04T12:00:00+00:00",
            "state": "answered",
            "citations": [],
            "actions": [],
            "remembered": [],
        }
        memory = {
            "memory_id": str(MEMORY),
            "kind": "preference",
            "text": "I prefer short answers.",
            "created_at": message["created_at"],
            "source_conversation_id": None,
            "source_message_id": None,
        }
        if action == "create":
            return {"conversation_id": str(CONVERSATION), "created_at": message["created_at"]}
        if action == "read":
            return {"conversation_id": str(CONVERSATION), "title": None, "messages": [message]}
        if action == "message":
            return {
                "owner_message": message,
                "reply": {
                    **message,
                    "role": "signal",
                    "state": "unavailable",
                    "text": "Model not configured.",
                },
            }
        if action == "memory":
            return {"memories": [memory]}
        if action == "add_memory":
            return memory
        if action == "forget":
            return {"state": "forgotten"}
        return {
            "availability": "model_unconfigured",
            "reason": "Model not configured.",
            "suggestions": [],
            "conversations": [],
        }


PATHS = [
    ("/conversations", {"request_id": str(uuid4())}, "create", 201),
    (
        f"/conversations/{CONVERSATION}/messages",
        {"request_id": str(uuid4()), "text": "Hello"},
        "message",
        200,
    ),
    (
        "/memory",
        {"request_id": str(uuid4()), "kind": "preference", "text": "I prefer short answers."},
        "add_memory",
        201,
    ),
    (f"/memory/{MEMORY}/forget", {}, "forget", 200),
]


@pytest.mark.anyio
@pytest.mark.parametrize("path,body,action,status", PATHS)
async def test_every_mutation_requires_proof_and_strict_json(path, body, action, status):
    service = Assistant()
    security = BrowserSecurity(b"synthetic-assistant-browser-key-32", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_assistant=service,
        browser_security=security,
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        url = f"/v1/sites/{SITE}/assistant{path}"
        assert (await client.post(url, json={"schema_version": 1, **body})).status_code == 403
        assert (
            await client.post(
                url, json={"schema_version": 1, **body, "execute": True}, headers=headers
            )
        ).status_code == 422
        r = await client.post(url, json={"schema_version": 1, **body}, headers=headers)
        assert r.status_code == status
        assert r.headers["cache-control"] == "no-store"
        assert service.calls[0]["action"] == action
        assert len(service.calls) == 1
        expected = {
            "create": {"schema_version", "conversation_id", "created_at"},
            "message": {"schema_version", "owner_message", "reply"},
            "add_memory": {
                "schema_version",
                "memory_id",
                "kind",
                "text",
                "created_at",
                "source_conversation_id",
                "source_message_id",
            },
            "forget": {"schema_version", "state"},
        }
        assert set(r.json()) == expected[action]
        assert r.json()["schema_version"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path,action,keys",
    [
        (
            "",
            "overview",
            {"schema_version", "availability", "reason", "suggestions", "conversations"},
        ),
        (
            f"/conversations/{CONVERSATION}",
            "read",
            {"schema_version", "conversation_id", "title", "messages"},
        ),
        ("/memory", "memory", {"schema_version", "memories"}),
    ],
)
async def test_read_contracts_and_cookie_requirement(path, action, keys):
    service = Assistant()
    app = create_app(settings=ApiSettings(environment="test"), browser_assistant=service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        url = f"/v1/sites/{SITE}/assistant{path}"
        assert (await client.get(url)).status_code == 403
        result = await client.get(url, headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"})
        assert result.status_code == 200 and set(result.json()) == keys
        assert service.calls[0]["action"] == action
        if action == "read":
            assert result.json()["messages"][0]["created_at"].endswith("Z")
        if action == "memory":
            assert result.json()["memories"][0]["created_at"].endswith("Z")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error,status",
    [
        (AuthorizationDenied(), 403),
        (AssistantConflict(), 409),
        (psycopg.OperationalError("synthetic-database-down"), 503),
    ],
)
async def test_errors_and_database_unavailability(error, status):
    service = Assistant(error)
    app = create_app(settings=ApiSettings(environment="test"), browser_assistant=service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        r = await client.get(
            f"/v1/sites/{SITE}/assistant", headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"}
        )
        assert r.status_code == status and set(r.json()) == {"error"}


@pytest.mark.anyio
async def test_oversized_message_and_unconfigured_routes():
    security = BrowserSecurity(b"synthetic-assistant-browser-key-32", frozenset({ORIGIN}))
    service = Assistant()
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_assistant=service,
        browser_security=security,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        headers = {
            "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
            "Origin": ORIGIN,
            "Sec-Fetch-Site": "same-origin",
            CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
        }
        r = await client.post(
            f"/v1/sites/{SITE}/assistant/conversations/{CONVERSATION}/messages",
            json={"schema_version": 1, "request_id": str(uuid4()), "text": "x" * 2001},
            headers=headers,
        )
        assert r.status_code == 422 and not service.calls
    app = create_app(settings=ApiSettings(environment="test"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        assert (await client.get(f"/v1/sites/{SITE}/assistant", headers=headers)).status_code == 503
