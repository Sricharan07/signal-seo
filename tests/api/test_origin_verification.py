from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.authorization import InvalidSession
from signal_core.origin_verification import (
    InvalidOriginVerification,
    OriginChallenge,
    OriginChallengeExpired,
    OriginChallengeNotFound,
    OriginClaimConflict,
    OriginProofMismatch,
    OriginProofNotFound,
    OriginProofRejected,
    OriginProofUnavailable,
    OriginVerificationConflict,
    OriginVerificationDenied,
    VerifiedOrigin,
)

TENANT_TOKEN = "t" * 43
BROWSER_ORIGIN = "https://dashboard.example.test"
SITE_ORIGIN = "https://www.example.com"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def browser_security() -> BrowserSecurity:
    return BrowserSecurity(
        csrf_hmac_key=b"origin-verification-key" * 2,
        allowed_origins=frozenset({BROWSER_ORIGIN}),
    )


def challenge(**overrides) -> OriginChallenge:
    challenge_id = overrides.pop("challenge_id", uuid4())
    now = datetime.now(UTC)
    values = {
        "tenant_id": uuid4(),
        "user_id": uuid4(),
        "site_id": uuid4(),
        "challenge_id": challenge_id,
        "origin": SITE_ORIGIN,
        "proof_url": f"{SITE_ORIGIN}/.well-known/signal-site-verification.txt",
        "proof_content": f"signal-site-verification={challenge_id}\n",
        "issued_at": now,
        "expires_at": now + timedelta(minutes=30),
        "replayed": False,
        **overrides,
    }
    return OriginChallenge(**values)


def verified(**overrides) -> VerifiedOrigin:
    now = datetime.now(UTC)
    values = {
        "tenant_id": uuid4(),
        "user_id": uuid4(),
        "site_id": uuid4(),
        "challenge_id": uuid4(),
        "origin": SITE_ORIGIN,
        "proof_method": "http_well_known",
        "verified_at": now,
        "recheck_at": now + timedelta(days=30),
        "replayed": False,
        **overrides,
    }
    return VerifiedOrigin(**values)


@dataclass
class StubOriginGateway:
    challenge_result: OriginChallenge = field(default_factory=challenge)
    verification_result: VerifiedOrigin = field(default_factory=verified)
    challenge_error: Exception | None = None
    verification_error: Exception | None = None
    challenge_inputs: list[tuple[object, ...]] = field(default_factory=list)
    verification_inputs: list[tuple[object, ...]] = field(default_factory=list)

    async def issue_origin_challenge(
        self,
        *,
        session_token: str,
        site_id: UUID,
        origin: str,
        idempotency_key: UUID,
    ) -> OriginChallenge:
        self.challenge_inputs.append((session_token, site_id, origin, idempotency_key))
        if self.challenge_error is not None:
            raise self.challenge_error
        return self.challenge_result

    async def verify_origin(
        self,
        *,
        session_token: str,
        site_id: UUID,
        challenge_id: UUID,
        origin: str,
        idempotency_key: UUID,
    ) -> VerifiedOrigin:
        self.verification_inputs.append(
            (session_token, site_id, challenge_id, origin, idempotency_key)
        )
        if self.verification_error is not None:
            raise self.verification_error
        return self.verification_result


def client_for(gateway: object | None, security: BrowserSecurity) -> httpx.AsyncClient:
    application = create_app(
        settings=ApiSettings(environment="test"),
        browser_login=gateway,
        browser_security=security,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="https://signal.example.test",
    )


def mutation_headers(security: BrowserSecurity) -> dict[str, str]:
    return {
        "Origin": BROWSER_ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Cookie": f"{SESSION_COOKIE_NAME}={TENANT_TOKEN}",
        CSRF_HEADER_NAME: security.issue_csrf_token(TENANT_TOKEN),
    }


@pytest.mark.anyio
async def test_owner_can_issue_exact_origin_challenge(browser_security: BrowserSecurity):
    site_id = uuid4()
    request_id = uuid4()
    result = challenge(site_id=site_id)
    gateway = StubOriginGateway(challenge_result=result)

    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{site_id}/origin-challenges",
            headers=mutation_headers(browser_security),
            json={"idempotency_key": str(request_id), "origin": SITE_ORIGIN},
        )

    assert response.status_code == 201
    assert response.json() == {
        "schema_version": 1,
        "site_id": str(site_id),
        "challenge_id": str(result.challenge_id),
        "origin": SITE_ORIGIN,
        "proof_method": "http_well_known",
        "proof_url": result.proof_url,
        "proof_content": result.proof_content,
        "issued_at": result.issued_at.isoformat().replace("+00:00", "Z"),
        "expires_at": result.expires_at.isoformat().replace("+00:00", "Z"),
        "replayed": False,
    }
    assert gateway.challenge_inputs == [(TENANT_TOKEN, site_id, SITE_ORIGIN, request_id)]


