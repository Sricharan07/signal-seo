from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.commands import (
    AcceptedHumanCommand,
    CommandNotFound,
    HumanCommandStatus,
    IdempotencyConflict,
)
from signal_core.workflow_contracts import CrawlManifestReference

TENANT_TOKEN = "t" * 43
ORIGIN = "https://dashboard.example.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def browser_security() -> BrowserSecurity:
    return BrowserSecurity(
        csrf_hmac_key=b"human-command-browser-security!!",
        allowed_origins=frozenset({ORIGIN}),
    )


def accepted_command(**overrides) -> AcceptedHumanCommand:
    values = {
        "id": uuid4(),
        "reused": False,
        "status": "accepted",
        "accepted_at": datetime.now(UTC).replace(microsecond=0),
        **overrides,
    }
    return AcceptedHumanCommand(**values)


def command_status(command: AcceptedHumanCommand, **overrides) -> HumanCommandStatus:
    values = {
        "id": command.id,
        "actor_user_id": uuid4(),
        "kind": "site.snapshot",
        "status": "accepted",
        "accepted_at": command.accepted_at,
        **overrides,
    }
    return HumanCommandStatus(**values)


@dataclass
class StubCommandGateway:
    acceptance: object = field(default_factory=accepted_command)
    status: object | None = None
    acceptance_error: Exception | None = None
    status_error: Exception | None = None
    acceptance_inputs: list[tuple[str, UUID, str]] = field(default_factory=list)
    status_inputs: list[tuple[str, UUID, UUID]] = field(default_factory=list)
    latest_inputs: list[tuple[str, UUID]] = field(default_factory=list)

    async def accept_snapshot_command(
        self,
        *,
        session_token: str,
        site_id: UUID,
        idempotency_key: str,
    ) -> AcceptedHumanCommand:
        self.acceptance_inputs.append((session_token, site_id, idempotency_key))
        if self.acceptance_error is not None:
            raise self.acceptance_error
        return self.acceptance

    async def snapshot_command_status(
        self,
        *,
        session_token: str,
        site_id: UUID,
        command_id: UUID,
    ) -> HumanCommandStatus:
        self.status_inputs.append((session_token, site_id, command_id))
        if self.status_error is not None:
            raise self.status_error
        if self.status is not None:
            return self.status
        assert isinstance(self.acceptance, AcceptedHumanCommand)
        return command_status(self.acceptance)

    async def latest_snapshot_command(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> HumanCommandStatus:
        self.latest_inputs.append((session_token, site_id))
        if self.status_error is not None:
            raise self.status_error
        if self.status is not None:
            return self.status
        assert isinstance(self.acceptance, AcceptedHumanCommand)
        return command_status(self.acceptance)


def client_for(
    gateway: object | None,
    security: BrowserSecurity | None,
) -> httpx.AsyncClient:
    application = create_app(
        settings=ApiSettings(environment="test"),
        browser_login=gateway,
        browser_security=security,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="https://signal.example.test",
    )


def mutation_headers(
    security: BrowserSecurity,
    *,
    idempotency_key: str = "snapshot-request-1",
) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}",
        CSRF_HEADER_NAME: security.issue_csrf_token(TENANT_TOKEN),
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
        "X-Correlation-ID": "command-request-1",
    }


