from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.audit_findings import AuditFinding, AuditNotReady, EvidenceConflict
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.page_observations import (
    OriginNotVerified,
    PageAnalysis,
    PageObservation,
    PageObservationConflict,
    PageObservationFailed,
    PageObservationNotReady,
)

TENANT_TOKEN = "t" * 43
ORIGIN = "https://dashboard.example.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def browser_security() -> BrowserSecurity:
    return BrowserSecurity(
        csrf_hmac_key=b"fixture-finding-browser-security",
        allowed_origins=frozenset({ORIGIN}),
    )


def finding(**overrides) -> AuditFinding:
    observed = datetime.now(UTC).replace(microsecond=0)
    values = {
        "id": uuid4(),
        "evidence_id": uuid4(),
        "command_id": uuid4(),
        "manifest_id": uuid4(),
        "finding_key": "metadata.meta_description.missing",
        "title": "Missing meta description",
        "summary": "The synthetic page fixture does not contain a non-empty meta description.",
        "resource_locator": "/fixture/missing-meta-description",
        "severity": "medium",
        "status": "open",
        "confidence_class": "deterministic",
        "source_kind": "synthetic_fixture",
        "source_identifier": "fixture:local-pilot/missing-meta-description/v1",
        "content_sha256": "a" * 64,
        "evidence_observed_at": observed,
        "first_seen_at": observed,
        "last_seen_at": observed,
        "reused": False,
        **overrides,
    }
    return AuditFinding(**values)


def page_observation(**overrides) -> PageObservation:
    observed = datetime.now(UTC).replace(microsecond=0)
    values = {
        "intent_id": uuid4(),
        "evidence_id": uuid4(),
        "finding_id": None,
        "command_id": uuid4(),
        "manifest_id": uuid4(),
        "origin": "https://example.com",
        "final_url": "https://example.com/",
        "http_status": 200,
        "media_type": "text/html",
        "title": "Example product",
        "heading": "Example heading",
        "meta_description": "A useful description.",
        "body_sha256": "b" * 64,
        "observed_at": observed,
        "reused": False,
        **overrides,
    }
    return PageObservation(**values)


@dataclass
class StubFindingGateway:
    current: tuple[AuditFinding, ...] = field(default_factory=lambda: (finding(),))
    error: Exception | None = None
    analyze_inputs: list[tuple[str, UUID]] = field(default_factory=list)
    read_inputs: list[tuple[str, UUID]] = field(default_factory=list)
    page_analysis: PageAnalysis = field(
        default_factory=lambda: PageAnalysis(page_observation(), None)
    )
    analyze_page_inputs: list[tuple[str, UUID, UUID]] = field(default_factory=list)
    latest_page_inputs: list[tuple[str, UUID]] = field(default_factory=list)

    async def analyze_local_fixture(self, *, session_token: str, site_id: UUID) -> AuditFinding:
        self.analyze_inputs.append((session_token, site_id))
        if self.error is not None:
            raise self.error
        return self.current[0]

    async def findings(self, *, session_token: str, site_id: UUID) -> tuple[AuditFinding, ...]:
        self.read_inputs.append((session_token, site_id))
        if self.error is not None:
            raise self.error
        return self.current

    async def analyze_verified_homepage(
        self,
        *,
        session_token: str,
        site_id: UUID,
        idempotency_key: UUID,
    ) -> PageAnalysis:
        self.analyze_page_inputs.append((session_token, site_id, idempotency_key))
        if self.error is not None:
            raise self.error
        return self.page_analysis

    async def latest_page_observation(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> PageObservation | None:
        self.latest_page_inputs.append((session_token, site_id))
        if self.error is not None:
            raise self.error
        return self.page_analysis.observation


def client_for(
    gateway: object | None,
    security: BrowserSecurity | None,
) -> httpx.AsyncClient:
    application = create_app(
        settings=ApiSettings(environment="test"),
        browser_security=security,
        browser_fixture_analysis=gateway,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="https://signal.example.test",
    )


def mutation_headers(security: BrowserSecurity) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}",
        CSRF_HEADER_NAME: security.issue_csrf_token(TENANT_TOKEN),
        "Content-Type": "application/json",
        "X-Correlation-ID": "fixture-request-1",
    }


