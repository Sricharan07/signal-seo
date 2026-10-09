import httpx2
import pytest
from signal_api.config import ApiSettings
from signal_api.main import create_app


class OptionalChat:
    def __init__(self, outcome):
        self.outcome = outcome

    async def availability(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.anyio
@pytest.mark.parametrize("chat_state", [False, True, RuntimeError("synthetic-unavailable")])
async def test_inventory_uses_composed_gateways_and_failed_probes_stay_disabled(chat_state):
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_ga4=object(),
        browser_indexnow=object(),
        browser_slack=OptionalChat(chat_state),
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = (await client.get("/v1/capabilities")).json()
    states = {entry["key"]: entry["availability"] for entry in result["capabilities"]}
    assert states["provider.ga4"] == states["provider.indexnow"] == "internal_only"
    assert states["provider.wordpress"] == states["provider.webflow"] == "disabled"
    assert states["provider.slack"] == ("internal_only" if chat_state is True else "disabled")
    assert states["provider.production_writes"] == states["customer.authentication"] == "disabled"
    assert result["production_writes_enabled"] is False
