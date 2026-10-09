from dataclasses import replace
from uuid import uuid4

import httpx2
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.authorization import AuthorizationDenied
from signal_core.indexnow import IndexNowUnavailable
from test_candidate_inbox_http import StubCandidateInbox, item


class Gateway:
    key_creation_available = True

    def __init__(self, fail=False):
        self.fail = fail

    async def read(self, token, site):
        if self.fail:
            raise AuthorizationDenied()
        return {
            "key_status": "not_created",
            "key_id": None,
            "reason": "EC_142_KEY_NOT_CREATED",
            "submissions": [],
        }

    async def create(self, token, site, request):
        if self.fail:
            raise self.fail
        return {
            "schema_version": 1,
            "state": "sealed",
            "revision_id": request,
            "revision_sha256": "a" * 64,
        }


@pytest.mark.anyio
async def test_key_creation_requires_browser_proof_and_a_closed_bounded_request():
    token = "synthetic-" + "t" * 33
    origin = "https://dashboard.example.invalid"
    security = BrowserSecurity(
        csrf_hmac_key=b"synthetic-indexnow-browser-proof-key", allowed_origins=frozenset({origin})
    )
    gateway = Gateway()
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_indexnow=gateway,
        browser_security=security,
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={token}",
        "Origin": origin,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": security.issue_csrf_token(token),
    }
    body = {"schema_version": 1, "request_id": str(uuid4())}
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = f"/v1/sites/{uuid4()}/indexnow/key"
        response = await client.post(path, headers=headers, json=body)
        assert response.status_code == 200 and response.json()["state"] == "sealed"
        for changes in (
            {"Origin": "https://other.example.invalid"},
            {"X-CSRF-Token": "synthetic-invalid"},
            {"Cookie": ""},
        ):
            assert (
                await client.post(path, headers={**headers, **changes}, json=body)
            ).status_code == 403
        for command in (
            {**body, "direct_write": True},
            {**body, "request_id": "invalid"},
            {**body, "schema_version": 2},
        ):
            assert (await client.post(path, headers=headers, json=command)).status_code == 422
        assert (
            await client.post(
                path, headers={**headers, "Content-Type": "application/json"}, content=b"x" * 1025
            )
        ).status_code == 422
        for error, status, code in (
            (AuthorizationDenied(), 403, "INDEXNOW_AUTHORITY_DENIED"),
            (IndexNowUnavailable("INDEXNOW_STEP_UP_REQUIRED"), 403, "INDEXNOW_STEP_UP_REQUIRED"),
            (RuntimeError("synthetic-provider-secret"), 503, "INDEXNOW_UNAVAILABLE"),
            (IndexNowUnavailable("synthetic-provider-secret"), 503, "INDEXNOW_UNAVAILABLE"),
        ):
            gateway.fail = error
            response = await client.post(path, headers=headers, json=body)
            assert response.status_code == status and response.json() == {"code": code}


@pytest.mark.anyio
async def test_key_creation_is_unavailable_without_a_composed_gateway():
    security = BrowserSecurity(
        csrf_hmac_key=b"synthetic-indexnow-browser-proof-key",
        allowed_origins=frozenset({"https://dashboard.example.invalid"}),
    )
    app = create_app(settings=ApiSettings(environment="test"), browser_security=security)
    token = "synthetic-" + "t" * 33
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/v1/sites/{uuid4()}/indexnow/key",
            headers={
                "Cookie": f"{SESSION_COOKIE_NAME}={token}",
                "Origin": "https://dashboard.example.invalid",
                "Sec-Fetch-Site": "same-origin",
                "X-CSRF-Token": security.issue_csrf_token(token),
            },
            json={"schema_version": 1, "request_id": str(uuid4())},
        )
    assert response.status_code == 503


@pytest.mark.anyio
@pytest.mark.parametrize(
    "configured,token,denied,expected",
    [
        (False, True, False, 503),
        (True, False, False, 401),
        (True, True, False, 200),
        (True, True, True, 403),
    ],
)
async def test_indexnow_projection_config_and_identity_denials(configured, token, denied, expected):
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_indexnow=Gateway(denied) if configured else None,
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/v1/sites/{uuid4()}/indexnow",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={'t' * 43}"} if token else {},
        )
    assert response.status_code == expected
    if expected == 200:
        assert response.json()["submissions"] == []
        assert "key" not in response.json()


@pytest.mark.anyio
async def test_key_manifest_is_inspectable_in_existing_inbox():
    original = item()
    key_id = uuid4()
    manifest = {
        **original.manifest,
        "audit_report_id": None,
        "finding_id": str(key_id),
        "source_path": "synthetic-indexnow-key.txt",
        "result_sha256": "a" * 64,
        "patch": {"offset": 0, "before": "", "after": "synthetic-indexnow-key"},
        "evidence": {
            "key_id": str(key_id),
            "key_sha256": "a" * 64,
            "site_origin": "https://example.invalid",
            "page_url": "https://example.invalid/synthetic-indexnow-key.txt",
            "finding": {"id": str(key_id), "key": "indexnow.key.required"},
        },
    }
    gateway = StubCandidateInbox(replace(original, manifest=manifest))
    app = create_app(settings=ApiSettings(environment="test"), browser_candidate_inbox=gateway)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/v1/sites/{original.manifest['site_id']}/candidate-recipe-inbox",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={'t' * 43}"},
        )
    assert response.status_code == 200
    assert response.json()["revisions"][0]["manifest"]["patch"]["before"] == ""