@pytest.mark.anyio
async def test_fixture_analysis_returns_inspectable_bounded_finding(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    expected = finding()
    gateway = StubFindingGateway(current=(expected,))
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{site_id}/analysis/local-fixture",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )

    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == "fixture-request-1"
    payload = response.json()
    assert payload["site_id"] == str(site_id)
    assert payload["finding"]["finding_id"] == str(expected.id)
    assert payload["finding"]["source_kind"] == "synthetic_fixture"
    assert payload["finding"]["content_sha256"] == "a" * 64
    assert gateway.analyze_inputs == [(TENANT_TOKEN, site_id)]
    assert TENANT_TOKEN not in response.text


@pytest.mark.anyio
async def test_finding_read_returns_current_site_list_and_empty_list(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    expected = finding()
    gateway = StubFindingGateway(current=(expected,))
    headers = {"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"}
    async with client_for(gateway, browser_security) as api:
        response = await api.get(f"/v1/sites/{site_id}/findings", headers=headers)
    assert response.status_code == 200
    assert response.json()["findings"][0]["evidence_id"] == str(expected.evidence_id)
    assert gateway.read_inputs == [(TENANT_TOKEN, site_id)]

    empty = StubFindingGateway(current=())
    async with client_for(empty, browser_security) as api:
        response = await api.get(f"/v1/sites/{site_id}/findings", headers=headers)
    assert response.status_code == 200
    assert response.json()["findings"] == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (AuditNotReady(), 409, "AUDIT_NOT_READY"),
        (EvidenceConflict(), 409, "EVIDENCE_CONFLICT"),
        (AuthorizationDenied(), 403, "SITE_COMMAND_DENIED"),
        (InvalidSession(), 401, "TENANT_SESSION_REQUIRED"),
    ],
)
async def test_fixture_analysis_maps_closed_failure_states(
    browser_security: BrowserSecurity,
    error: Exception,
    status: int,
    code: str,
):
    gateway = StubFindingGateway(error=error)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/analysis/local-fixture",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


@pytest.mark.anyio
async def test_fixture_routes_fail_closed_without_explicit_gateway(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    async with client_for(None, browser_security) as api:
        write = await api.post(
            f"/v1/sites/{site_id}/analysis/local-fixture",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )
        read = await api.get(
            f"/v1/sites/{site_id}/findings",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    for response in (write, read):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b'{"schema_version":2}',
        b'{"schema_version":1,"extra":true}',
        b'"not-an-object"',
        b"\xff",
        b" " * 257,
    ],
)
async def test_fixture_analysis_body_is_strict_before_gateway(
    browser_security: BrowserSecurity,
    body: bytes,
):
    gateway = StubFindingGateway()
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/analysis/local-fixture",
            headers=mutation_headers(browser_security),
            content=body,
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_FIXTURE_ANALYSIS"
    assert gateway.analyze_inputs == []


@pytest.mark.anyio
async def test_fixture_analysis_rejects_cross_origin_before_gateway(
    browser_security: BrowserSecurity,
):
    gateway = StubFindingGateway()
    headers = mutation_headers(browser_security)
    headers["Origin"] = "https://attacker.example"
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/analysis/local-fixture",
            headers=headers,
            json={"schema_version": 1},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "BROWSER_REQUEST_REJECTED"
    assert gateway.analyze_inputs == []


@pytest.mark.anyio
async def test_verified_homepage_analysis_and_latest_read_are_inspectable(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    request_id = uuid4()
    observed = page_observation(finding_id=uuid4(), meta_description=None)
    verified_finding = finding(
        id=observed.finding_id,
        evidence_id=observed.evidence_id,
        command_id=observed.command_id,
        manifest_id=observed.manifest_id,
        summary="The verified homepage does not contain a non-empty meta description.",
        resource_locator="https://example.com/",
        source_kind="verified_origin",
        source_identifier="https://example.com/",
        content_sha256=observed.body_sha256,
    )
    gateway = StubFindingGateway(
        current=(verified_finding,),
        page_analysis=PageAnalysis(observed, verified_finding),
    )
    async with client_for(gateway, browser_security) as api:
        write = await api.post(
            f"/v1/sites/{site_id}/analysis/verified-homepage",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1, "idempotency_key": str(request_id)},
        )
        read = await api.get(
            f"/v1/sites/{site_id}/observations/homepage/latest",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )

    assert write.status_code == 200
    assert write.json()["observation"]["final_url"] == "https://example.com/"
    assert write.json()["finding"]["source_kind"] == "verified_origin"
    assert read.status_code == 200
    assert read.json()["observation"]["evidence_id"] == str(observed.evidence_id)
    assert gateway.analyze_page_inputs == [(TENANT_TOKEN, site_id, request_id)]
    assert gateway.latest_page_inputs == [(TENANT_TOKEN, site_id)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (OriginNotVerified(), 409, "ORIGIN_NOT_VERIFIED"),
        (PageObservationNotReady(), 409, "AUDIT_NOT_READY"),
        (PageObservationConflict(), 409, "PAGE_OBSERVATION_CONFLICT"),
        (PageObservationFailed("transport_unavailable"), 502, "PAGE_OBSERVATION_FAILED"),
        (AuthorizationDenied(), 403, "SITE_COMMAND_DENIED"),
        (InvalidSession(), 401, "TENANT_SESSION_REQUIRED"),
    ],
)
async def test_verified_homepage_analysis_maps_closed_failures(
    browser_security: BrowserSecurity,
    error: Exception,
    status: int,
    code: str,
):
    gateway = StubFindingGateway(error=error)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/analysis/verified-homepage",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1, "idempotency_key": str(uuid4())},
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


@pytest.mark.anyio
async def test_verified_homepage_analysis_rejects_invalid_body_before_gateway(
    browser_security: BrowserSecurity,
):
    gateway = StubFindingGateway()
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/analysis/verified-homepage",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1, "idempotency_key": "not-a-uuid"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PAGE_ANALYSIS"
    assert gateway.analyze_page_inputs == []
