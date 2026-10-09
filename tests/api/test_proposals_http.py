import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.audit_findings import AuditFinding
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.model_reasoning import MetadataDraft, MetadataDraftError, ModelUsage
from signal_core.proposals import (
    ApprovalDecisionConflict,
    ApprovalExpired,
    ApprovalNotFound,
    ApprovalPermissionDenied,
    ApprovalRevisionConflict,
    FixtureModelRun,
    LocalProposal,
    ModelAttemptLimit,
    ModelInputConflict,
    ModelOutcomeUnknown,
    ModelRunInProgress,
    ProposalManifestConflict,
    ProposalNotReady,
    build_local_fixture_manifest,
    build_model_fixture_manifest,
    build_verified_homepage_model_manifest,
    canonicalize_proposal_manifest,
)

TENANT_TOKEN = "t" * 43
ORIGIN = "https://dashboard.example.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def browser_security() -> BrowserSecurity:
    return BrowserSecurity(
        csrf_hmac_key=b"proposal-browser-security-key-32-bytes",
        allowed_origins=frozenset({ORIGIN}),
    )


def proposal(**overrides) -> LocalProposal:
    now = datetime.now(UTC).replace(microsecond=0)
    site_id = overrides.pop("site_id", uuid4())
    finding = AuditFinding(
        id=uuid4(),
        evidence_id=uuid4(),
        command_id=uuid4(),
        manifest_id=uuid4(),
        finding_key="metadata.meta_description.missing",
        title="Missing meta description",
        summary="The synthetic fixture has no non-empty meta description.",
        resource_locator="/fixture/missing-meta-description",
        severity="medium",
        status="open",
        confidence_class="deterministic",
        source_kind="synthetic_fixture",
        source_identifier="fixture:local-pilot/missing-meta-description/v1",
        content_sha256="a" * 64,
        evidence_observed_at=now,
        first_seen_at=now,
        last_seen_at=now,
        reused=False,
    )
    manifest = build_local_fixture_manifest(site_id=site_id, finding=finding)
    values = {
        "proposal_id": uuid4(),
        "revision_id": uuid4(),
        "revision_number": 1,
        "revision_sha256": hashlib.sha256(canonicalize_proposal_manifest(manifest)).hexdigest(),
        "manifest": manifest,
        "created_by_user_id": uuid4(),
        "created_at": now,
        "approval_request_id": uuid4(),
        "approval_status": "pending",
        "approval_requested_at": now,
        "approval_expires_at": now + timedelta(hours=24),
        "decision_id": None,
        "decision": None,
        "decided_by_user_id": None,
        "decision_channel": None,
        "decided_at": None,
        "reused": False,
        **overrides,
    }
    return LocalProposal(**values)


