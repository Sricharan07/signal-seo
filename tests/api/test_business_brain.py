from datetime import UTC, datetime
from uuid import uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.business_brain import BusinessBrainUnavailable
from signal_core.business_brain_extraction import ExtractionState

TOKEN = "t" * 43
ORIGIN = "https://dashboard.example.test"
SITE = uuid4()
FACT = uuid4()


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Brain:
    business_brain_configured = False

    def __init__(self, denied=False):
        self.denied = denied
        self.calls = []
        self.fact = {
            "fact_id": str(FACT),
            "category": "competitor",
            "statement": "Owner competitor.",
            "status": "proposed",
            "source_kind": "owner_statement",
            "page_evidence_id": None,
            "document_id": None,
            "extracted_range": None,
            "owner_membership_id": str(uuid4()),
            "sensitive": False,
            "supersedes_id": None,
            "created_at": datetime.now(UTC).isoformat(),
            "decision_id": None,
            "extraction_id": None,
        }

    async def read_business_brain(self, **kwargs):
        self.calls.append(kwargs)
        if self.denied:
            raise BusinessBrainUnavailable("owner_access_denied")
        return {"facts": [self.fact], "voice": None, "extractions": []}

    async def mutate_business_brain(self, **kwargs):
        self.calls.append(kwargs)
        if self.denied:
            raise BusinessBrainUnavailable("owner_access_denied")
        return {
            "approve": "approved",
            "correct": "corrected",
            "remove": "removed",
            "propose": "proposed",
            "voice": "recorded",
        }[kwargs["action"]]

    async def extract_business_brain(self, **kwargs):
        self.calls.append(kwargs)
        if self.denied:
            raise BusinessBrainUnavailable("owner_access_denied")
        return ExtractionState("unavailable", reason="MODEL_UNCONFIGURED")


def setup(brain):
    security = BrowserSecurity(b"synthetic-brain-browser-hmac-key!!", frozenset({ORIGIN}))
    app = create_app(
        settings=ApiSettings(environment="test"), browser_security=security, browser_brain=brain
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }
    return app, headers


@pytest.mark.anyio
async def test_owner_facts_voice_status_and_provenance():
    brain = Brain()
    app, headers = setup(brain)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        base = f"/v1/sites/{SITE}/business-brain"
        response = await client.get(base + "/facts?status=proposed", headers=headers)
        assert response.status_code == 200
        fact = response.json()["facts"][0]
        assert fact["category"] == "competitor"
        assert (await client.get(fact["provenance_url"], headers=headers)).status_code == 200
        assert (await client.get(base + "/approved-facts", headers=headers)).json()["facts"] == []
        assert (await client.get(base + "/voice", headers=headers)).json()["voice"] is None
        assert (await client.get(base + "/extraction", headers=headers)).json()[
            "state"
        ] == "unavailable"
        for action in ["approve", "correct", "remove"]:
            body = {
                "schema_version": 1,
                **({"statement": "Corrected"} if action == "correct" else {}),
            }
            assert (
                await client.post(base + f"/facts/{FACT}/{action}", headers=headers, json=body)
            ).status_code == 200
        assert (
            await client.post(
                base + "/facts",
                headers=headers,
                json={
                    "schema_version": 1,
                    "category": "competitor",
                    "statement": "Named competitor",
                },
            )
        ).status_code == 200
        assert (
            await client.put(
                base + "/voice",
                headers=headers,
                json={
                    "schema_version": 1,
                    "profile": {"tone": "Direct", "audience": "Owner", "guidelines": "Sources"},
                    "supersedes_id": None,
                },
            )
        ).status_code == 200
        assert (
            await client.post(
                base + "/extraction",
                headers=headers,
                json={
                    "schema_version": 1,
                    "source_kind": "page_evidence",
                    "source_id": str(uuid4()),
                    "extracted_range": None,
                },
            )
        ).json()["state"] == "unavailable"


MUTATIONS = [
    ("POST", f"facts/{FACT}/approve", {}),
    ("POST", f"facts/{FACT}/correct", {"statement": "Correction"}),
    ("POST", f"facts/{FACT}/remove", {}),
    ("POST", "facts", {"category": "competitor", "statement": "Competitor"}),
    (
        "PUT",
        "voice",
        {
            "profile": {"tone": "Direct", "audience": "Owner", "guidelines": "Sources"},
            "supersedes_id": None,
        },
    ),
    (
        "POST",
        "extraction",
        {"source_kind": "page_evidence", "source_id": str(uuid4()), "extracted_range": None},
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize("method,path,body", MUTATIONS)
async def test_every_mutation_denies_non_owner(method, path, body):
    app, headers = setup(Brain(denied=True))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.request(
            method,
            f"/v1/sites/{SITE}/business-brain/{path}",
            headers=headers,
            json={"schema_version": 1, **body},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "BRAIN_ACCESS_DENIED"


@pytest.mark.anyio
@pytest.mark.parametrize("method,path,body", MUTATIONS)
async def test_every_mutation_denies_csrf_and_extra_fields_before_port(method, path, body):
    brain = Brain()
    app, headers = setup(brain)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        url = f"/v1/sites/{SITE}/business-brain/{path}"
        response = await client.request(
            method, url, headers={"Cookie": headers["Cookie"]}, json={"schema_version": 1, **body}
        )
        assert response.status_code == 403
        response = await client.request(
            method,
            url,
            headers=headers,
            json={"schema_version": 1, **body, "authority": "approve_all"},
        )
        assert response.status_code == 422
    assert brain.calls == []


@pytest.mark.anyio
async def test_missing_gateway_and_invalid_status_are_visible():
    app, headers = setup(None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        assert (
            await client.get(f"/v1/sites/{SITE}/business-brain/facts", headers=headers)
        ).status_code == 503
        assert (
            await client.get(
                f"/v1/sites/{SITE}/business-brain/facts?status=trusted", headers=headers
            )
        ).status_code == 422