@pytest.mark.anyio
async def test_exact_origin_verification_returns_bounded_authority(
    browser_security: BrowserSecurity,
):
    site_id = uuid4()
    challenge_id = uuid4()
    request_id = uuid4()
    result = verified(site_id=site_id, challenge_id=challenge_id)
    gateway = StubOriginGateway(verification_result=result)

    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{site_id}/verify-origin",
            headers=mutation_headers(browser_security),
            json={
                "idempotency_key": str(request_id),
                "challenge_id": str(challenge_id),
                "origin": SITE_ORIGIN,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "site_id": str(site_id),
        "challenge_id": str(challenge_id),
        "origin": SITE_ORIGIN,
        "ownership_status": "verified",
        "proof_method": "http_well_known",
        "permitted_origins": [SITE_ORIGIN],
        "verified_at": result.verified_at.isoformat().replace("+00:00", "Z"),
        "recheck_at": result.recheck_at.isoformat().replace("+00:00", "Z"),
        "replayed": False,
    }
    assert gateway.verification_inputs == [
        (TENANT_TOKEN, site_id, challenge_id, SITE_ORIGIN, request_id)
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "status", "code", "retryable"),
    [
        (InvalidSession(), 401, "TENANT_SESSION_REQUIRED", False),
        (InvalidOriginVerification(), 422, "INVALID_ORIGIN_VERIFICATION", False),
        (OriginVerificationDenied(), 403, "ORIGIN_VERIFICATION_DENIED", False),
        (OriginChallengeNotFound(), 404, "ORIGIN_CHALLENGE_NOT_FOUND", False),
        (OriginChallengeExpired(), 409, "ORIGIN_CHALLENGE_EXPIRED", True),
        (OriginProofNotFound(), 409, "ORIGIN_PROOF_NOT_FOUND", True),
        (OriginProofMismatch(), 409, "ORIGIN_PROOF_MISMATCH", True),
        (OriginProofRejected(), 422, "ORIGIN_PROOF_REJECTED", False),
        (OriginProofUnavailable(), 503, "ORIGIN_PROOF_UNAVAILABLE", True),
        (OriginClaimConflict(), 409, "ORIGIN_CLAIM_CONFLICT", False),
        (OriginVerificationConflict(), 409, "ORIGIN_VERIFICATION_CONFLICT", True),
    ],
)
async def test_verification_maps_closed_safe_outcomes(
    browser_security: BrowserSecurity,
    error: Exception,
    status: int,
    code: str,
    retryable: bool,
):
    site_id = uuid4()
    gateway = StubOriginGateway(verification_error=error)
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{site_id}/verify-origin",
            headers=mutation_headers(browser_security),
            json={
                "idempotency_key": str(uuid4()),
                "challenge_id": str(uuid4()),
                "origin": SITE_ORIGIN,
            },
        )

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["retryable"] is retryable
    assert "example.com" not in response.json()["error"]["message"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    ["origin-challenges", "verify-origin"],
)
async def test_mutations_reject_bad_browser_proof_without_gateway_call(
    browser_security: BrowserSecurity,
    path: str,
):
    site_id = uuid4()
    gateway = StubOriginGateway()
    headers = mutation_headers(browser_security)
    headers[CSRF_HEADER_NAME] = "x" * 43
    payload = {"idempotency_key": str(uuid4()), "origin": SITE_ORIGIN}
    if path == "verify-origin":
        payload["challenge_id"] = str(uuid4())

    async with client_for(gateway, browser_security) as api:
        response = await api.post(f"/v1/sites/{site_id}/{path}", headers=headers, json=payload)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "BROWSER_REQUEST_REJECTED"
    assert gateway.challenge_inputs == []
    assert gateway.verification_inputs == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("origin-challenges", {}),
        (
            "origin-challenges",
            {"idempotency_key": str(uuid4()), "origin": "http://www.example.com"},
        ),
        (
            "verify-origin",
            {
                "idempotency_key": str(uuid4()),
                "challenge_id": str(uuid4()),
                "origin": f"{SITE_ORIGIN}/path",
            },
        ),
        (
            "verify-origin",
            {
                "idempotency_key": str(uuid4()),
                "challenge_id": str(uuid4()),
                "origin": SITE_ORIGIN,
                "tenant_id": str(uuid4()),
            },
        ),
    ],
)
async def test_invalid_bodies_fail_before_gateway_call(
    browser_security: BrowserSecurity,
    path: str,
    payload: dict[str, object],
):
    gateway = StubOriginGateway()
    async with client_for(gateway, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/{path}",
            headers=mutation_headers(browser_security),
            json=payload,
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_ORIGIN_VERIFICATION"
    assert gateway.challenge_inputs == []
    assert gateway.verification_inputs == []


@pytest.mark.anyio
async def test_gateway_projection_mismatch_is_rejected(browser_security: BrowserSecurity):
    site_id = uuid4()
    challenge_id = uuid4()
    challenge_gateway = StubOriginGateway(
        challenge_result=challenge(site_id=uuid4()),
    )
    verification_gateway = StubOriginGateway(
        verification_result=verified(site_id=site_id, challenge_id=uuid4()),
    )
    async with client_for(challenge_gateway, browser_security) as api:
        challenge_response = await api.post(
            f"/v1/sites/{site_id}/origin-challenges",
            headers=mutation_headers(browser_security),
            json={"idempotency_key": str(uuid4()), "origin": SITE_ORIGIN},
        )
    async with client_for(verification_gateway, browser_security) as api:
        verification_response = await api.post(
            f"/v1/sites/{site_id}/verify-origin",
            headers=mutation_headers(browser_security),
            json={
                "idempotency_key": str(uuid4()),
                "challenge_id": str(challenge_id),
                "origin": SITE_ORIGIN,
            },
        )

    assert challenge_response.status_code == 500
    assert challenge_response.json()["error"]["code"] == "ORIGIN_CHALLENGE_FAILED"
    assert verification_response.status_code == 500
    assert verification_response.json()["error"]["code"] == "ORIGIN_VERIFICATION_FAILED"


@pytest.mark.anyio
async def test_origin_gateway_must_be_configured(browser_security: BrowserSecurity):
    async with client_for(None, browser_security) as api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/origin-challenges",
            headers=mutation_headers(browser_security),
            json={"idempotency_key": str(uuid4()), "origin": SITE_ORIGIN},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTHENTICATION_NOT_READY"


def test_result_representations_do_not_render_public_proof_content():
    result = challenge()
    assert result.proof_content not in repr(result)
    assert "signal-site-verification" not in repr(replace(result, replayed=True))