def model_proposal(*, site_id: UUID) -> LocalProposal:
    now = datetime.now(UTC).replace(microsecond=0)
    description = (
        "Explore Signal's synthetic fixture through a supervised metadata draft "
        "grounded in exact local evidence."
    )
    rationale = "The copy is limited to facts present in the synthetic evidence packet."
    output = {"meta_description": description, "rationale": rationale}
    output_sha256 = hashlib.sha256(
        json.dumps(output, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    draft = MetadataDraft(
        meta_description=description,
        rationale=rationale,
        provider_response_id="resp_api_fixture_1",
        model_reported="gpt-6-luna-2026-09-01",
        usage=ModelUsage(100, 30, 130, 0),
        output_sha256=output_sha256,
    )
    run = FixtureModelRun(
        agent_run_id=uuid4(),
        model_call_id=uuid4(),
        attempt_number=1,
        finding_id=uuid4(),
        evidence_id=uuid4(),
        command_id=uuid4(),
        manifest_id=uuid4(),
        resource_locator="/fixture/missing-meta-description",
        confidence_class="deterministic",
        evidence_observed_at=now,
        input_sha256="b" * 64,
        status="requested",
        reused=False,
        outcome="started",
    )
    manifest = build_model_fixture_manifest(site_id=site_id, run=run, draft=draft)
    return proposal(
        site_id=site_id,
        manifest=manifest,
        revision_sha256=hashlib.sha256(canonicalize_proposal_manifest(manifest)).hexdigest(),
    )


def verified_model_proposal(*, site_id: UUID) -> LocalProposal:
    now = datetime.now(UTC).replace(microsecond=0)
    description = (
        "Understand Acme search performance through the title and heading observed on its "
        "owner-verified homepage."
    )
    rationale = "The copy is limited to the verified homepage title and heading."
    output = {"meta_description": description, "rationale": rationale}
    output_sha256 = hashlib.sha256(
        json.dumps(output, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    draft = MetadataDraft(
        meta_description=description,
        rationale=rationale,
        provider_response_id="resp_api_verified_1",
        model_reported="gpt-6-luna-2026-09-20",
        usage=ModelUsage(90, 30, 120, 0),
        output_sha256=output_sha256,
    )
    run = FixtureModelRun(
        agent_run_id=uuid4(),
        model_call_id=uuid4(),
        attempt_number=1,
        finding_id=uuid4(),
        evidence_id=uuid4(),
        command_id=uuid4(),
        manifest_id=uuid4(),
        resource_locator="https://acme.example/",
        confidence_class="deterministic",
        evidence_observed_at=now,
        input_sha256="c" * 64,
        status="requested",
        reused=False,
        outcome="started",
    )
    manifest = build_verified_homepage_model_manifest(site_id=site_id, run=run, draft=draft)
    return proposal(
        site_id=site_id,
        manifest=manifest,
        revision_sha256=hashlib.sha256(canonicalize_proposal_manifest(manifest)).hexdigest(),
    )


@dataclass
class StubProposalGateway:
    current: tuple[LocalProposal, ...] = field(default_factory=lambda: (proposal(),))
    error: Exception | None = None
    prepare_inputs: list[tuple[str, UUID]] = field(default_factory=list)
    read_inputs: list[tuple[str, UUID]] = field(default_factory=list)
    decision_inputs: list[tuple[object, ...]] = field(default_factory=list)

    async def prepare_local_fixture_proposal(
        self, *, session_token: str, site_id: UUID
    ) -> LocalProposal:
        self.prepare_inputs.append((session_token, site_id))
        if self.error is not None:
            raise self.error
        return self.current[0]

    async def prepare_model_fixture_proposal(
        self, *, session_token: str, site_id: UUID
    ) -> LocalProposal:
        self.prepare_inputs.append((session_token, site_id))
        if self.error is not None:
            raise self.error
        return self.current[0]

    async def prepare_verified_homepage_proposal(
        self, *, session_token: str, site_id: UUID
    ) -> LocalProposal:
        self.prepare_inputs.append((session_token, site_id))
        if self.error is not None:
            raise self.error
        return self.current[0]

    async def local_fixture_proposals(
        self, *, session_token: str, site_id: UUID
    ) -> tuple[LocalProposal, ...]:
        self.read_inputs.append((session_token, site_id))
        if self.error is not None:
            raise self.error
        return self.current

    async def decide_local_fixture_approval(
        self,
        *,
        session_token: str,
        site_id: UUID,
        approval_request_id: UUID,
        revision_sha256: str,
        decision_id: UUID,
        decision: str,
    ) -> LocalProposal:
        self.decision_inputs.append(
            (
                session_token,
                site_id,
                approval_request_id,
                revision_sha256,
                decision_id,
                decision,
            )
        )
        if self.error is not None:
            raise self.error
        current = self.current[0]
        return proposal(
            site_id=site_id,
            proposal_id=current.proposal_id,
            revision_id=current.revision_id,
            revision_number=current.revision_number,
            approval_request_id=approval_request_id,
            approval_status=decision,
            decision_id=decision_id,
            decision=decision,
            decided_by_user_id=uuid4(),
            decision_channel="dashboard",
            decided_at=datetime.now(UTC),
        )


def client_for(gateway: object | None, security: BrowserSecurity) -> httpx.AsyncClient:
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_security=security,
        browser_proposals=gateway,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://signal.example.test",
    )


def mutation_headers(security: BrowserSecurity) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}",
        CSRF_HEADER_NAME: security.issue_csrf_token(TENANT_TOKEN),
        "Content-Type": "application/json",
        "X-Correlation-ID": "proposal-request-1",
    }


@pytest.mark.anyio
async def test_prepare_read_and_decide_one_exact_proposal(browser_security: BrowserSecurity):
    site_id = uuid4()
    current = proposal(site_id=site_id)
    gateway = StubProposalGateway(current=(current,))
    headers = mutation_headers(browser_security)
    decision_id = uuid4()
    async with client_for(gateway, browser_security) as api:
        prepared = await api.post(
            f"/v1/sites/{site_id}/proposals/local-fixture",
            headers=headers,
            json={"schema_version": 1},
        )
        visible = await api.get(
            f"/v1/sites/{site_id}/proposals",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
        decided = await api.post(
            f"/v1/sites/{site_id}/approval-requests/{current.approval_request_id}/decision",
            headers=headers,
            json={
                "schema_version": 1,
                "revision_sha256": current.revision_sha256,
                "decision_id": str(decision_id),
                "decision": "approved",
            },
        )

    assert prepared.status_code == 200
    assert prepared.json()["proposal"]["revision_sha256"] == current.revision_sha256
    assert visible.status_code == 200
    assert visible.json()["proposals"][0]["manifest"]["authority"]["external_write"] is False
    assert decided.status_code == 200
    assert decided.json()["proposal"]["decision"] == "approved"
    assert gateway.prepare_inputs == [(TENANT_TOKEN, site_id)]
    assert gateway.read_inputs == [(TENANT_TOKEN, site_id)]
    assert gateway.decision_inputs[0][-2:] == (decision_id, "approved")
    assert TENANT_TOKEN not in prepared.text + visible.text + decided.text


@pytest.mark.anyio
async def test_model_prepare_returns_release_evidence_and_no_write_authority(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    current = model_proposal(site_id=site_id)
    gateway = StubProposalGateway(current=(current,))
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{site_id}/proposals/model-fixture",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )

    assert response.status_code == 200
    manifest = response.json()["proposal"]["manifest"]
    assert manifest["proposal_kind"] == "model_fixture_metadata_draft"
    assert manifest["model"]["model_requested"] == "gpt-6-luna"
    assert manifest["model"]["store"] is False
    assert manifest["authority"]["external_write"] is False
    assert gateway.prepare_inputs == [(TENANT_TOKEN, site_id)]


@pytest.mark.anyio
async def test_verified_homepage_prepare_returns_real_evidence_bound_revision(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    current = verified_model_proposal(site_id=site_id)
    gateway = StubProposalGateway(current=(current,))
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{site_id}/proposals/verified-homepage",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )

    assert response.status_code == 200
    manifest = response.json()["proposal"]["manifest"]
    assert manifest["proposal_kind"] == "model_verified_homepage_metadata_draft"
    assert manifest["target"]["resource_locator"] == "https://acme.example/"
    assert manifest["assessment"]["customer_origin_read"] is True
    assert manifest["model"]["release"] == "verified-gpt-6-luna-metadata-v2"
    assert manifest["authority"]["requested"] == "accept_verified_homepage_metadata_draft"
    assert manifest["authority"]["external_write"] is False
    assert gateway.prepare_inputs == [(TENANT_TOKEN, site_id)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "code", "retryable"),
    [
        (ProposalNotReady(), 409, "PROPOSAL_NOT_READY", False),
        (
            MetadataDraftError("MODEL_OUTPUT_INVALID", retryable=False),
            502,
            "MODEL_OUTPUT_INVALID",
            False,
        ),
    ],
)
async def test_verified_homepage_prepare_fails_closed_without_a_usable_revision(
    browser_security: BrowserSecurity,
    error: Exception,
    status: int,
    code: str,
    retryable: bool,
):
    gateway = StubProposalGateway(error=error)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/proposals/verified-homepage",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["retryable"] is retryable


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ProposalNotReady(), 409, "PROPOSAL_NOT_READY"),
        (ProposalManifestConflict(), 409, "PROPOSAL_EVIDENCE_CHANGED"),
        (ApprovalPermissionDenied(), 403, "APPROVAL_PERMISSION_DENIED"),
        (AuthorizationDenied(), 403, "SITE_COMMAND_DENIED"),
        (InvalidSession(), 401, "TENANT_SESSION_REQUIRED"),
    ],
)
async def test_prepare_maps_closed_failure_states(
    browser_security: BrowserSecurity, error: Exception, status: int, code: str
):
    gateway = StubProposalGateway(error=error)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/proposals/local-fixture",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "code", "retryable"),
    [
        (ModelRunInProgress(), 409, "MODEL_RUN_IN_PROGRESS", True),
        (ModelOutcomeUnknown(), 409, "MODEL_OUTCOME_UNKNOWN", False),
        (ModelAttemptLimit(), 409, "MODEL_ATTEMPT_LIMIT", False),
        (ModelInputConflict(), 409, "PROPOSAL_EVIDENCE_CHANGED", True),
        (
            MetadataDraftError("MODEL_CREDENTIAL_UNAVAILABLE", retryable=False),
            503,
            "MODEL_CREDENTIAL_UNAVAILABLE",
            False,
        ),
        (
            MetadataDraftError("MODEL_RATE_LIMITED", retryable=True),
            503,
            "MODEL_RATE_LIMITED",
            True,
        ),
        (
            MetadataDraftError("MODEL_OUTPUT_INVALID", retryable=False),
            502,
            "MODEL_OUTPUT_INVALID",
            False,
        ),
    ],
)
async def test_model_prepare_maps_sanitized_closed_failure_states(
    browser_security: BrowserSecurity,
    error: Exception,
    status: int,
    code: str,
    retryable: bool,
):
    gateway = StubProposalGateway(error=error)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/proposals/model-fixture",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["retryable"] is retryable


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["model-fixture", "verified-homepage"])
@pytest.mark.parametrize("code", ["MODEL_BUDGET_EXHAUSTED", "MODEL_LOW_QUALITY"])
async def test_model_budget_and_quality_exhaustion_are_visibly_unavailable(
    browser_security: BrowserSecurity, endpoint: str, code: str
):
    gateway = StubProposalGateway(error=MetadataDraftError(code, retryable=False))
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/proposals/{endpoint}",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["retryable"] is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ApprovalNotFound(), 404, "APPROVAL_NOT_FOUND"),
        (ApprovalExpired(), 409, "APPROVAL_EXPIRED"),
        (ApprovalRevisionConflict(), 409, "APPROVAL_REVISION_CHANGED"),
        (ApprovalDecisionConflict(), 409, "APPROVAL_ALREADY_DECIDED"),
        (ApprovalPermissionDenied(), 403, "APPROVAL_PERMISSION_DENIED"),
    ],
)
async def test_decision_maps_closed_failure_states(
    browser_security: BrowserSecurity, error: Exception, status: int, code: str
):
    gateway = StubProposalGateway(error=error)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/approval-requests/{uuid4()}/decision",
            headers=mutation_headers(browser_security),
            json={
                "schema_version": 1,
                "revision_sha256": "a" * 64,
                "decision_id": str(uuid4()),
                "decision": "approved",
            },
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