@pytest.mark.anyio
async def test_snapshot_acceptance_returns_durable_202_contract(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    expected = accepted_command()
    gateway = StubCommandGateway(acceptance=expected)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{site_id}/commands/snapshot",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )
    status_url = f"/v1/sites/{site_id}/commands/{expected.id}"
    assert response.status_code == 202
    assert response.headers["location"] == status_url
    assert response.headers["x-correlation-id"] == "command-request-1"
    assert response.json() == {
        "schema_version": 1,
        "command_id": str(expected.id),
        "site_id": str(site_id),
        "status": "accepted",
        "status_url": status_url,
        "reused": False,
        "accepted_at": expected.accepted_at.isoformat().replace("+00:00", "Z"),
        "correlation_id": "command-request-1",
    }
    assert gateway.acceptance_inputs == [(TENANT_TOKEN, site_id, "snapshot-request-1")]
    assert TENANT_TOKEN not in response.text
    assert "snapshot-request-1" not in response.text


@pytest.mark.anyio
async def test_exact_retry_is_transparently_returned_as_reused(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    expected = accepted_command(reused=True)
    gateway = StubCommandGateway(acceptance=expected)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{site_id}/commands/snapshot",
            headers=mutation_headers(browser_security, idempotency_key="retry-key"),
            json={"schema_version": 1},
        )
    assert response.status_code == 202
    assert response.json()["reused"] is True
    assert response.json()["status"] == "accepted"


@pytest.mark.anyio
async def test_command_status_requires_session_and_returns_bounded_projection(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    accepted = accepted_command()
    expected = command_status(accepted)
    gateway = StubCommandGateway(acceptance=accepted, status=expected)
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/sites/{site_id}/commands/{accepted.id}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    assert response.status_code == 200
    assert response.json() | {"correlation_id": "ignored"} == {
        "schema_version": 1,
        "command_id": str(accepted.id),
        "site_id": str(site_id),
        "actor_user_id": str(expected.actor_user_id),
        "kind": "site.snapshot",
        "status": "accepted",
        "accepted_at": expected.accepted_at.isoformat().replace("+00:00", "Z"),
        "correlation_id": "ignored",
    }
    assert gateway.status_inputs == [(TENANT_TOKEN, site_id, accepted.id)]


@pytest.mark.anyio
async def test_latest_command_status_returns_current_authorized_projection(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    accepted = accepted_command()
    expected = command_status(accepted)
    gateway = StubCommandGateway(acceptance=accepted, status=expected)
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/sites/{site_id}/commands/latest",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )

    assert response.status_code == 200
    assert response.json()["command_id"] == str(accepted.id)
    assert response.json()["status"] == "accepted"
    assert gateway.latest_inputs == [(TENANT_TOKEN, site_id)]


@pytest.mark.anyio
async def test_latest_command_status_hides_missing_or_unauthorized_work(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    for error, expected_status in [
        (CommandNotFound(), 404),
        (AuthorizationDenied(), 403),
        (InvalidSession(), 401),
    ]:
        gateway = StubCommandGateway(status_error=error)
        async with client_for(gateway, browser_security) as api:
            response = await api.get(
                f"/v1/sites/{site_id}/commands/latest",
                headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
            )
        assert response.status_code == expected_status


@pytest.mark.anyio
async def test_command_status_exposes_complete_workflow_admission_progress(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    accepted = accepted_command()
    projected_at = datetime.now(UTC).replace(microsecond=0)
    expected = command_status(
        accepted,
        status="workflow_admitted",
        workflow_id=f"signal:CrawlSite:{uuid4()}:{accepted.id}",
        workflow_type="CrawlSite",
        workflow_state="admitted",
        projected_at=projected_at,
    )
    gateway = StubCommandGateway(acceptance=accepted, status=expected)
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/sites/{site_id}/commands/{accepted.id}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "workflow_admitted"
    assert response.json()["workflow_id"] == expected.workflow_id
    assert response.json()["workflow_type"] == "CrawlSite"
    assert response.json()["workflow_state"] == "admitted"
    assert response.json()["projected_at"] == projected_at.isoformat().replace("+00:00", "Z")


@pytest.mark.anyio
async def test_command_status_exposes_complete_temporal_start_progress(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    accepted = accepted_command()
    projected_at = datetime.now(UTC).replace(microsecond=0)
    expected = command_status(
        accepted,
        status="processing",
        workflow_id=f"signal:CrawlSite:{uuid4()}:{accepted.id}",
        workflow_type="CrawlSite",
        first_run_id=str(uuid4()),
        workflow_state="running",
        projected_at=projected_at,
    )
    gateway = StubCommandGateway(acceptance=accepted, status=expected)
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/sites/{site_id}/commands/{accepted.id}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "processing"
    assert response.json()["workflow_id"] == expected.workflow_id
    assert response.json()["workflow_type"] == "CrawlSite"
    assert response.json()["first_run_id"] == expected.first_run_id
    assert response.json()["workflow_state"] == "running"
    assert response.json()["projected_at"] == projected_at.isoformat().replace("+00:00", "Z")


@pytest.mark.anyio
async def test_command_status_exposes_bounded_success_manifest_reference(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    accepted = accepted_command()
    projected_at = datetime.now(UTC).replace(microsecond=0)
    manifest_id = uuid4()
    expected = command_status(
        accepted,
        status="succeeded",
        workflow_id=f"signal:CrawlSite:{uuid4()}:{accepted.id}",
        workflow_type="CrawlSite",
        first_run_id=str(uuid4()),
        workflow_state="succeeded",
        projected_at=projected_at,
        result_reference=CrawlManifestReference(
            schema_version=1,
            manifest_id=str(manifest_id),
            manifest_sha256="a" * 64,
            coverage="partial",
            discovered_count=12,
            terminal_count=10,
            scope_version=3,
            crawl_policy_version=4,
        ),
    )
    gateway = StubCommandGateway(acceptance=accepted, status=expected)
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/sites/{site_id}/commands/{accepted.id}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["workflow_state"] == "succeeded"
    assert "terminal_reason" not in response.json()
    assert response.json()["result_reference"] == {
        "schema_version": 1,
        "manifest_id": str(manifest_id),
        "manifest_sha256": "a" * 64,
        "coverage": "partial",
        "discovered_count": 12,
        "terminal_count": 10,
        "scope_version": 3,
        "crawl_policy_version": 4,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("state", "reason"),
    [("failed", "crawl_activity_failed"), ("cancelled", "crawl_cancelled")],
)
async def test_command_status_exposes_closed_terminal_reason(
    browser_security: BrowserSecurity,
    state: str,
    reason: str,
):
    site_id = uuid4()
    accepted = accepted_command()
    expected = command_status(
        accepted,
        status=state,
        workflow_id=f"signal:CrawlSite:{uuid4()}:{accepted.id}",
        workflow_type="CrawlSite",
        first_run_id=str(uuid4()),
        workflow_state=state,
        projected_at=datetime.now(UTC).replace(microsecond=0),
        terminal_reason=reason,
    )
    gateway = StubCommandGateway(acceptance=accepted, status=expected)
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/sites/{site_id}/commands/{accepted.id}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == state
    assert response.json()["workflow_state"] == state
    assert "result_reference" not in response.json()
    assert response.json()["terminal_reason"] == reason


@pytest.mark.anyio
@pytest.mark.parametrize(
    "terminal_overrides",
    [
        {"status": "succeeded", "workflow_state": "succeeded"},
        {
            "status": "failed",
            "workflow_state": "failed",
            "terminal_reason": "crawl_cancelled",
        },
        {
            "status": "cancelled",
            "workflow_state": "succeeded",
            "terminal_reason": "crawl_cancelled",
        },
    ],
)
async def test_command_status_rejects_incoherent_terminal_projection(
    browser_security: BrowserSecurity,
    terminal_overrides,
):
    accepted = accepted_command()
    gateway = StubCommandGateway(
        acceptance=accepted,
        status=command_status(
            accepted,
            workflow_id=f"signal:CrawlSite:{uuid4()}:{accepted.id}",
            workflow_type="CrawlSite",
            first_run_id=str(uuid4()),
            projected_at=datetime.now(UTC),
            **terminal_overrides,
        ),
    )
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/sites/{uuid4()}/commands/{accepted.id}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "COMMAND_STATUS_FAILED"


@pytest.mark.anyio
async def test_command_status_rejects_incoherent_progress_projection(
    browser_security: BrowserSecurity,
):
    accepted = accepted_command()
    gateway = StubCommandGateway(
        acceptance=accepted,
        status=command_status(accepted, status="workflow_admitted"),
    )
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/sites/{uuid4()}/commands/{accepted.id}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "COMMAND_STATUS_FAILED"


@pytest.mark.anyio
async def test_command_status_rejects_workflow_identity_for_another_command(
    browser_security: BrowserSecurity,
):
    accepted = accepted_command()
    gateway = StubCommandGateway(
        acceptance=accepted,
        status=command_status(
            accepted,
            status="workflow_admitted",
            workflow_id=f"signal:CrawlSite:{uuid4()}:{uuid4()}",
            workflow_type="CrawlSite",
            workflow_state="admitted",
            projected_at=datetime.now(UTC),
        ),
    )
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/sites/{uuid4()}/commands/{accepted.id}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "COMMAND_STATUS_FAILED"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "processing", "workflow_state": "running"},
        {
            "status": "processing",
            "workflow_id": None,
            "workflow_type": "CrawlSite",
            "first_run_id": str(uuid4()),
            "workflow_state": "running",
            "projected_at": datetime.now(UTC),
        },
        {
            "status": "processing",
            "workflow_id": f"signal:CrawlSite:{uuid4()}:{uuid4()}",
            "workflow_type": "CrawlSite",
            "first_run_id": str(uuid4()),
            "workflow_state": "admitted",
            "projected_at": datetime.now(UTC),
        },
    ],
)
async def test_command_status_rejects_incomplete_or_incoherent_start_progress(
    browser_security: BrowserSecurity,
    overrides,
):
    accepted = accepted_command()
    gateway = StubCommandGateway(
        acceptance=accepted,
        status=command_status(accepted, **overrides),
    )
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/sites/{uuid4()}/commands/{accepted.id}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "COMMAND_STATUS_FAILED"


@pytest.mark.anyio
async def test_command_routes_fail_closed_without_gateway(browser_security: BrowserSecurity):
    site_id, command_id = uuid4(), uuid4()
    async with client_for(None, browser_security) as api:
        accepted = await api.post(
            f"/v1/sites/{site_id}/commands/snapshot",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )
        status = await api.get(
            f"/v1/sites/{site_id}/commands/{command_id}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    for response in (accepted, status):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    ["origin", "csrf", "cookie", "fetch_site"],
)
async def test_snapshot_mutation_rejects_invalid_browser_proof_before_gateway(
    browser_security: BrowserSecurity,
    mutation: str,
):
    site_id = uuid4()
    gateway = StubCommandGateway()
    headers = mutation_headers(browser_security)
    if mutation == "origin":
        headers["Origin"] = "https://attacker.example"
    elif mutation == "csrf":
        headers[CSRF_HEADER_NAME] = "x" * 43
    elif mutation == "cookie":
        headers["Cookie"] = f"{SESSION_COOKIE_NAME}={'x' * 43}"
    else:
        headers["Sec-Fetch-Site"] = "cross-site"
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{site_id}/commands/snapshot",
            headers=headers,
            json={"schema_version": 1},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "BROWSER_REQUEST_REJECTED"
    assert gateway.acceptance_inputs == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "headers"),
    [
        (b"{}", {}),
        (b'{"schema_version":2}', {}),
        (b'{"schema_version":1,"extra":true}', {}),
        (b'{"schema_version":1,"schema_version":1}', {}),
        (b'"not-an-object"', {}),
        (b"\xff", {}),
        (b'{"schema_version":1}', {"Content-Type": "text/plain"}),
        (b'{"schema_version":1}', {"Content-Encoding": "gzip"}),
        (b" " * 257, {}),
    ],
)
async def test_snapshot_request_body_is_strict_and_bounded_before_gateway(
    browser_security: BrowserSecurity,
    body: bytes,
    headers: dict[str, str],
):
    gateway = StubCommandGateway()
    supplied = mutation_headers(browser_security) | headers
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/commands/snapshot",
            headers=supplied,
            content=body,
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_SNAPSHOT_COMMAND"
    assert gateway.acceptance_inputs == []


@pytest.mark.anyio
async def test_idempotency_header_is_exact_and_bounded_before_gateway(
    browser_security: BrowserSecurity,
):
    gateway = StubCommandGateway()
    site_id = uuid4()
    base = list(mutation_headers(browser_security).items())
    without_key = [(key, value) for key, value in base if key.lower() != "idempotency-key"]
    duplicate = [*base, ("Idempotency-Key", "second-key")]
    invalid = mutation_headers(browser_security, idempotency_key="contains space")
    overlong = mutation_headers(browser_security, idempotency_key="x" * 129)
    async with client_for(gateway, browser_security) as api:
        responses = [
            await api.post(
                f"/v1/sites/{site_id}/commands/snapshot",
                headers=headers,
                content=b'{"schema_version":1}',
            )
            for headers in (without_key, duplicate, invalid, overlong)
        ]
    assert [response.status_code for response in responses] == [422, 422, 422, 422]
    assert all(
        response.json()["error"]["code"] == "INVALID_SNAPSHOT_COMMAND" for response in responses
    )
    assert gateway.acceptance_inputs == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "code", "clears_cookie"),
    [
        (InvalidSession(), 401, "TENANT_SESSION_REQUIRED", True),
        (AuthorizationDenied(), 403, "SITE_COMMAND_DENIED", False),
        (IdempotencyConflict(), 409, "IDEMPOTENCY_CONFLICT", False),
    ],
)
async def test_acceptance_failures_have_stable_non_disclosing_errors(
    browser_security: BrowserSecurity,
    error: Exception,
    status: int,
    code: str,
    clears_cookie: bool,
):
    gateway = StubCommandGateway(acceptance_error=error)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/commands/snapshot",
            headers=mutation_headers(browser_security, idempotency_key="private-key"),
            json={"schema_version": 1},
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "private-key" not in response.text
    cleared = any("Max-Age=0" in value for value in response.headers.get_list("set-cookie"))
    assert cleared is clears_cookie


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "code", "clears_cookie"),
    [
        (InvalidSession(), 401, "TENANT_SESSION_REQUIRED", True),
        (AuthorizationDenied(), 403, "SITE_COMMAND_DENIED", False),
        (CommandNotFound(), 404, "COMMAND_NOT_FOUND", False),
    ],
)
async def test_status_failures_have_stable_non_disclosing_errors(
    browser_security: BrowserSecurity,
    error: Exception,
    status: int,
    code: str,
    clears_cookie: bool,
):
    gateway = StubCommandGateway(status_error=error)
    async with client_for(gateway, browser_security) as api:
        response = await api.get(
            f"/v1/sites/{uuid4()}/commands/{uuid4()}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    cleared = any("Max-Age=0" in value for value in response.headers.get_list("set-cookie"))
    assert cleared is clears_cookie


@pytest.mark.anyio
async def test_status_rejects_missing_or_duplicate_session_cookie_before_gateway(
    browser_security: BrowserSecurity,
):
    gateway = StubCommandGateway()
    route = f"/v1/sites/{uuid4()}/commands/{uuid4()}"
    duplicate = [
        ("Cookie", f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"),
        ("Cookie", f"{SESSION_COOKIE_NAME}={'x' * 43}"),
    ]
    async with client_for(gateway, browser_security) as api:
        missing = await api.get(route)
        ambiguous = await api.get(route, headers=duplicate)
    assert missing.status_code == ambiguous.status_code == 401
    assert gateway.status_inputs == []


@pytest.mark.anyio
async def test_gateway_output_is_revalidated_before_response(
    browser_security: BrowserSecurity,
):
    site_id, command_id = uuid4(), uuid4()
    bad_acceptance = SimpleNamespace(
        id=command_id,
        reused=False,
        status="succeeded",
        accepted_at=datetime.now(UTC),
    )
    bad_status = SimpleNamespace(
        id=uuid4(),
        actor_user_id=uuid4(),
        kind="site.snapshot",
        status="accepted",
        accepted_at=datetime.now(UTC),
    )
    gateway = StubCommandGateway(acceptance=bad_acceptance, status=bad_status)
    async with client_for(gateway, browser_security) as api:
        accepted = await api.post(
            f"/v1/sites/{site_id}/commands/snapshot",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )
        status = await api.get(
            f"/v1/sites/{site_id}/commands/{command_id}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    assert accepted.status_code == 500
    assert accepted.json()["error"]["code"] == "COMMAND_ACCEPTANCE_FAILED"
    assert status.status_code == 500
    assert status.json()["error"]["code"] == "COMMAND_STATUS_FAILED"
