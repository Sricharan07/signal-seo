"""Public denial ignores forwarded addresses; owner routes stay authenticated."""

from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from signal_api.main import create_app


def test_public_health_closed_even_with_forged_forwarding():
    with TestClient(create_app(), client=("192.0.2.10", 1000)) as client:
        assert client.get("/health", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 404


def test_private_health_unknown_not_ok():
    with TestClient(create_app(), client=("127.0.0.1", 1000)) as client:
        response = client.get("/health")
        assert response.status_code == 503 and response.json()["state"] == "unknown"


def test_owner_missing_cookie_role_failure_and_storage_failure():
    class Gateway:
        async def read(self, token, site):
            raise PermissionError

    app = create_app(browser_health=Gateway())
    with TestClient(app) as client:
        assert client.get(f"/v1/sites/{uuid4()}/health").status_code == 403
        assert (
            client.get(
                f"/v1/sites/{uuid4()}/health", cookies={"signal_tenant_session": "A" * 43}
            ).status_code
            == 403
        )


def test_public_caddy_health_denial_is_preserved():
    from pathlib import Path

    source = Path("deploy/integration-test/application/Caddyfile").read_text()
    assert "@private path /identity* /admin* /metrics* /health* /api* /v1*" in source
    assert "handle @private {\n    respond 404" in source


def test_private_checks_dont_turn_unknown_into_success():
    from datetime import UTC, datetime

    from signal_core.health_monitoring import evaluate

    monitor = SimpleNamespace(latest=(evaluate("temporal", None, datetime.now(UTC)),))
    with TestClient(create_app(health_monitor=monitor), client=("127.0.0.1", 1000)) as client:
        assert client.get("/health").status_code == 503
