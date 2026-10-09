import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2
import pytest
import rfc8785
from signal_api.browser_security import SESSION_COOKIE_NAME
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.github_delivery_observation import (
    GitHubDeliveryObservation,
    GitHubObservationUnavailable,
)

SITE = UUID("11111111-1111-4111-8111-111111111111")
TOKEN = "t" * 43


@dataclass
class Gateway:
    values: tuple
    fail: bool = False

    async def github_delivery_observations(self, *, session_token, site_id):
        assert session_token == TOKEN and site_id == SITE
        if self.fail:
            raise GitHubObservationUnavailable("UNAVAILABLE")
        return self.values


def observation(*, invalid=False):
    attempt, operation, now = uuid4(), uuid4(), datetime.now(UTC)
    receipt = {
        "schema_version": 1,
        "site_id": str(SITE),
        "attempt_id": str(attempt),
        "operation_id": str(operation),
        "revision_sha256": "a" * 64,
        "environment": "production",
        "deployment_actor_id": 56,
        "provider": {"stage": "pr_opened"},
        "provider_evidence": [],
        "live": None,
        "live_egress_operation_id": None,
        "outcome": "inconclusive",
        "reason": "GITHUB_DELIVERY_UNAVAILABLE",
        "recovery_plan": "Inverse patch; preserve later edits.",
        "delivery_certified": False,
    }
    digest = hashlib.sha256(rfc8785.dumps(receipt)).hexdigest()
    return GitHubDeliveryObservation(
        attempt, operation, "completed", receipt, "0" * 64 if invalid else digest, now, now
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state,status",
    [("empty", 200), ("committed", 200), ("invalid", 500), ("failure", 503), ("no_session", 401)],
)
async def test_delivery_endpoint_only_exposes_exact_committed_read_evidence(state, status):
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_github_delivery=Gateway(
            () if state == "empty" else (observation(invalid=state == "invalid"),),
            fail=state == "failure",
        ),
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get(
            f"/v1/sites/{SITE}/github-delivery-observations",
            headers={} if state == "no_session" else {"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
        )
    assert result.status_code == status
    if state == "committed":
        record = result.json()["observations"][0]
        assert (
            hashlib.sha256(record["canonical_receipt"].encode()).hexdigest()
            == record["receipt_sha256"]
        )
    if state == "empty":
        assert result.json()["observations"] == []


@pytest.mark.anyio
async def test_delivery_endpoint_has_no_write_method():
    app = create_app(settings=ApiSettings(environment="test"), browser_github_delivery=Gateway(()))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post(f"/v1/sites/{SITE}/github-delivery-observations")
        ).status_code == 405