@pytest.mark.anyio
async def test_proposal_mutations_reject_bad_body_and_cross_origin_before_gateway(
    browser_security: BrowserSecurity,
):
    gateway = StubProposalGateway()
    bad = mutation_headers(browser_security)
    attack = mutation_headers(browser_security)
    attack["Origin"] = "https://attacker.example"
    async with client_for(gateway, browser_security) as api:
        malformed = await api.post(
            f"/v1/sites/{uuid4()}/proposals/local-fixture",
            headers=bad,
            json={"schema_version": 1, "extra": True},
        )
        rejected = await api.post(
            f"/v1/sites/{uuid4()}/proposals/local-fixture",
            headers=attack,
            json={"schema_version": 1},
        )
        bad_decision = await api.post(
            f"/v1/sites/{uuid4()}/approval-requests/{uuid4()}/decision",
            headers=bad,
            json={
                "schema_version": 1,
                "revision_sha256": "a" * 64,
                "decision_id": str(uuid4()),
                "decision": "merge",
            },
        )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "INVALID_PROPOSAL_REQUEST"
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "BROWSER_REQUEST_REJECTED"
    assert bad_decision.status_code == 422
    assert bad_decision.json()["error"]["code"] == "INVALID_APPROVAL_DECISION"
    assert gateway.prepare_inputs == []
    assert gateway.decision_inputs == []


@pytest.mark.anyio
async def test_proposal_routes_require_an_explicit_gateway(browser_security: BrowserSecurity):
    site_id = uuid4()
    async with client_for(None, browser_security) as api:
        prepared = await api.post(
            f"/v1/sites/{site_id}/proposals/local-fixture",
            headers=mutation_headers(browser_security),
            json={"schema_version": 1},
        )
        visible = await api.get(
            f"/v1/sites/{site_id}/proposals",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}"},
        )
    for response in (prepared, visible):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"
