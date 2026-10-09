"""Exact owner standing-grant HTTP boundaries; provider execution stays absent."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.standing_authorization import (
    RevokedGrant,
    StandingAuthorizationUnavailable,
    StandingGrant,
    StandingGrantRequest,
    StandingGrantView,
)

TOKEN = "synthetic-" + "t" * 33
ORIGIN = "https://dashboard.example.test"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def security():
    return BrowserSecurity(
        csrf_hmac_key=b"synthetic-standing-grant-csrf-key" * 2,
        allowed_origins=frozenset({ORIGIN}),
    )


@dataclass
class StubStandingGateway:
    grant_result: StandingGrant = field(default_factory=lambda: StandingGrant(uuid4(), (uuid4(),)))
    revoke_result: RevokedGrant = field(
        default_factory=lambda: RevokedGrant(uuid4(), "AUTHORITY_DURABILITY_PENDING")
    )
    read_result: StandingGrantView | None = None
    error: Exception | None = None
    seen: list[object] = field(default_factory=list)

    async def grant_standing_authorization(
        self, *, session_token: str, request: StandingGrantRequest
    ) -> StandingGrant:
        self.seen.append((session_token, request))
        if self.error:
            raise self.error
        return self.grant_result

    async def revoke_standing_authorization(
        self, *, session_token: str, site_id: UUID, grant_id: UUID
    ) -> RevokedGrant:
        self.seen.append((session_token, site_id, grant_id))
        if self.error:
            raise self.error
        return self.revoke_result

    async def read_standing_authorization(
        self, *, session_token: str, site_id: UUID
    ) -> StandingGrantView:
        self.seen.append((session_token, site_id))
        if self.error:
            raise self.error
        return self.read_result or StandingGrantView(
            "no_grant", None, (), (), {}, {}, None, None, (), None, None, None, None, None
        )


def headers(security: BrowserSecurity):
    return {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }


def grant_body():
    now = datetime.now(UTC)
    return {
        "schema_version": 1,
        "recipe_ranges": [
            {
                "key": "title_description_improvement",
                "minimum_inclusive": "1.0.0",
                "maximum_exclusive": "2.0.0",
            }
        ],
        "thresholds": {"draft_patch": 0.9},
        "weekly_volume_caps": {"draft_patch": 2},
        "weekly_total_cap": 2,
        "weekly_spend_cents": 0,
        "excluded_paths": ["/private"],
        "starts_at": now.isoformat(),
        "ends_at": (now + timedelta(days=7)).isoformat(),
        "recovery_window_hours": 24,
    }


def client(gateway: StubStandingGateway, security: BrowserSecurity):
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_login=gateway,
        browser_security=security,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://signal.example.test",
    )


@pytest.mark.anyio
async def test_grant_records_exact_owner_request_and_revokes_pending(security):
    site_id, grant_id = uuid4(), uuid4()
    gateway = StubStandingGateway(grant_result=StandingGrant(grant_id, (uuid4(),)))
    async with client(gateway, security) as api:
        granted = await api.post(
            f"/v1/sites/{site_id}/standing-authorization",
            headers=headers(security),
            json=grant_body(),
        )
        revoked = await api.post(
            f"/v1/sites/{site_id}/standing-authorization/{grant_id}/revoke",
            headers=headers(security),
            json={"schema_version": 1},
        )
    assert granted.status_code == 201
    assert granted.json()["grant_id"] == str(grant_id)
    assert granted.json()["state"] == "recorded"
    assert gateway.seen[0][0] == TOKEN
    assert gateway.seen[0][1].thresholds == {"draft_patch": 0.9}
    assert revoked.status_code == 202
    assert revoked.json()["durability"] == "AUTHORITY_DURABILITY_PENDING"


@pytest.mark.anyio
async def test_invalid_or_cross_origin_grant_never_reaches_gateway(security):
    gateway = StubStandingGateway()
    site_id = uuid4()
    async with client(gateway, security) as api:
        invalid = await api.post(
            f"/v1/sites/{site_id}/standing-authorization",
            headers=headers(security),
            json={**grant_body(), "thresholds": {"canonical_change": 0.9}},
        )
        cross = await api.post(
            f"/v1/sites/{site_id}/standing-authorization",
            headers={**headers(security), "Origin": "https://other.example.test"},
            json=grant_body(),
        )
    assert invalid.status_code == 422
    assert cross.status_code == 403
    assert gateway.seen == []


@pytest.mark.anyio
async def test_role_removal_and_unavailable_grant_are_explicit(security):
    gateway = StubStandingGateway(error=StandingAuthorizationUnavailable("permission_denied"))
    site_id = uuid4()
    async with client(gateway, security) as api:
        response = await api.get(
            f"/v1/sites/{site_id}/standing-authorization",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SITE_COMMAND_DENIED"
