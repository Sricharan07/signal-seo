"""Same-origin email opt-in has no verification mail, token, or send endpoint."""

from dataclasses import dataclass, field
from uuid import uuid4

import httpx2
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.oidc_protocol import _verified_email_claim

TOKEN = "synthetic-" + "t" * 33
ORIGIN = "https://dashboard.example.invalid"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass
class Gateway:
    seen: list = field(default_factory=list)
    deny: bool = False

    async def read(self, token, site):
        if self.deny:
            raise PermissionError
        return {
            "availability": "available",
            "address": "owner@example.invalid",
            "can_enable": True,
            "enabled": False,
            "last_delivery_state": None,
        }

    async def control(self, token, site, command):
        self.seen.append((token, site, command.enabled, command.address))
        if self.deny:
            raise PermissionError
        return {"state": "enabled" if command.enabled else "disabled"}


def _client(gateway):
    security = BrowserSecurity(
        csrf_hmac_key=b"synthetic-email-csrf-key" * 3, allowed_origins=frozenset({ORIGIN})
    )
    app = create_app(
        settings=ApiSettings(environment="test"), browser_security=security, browser_email=gateway
    )
    client = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://api.example.invalid"
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }
    return client, headers


@pytest.mark.anyio
async def test_email_unconfigured_is_visibly_unavailable():
    client, headers = _client(None)
    async with client:
        response = await client.get(f"/v1/sites/{uuid4()}/email", headers=headers)
    assert response.status_code == 503 and response.json() == {"availability": "unavailable"}


@pytest.mark.anyio
async def test_email_opt_in_is_exact_and_csrf_bound():
    gateway = Gateway()
    client, headers = _client(gateway)
    site = uuid4()
    async with client:
        accepted = await client.post(
            f"/v1/sites/{site}/email",
            headers=headers,
            json={"enabled": True, "address": "owner@example.invalid"},
        )
        read = await client.get(f"/v1/sites/{site}/email", headers=headers)
        cross = await client.post(
            f"/v1/sites/{site}/email",
            headers={**headers, "Origin": "https://other.example.invalid"},
            json={"enabled": True, "address": "owner@example.invalid"},
        )
        forged = await client.post(
            f"/v1/sites/{site}/email",
            headers=headers,
            json={
                "enabled": True,
                "address": "owner@example.invalid",
                "email_verified": True,
                "user_id": str(uuid4()),
            },
        )
        send = await client.post(f"/v1/sites/{site}/email/send", headers=headers, json={})
    assert accepted.json() == {"state": "enabled"}
    assert read.status_code == 200 and TOKEN not in read.text
    assert cross.status_code == 403 and forged.status_code == 422 and send.status_code == 404
    assert gateway.seen == [(TOKEN, site, True, "owner@example.invalid")]


@pytest.mark.anyio
async def test_email_wrong_user_or_role_has_no_control():
    client, headers = _client(Gateway(deny=True))
    async with client:
        response = await client.post(
            f"/v1/sites/{uuid4()}/email", headers=headers, json={"enabled": False, "address": None}
        )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "claims",
    [
        {"email": "owner@example.invalid", "email_verified": False},
        {"email": "owner@example.invalid"},
        {},
        {"email_verified": None, "email": "owner@example.invalid"},
    ],
)
def test_email_false_or_missing_identity_provider_verification_is_never_a_proof(claims):
    assert _verified_email_claim(claims) is None


def test_email_identity_provider_verified_address_is_normalized_exactly():
    assert (
        _verified_email_claim({"email": "Owner@Example.Invalid", "email_verified": True})
        == "owner@example.invalid"
    )


@pytest.mark.parametrize(
    "body", [{"enabled": "true"}, {"enabled": 1}, {"enabled": True, "address": "x" * 321}]
)
@pytest.mark.anyio
async def test_email_malformed_preference_is_not_interpreted(body):
    gateway = Gateway()
    client, headers = _client(gateway)
    async with client:
        response = await client.post(f"/v1/sites/{uuid4()}/email", headers=headers, json=body)
    assert response.status_code == 422 and not gateway.seen
