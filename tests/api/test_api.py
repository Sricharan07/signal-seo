import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import httpx2 as httpx
import pytest
from signal_api.config import ApiSettings
from signal_api.main import create_app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@asynccontextmanager
async def api_client(
    *, ready: bool = False, docs: bool = True, raise_app_exceptions: bool = True
) -> AsyncIterator[httpx.AsyncClient]:
    async def probe() -> bool:
        return ready

    application = create_app(
        settings=ApiSettings(environment="test", expose_docs=docs), readiness_probe=probe
    )
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=raise_app_exceptions)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.anyio
async def test_liveness_is_truthful_and_has_security_headers():
    async with api_client() as api:
        response = await api.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "signal-api", "version": "0.0.0"}
    assert response.headers["cache-control"] == "no-store"
    assert (
        response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    )
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.anyio
async def test_readiness_fails_closed_until_probe_succeeds():
    async with api_client(ready=False) as api:
        blocked = await api.get("/health/ready")
    assert blocked.status_code == 503
    assert blocked.json()["error"] | {"correlation_id": "ignored"} == {
        "code": "DEPENDENCIES_NOT_READY",
        "message": "Required service dependencies are not ready.",
        "retryable": True,
        "correlation_id": "ignored",
    }
    async with api_client(ready=True) as api:
        ready = await api.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


@pytest.mark.anyio
async def test_capability_contract_never_claims_customer_or_write_authority():
    async with api_client() as api:
        response = await api.get("/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert body["release_status"] == "development"
    assert body["production_writes_enabled"] is False
    inventory = {item["key"]: item["availability"] for item in body["capabilities"]}
    assert len(inventory) == len(body["capabilities"])
    assert all(value == "disabled" for value in inventory.values())
    assert {
        "provider.gsc",
        "provider.github",
        "provider.bing",
        "provider.ga4",
        "provider.google_docs",
        "provider.wordpress",
        "provider.webflow",
        "provider.indexnow",
        "provider.email",
        "planning.content_writer",
        "operations.health",
        "evidence.pagespeed",
    } <= inventory.keys()
    assert not any("local_fixture" in key for key in inventory)


@pytest.mark.anyio
async def test_correlation_id_accepts_only_bounded_safe_tokens():
    async with api_client() as api:
        accepted = await api.get("/health/live", headers={"X-Correlation-ID": "web:request-123"})
        replaced = await api.get("/health/live", headers={"X-Correlation-ID": "bad value/secret"})
    assert accepted.headers["x-correlation-id"] == "web:request-123"
    assert UUID(replaced.headers["x-correlation-id"])


@pytest.mark.anyio
async def test_unknown_routes_and_methods_have_stable_safe_errors():
    async with api_client() as api:
        missing = await api.get("/v1/missing")
        method = await api.post("/v1/capabilities")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"
    assert method.status_code == 405
    assert method.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert "detail" not in missing.text


@pytest.mark.anyio
async def test_validation_errors_use_the_safe_contract():
    application = create_app(settings=ApiSettings(environment="test"))

    @application.get("/test/items/{item_id}")
    async def typed_path(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get("/test/items/not-an-integer")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert "not-an-integer" not in response.text
    assert "input" not in response.text


@pytest.mark.anyio
async def test_unhandled_exceptions_are_redacted_from_responses_and_logs(
    caplog: pytest.LogCaptureFixture,
):
    application = create_app(settings=ApiSettings(environment="test"))

    @application.get("/test/failure")
    async def failure() -> None:
        raise RuntimeError("sensitive internal value")

    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="signal.api"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
            response = await api.get("/test/failure")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "sensitive" not in response.text
    assert "Traceback" not in response.text
    assert "sensitive" not in caplog.text
    assert "Traceback" not in caplog.text
    assert "unhandled_request_failure" in caplog.text


@pytest.mark.anyio
async def test_openapi_contains_only_reviewed_bounded_mutations():
    async with api_client(docs=True) as api:
        schema = (await api.get("/openapi.json")).json()
    mutations = {
        (path, method)
        for path, operations in schema["paths"].items()
        for method in operations
        if method in {"post", "put", "patch", "delete"}
    }
    assert mutations == {
        ("/v1/session/logout", "post"),
        ("/v1/sites/{site_id}/slack", "post"),
        ("/v1/sites/{site_id}/chat-reports", "post"),
        ("/v1/sites/{site_id}/gsc", "post"),
        ("/v1/sites/{site_id}/bing", "post"),
        ("/v1/sites/{site_id}/github", "post"),
        ("/v1/sites/{site_id}/github-pr", "post"),
        ("/v1/sites/{site_id}/dataforseo", "post"),
        ("/v1/sites/{site_id}/indexnow/key", "post"),
        ("/v1/sites/{site_id}/ga4", "post"),
        ("/v1/sites/{site_id}/email", "post"),
        ("/v1/sites/{site_id}/wordpress", "post"),
        ("/v1/sites/{site_id}/google-docs", "post"),
        ("/v1/slack/{binding_id}/interactivity", "post"),
        ("/v1/sites/{site_id}/telegram", "post"),
        ("/v1/telegram/{binding_id}/webhook", "post"),
        ("/v1/session/site", "put"),
        ("/v1/session/switch-tenant", "post"),
        ("/v1/invitations/accept", "post"),
        ("/v1/sites/{site_id}/invitations", "post"),
        ("/v1/sites/{site_id}/invitations/{invitation_id}/revoke", "post"),
        ("/v1/sites", "post"),
        ("/v1/sites/{site_id}/standing-authorization", "post"),
        ("/v1/sites/{site_id}/ai-visibility/schedule", "post"),
        ("/v1/sites/{site_id}/standing-authorization/{grant_id}/revoke", "post"),
        ("/v1/sites/{site_id}/weekly-loop/pause", "post"),
        ("/v1/sites/{site_id}/weekly-loop/resume", "post"),
        ("/v1/sites/{site_id}/webflow/review", "post"),
        ("/v1/sites/{site_id}/webflow/begin", "post"),
        ("/v1/sites/{site_id}/webflow/complete", "post"),
        ("/v1/sites/{site_id}/webflow/seal", "post"),
        ("/v1/sites/{site_id}/webflow/revoke", "post"),
        ("/v1/sites/{site_id}/origin-challenges", "post"),
        ("/v1/sites/{site_id}/verify-origin", "post"),
        ("/v1/sites/{site_id}/brand-documents", "post"),
        ("/v1/sites/{site_id}/brand-documents/{document_id}", "delete"),
        ("/v1/sites/{site_id}/business-brain/facts", "post"),
        ("/v1/sites/{site_id}/business-brain/facts/{fact_id}/approve", "post"),
        ("/v1/sites/{site_id}/business-brain/facts/{fact_id}/correct", "post"),
        ("/v1/sites/{site_id}/business-brain/facts/{fact_id}/remove", "post"),
        ("/v1/sites/{site_id}/business-brain/voice", "put"),
        ("/v1/sites/{site_id}/business-brain/extraction", "post"),
        ("/v1/sites/{site_id}/content-writer/briefs", "post"),
        ("/v1/sites/{site_id}/content-writer/accept-brief", "post"),
        ("/v1/sites/{site_id}/ai-visibility/prepare", "post"),
        ("/v1/sites/{site_id}/ai-visibility/decide", "post"),
        ("/v1/sites/{site_id}/ai-visibility/questions-propose", "post"),
        ("/v1/sites/{site_id}/ai-visibility/questions-approve", "post"),
        ("/v1/sites/{site_id}/ai-visibility/seal", "post"),
        ("/v1/sites/{site_id}/content-writer/caps", "post"),
        ("/v1/sites/{site_id}/content-writer/drafts", "post"),
        ("/v1/sites/{site_id}/content-writer/candidates", "post"),
        ("/v1/sites/{site_id}/content-writer/review", "post"),
        ("/v1/sites/{site_id}/seo-strategy/refresh", "post"),
        ("/v1/sites/{site_id}/seo-strategy/decide", "post"),
        ("/v1/sites/{site_id}/seo-strategy/ideas", "post"),
        ("/v1/sites/{site_id}/assistant/conversations", "post"),
        ("/v1/sites/{site_id}/assistant/conversations/{conversation_id}/messages", "post"),
        ("/v1/sites/{site_id}/assistant/memory", "post"),
        ("/v1/sites/{site_id}/assistant/memory/{memory_id}/forget", "post"),
        ("/v1/sites/{site_id}/content-writer/approve-delivery", "post"),
        ("/v1/sites/{site_id}/commands/snapshot", "post"),
        ("/v1/sites/{site_id}/analysis/local-fixture", "post"),
        ("/v1/sites/{site_id}/analysis/verified-homepage", "post"),
        ("/v1/sites/{site_id}/proposals/local-fixture", "post"),
        ("/v1/sites/{site_id}/proposals/model-fixture", "post"),
        ("/v1/sites/{site_id}/proposals/verified-homepage", "post"),
        (
            "/v1/sites/{site_id}/approval-requests/{approval_request_id}/decision",
            "post",
        ),
        (
            "/v1/sites/{site_id}/candidate-recipe-revisions/{revision_id}/decision",
            "post",
        ),
    }


@pytest.mark.anyio
async def test_docs_are_disabled_by_default_and_forbidden_in_production():
    application = create_app(settings=ApiSettings())
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        assert (await api.get("/docs")).status_code == 404
        assert (await api.get("/openapi.json")).status_code == 404
    with pytest.raises(ValueError, match="disabled in production"):
        ApiSettings(environment="production", expose_docs=True)


def test_environment_configuration_is_strict(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "staging")
    with pytest.raises(ValueError, match="SIGNAL_ENVIRONMENT"):
        ApiSettings.from_environment()

    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "test")
    monkeypatch.setenv("SIGNAL_EXPOSE_API_DOCS", "yes")
    with pytest.raises(ValueError, match="SIGNAL_EXPOSE_API_DOCS"):
        ApiSettings.from_environment()
