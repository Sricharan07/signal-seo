from typing import Annotated

import httpx2 as httpx
import pytest
from fastapi import Depends, FastAPI, Response
from signal_api.browser_security import (
    CSRF_HEADER_NAME,
    IDENTITY_COOKIE_NAME,
    INVITATION_IDENTITY_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    BrowserInvitationMutationProof,
    BrowserLogoutProof,
    BrowserMutationProof,
    BrowserRequestRejected,
    BrowserSecurity,
    clear_session_cookie,
    require_browser_mutation,
    set_invitation_identity_cookie,
    set_session_cookie,
)
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.session_tokens import InvalidOpaqueSessionToken, hash_session_token
from starlette.requests import Request

SESSION_TOKEN = "s" * 43
IDENTITY_TOKEN = "i" * 43
ORIGIN = "https://dashboard.example.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def browser_security() -> BrowserSecurity:
    return BrowserSecurity(csrf_hmac_key=b"k" * 32, allowed_origins=frozenset({ORIGIN}))


def protected_app(security: BrowserSecurity | None) -> FastAPI:
    application = create_app(settings=ApiSettings(environment="test"), browser_security=security)

    @application.post("/test/mutation")
    async def mutation(
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> dict[str, object]:
        return {
            "origin": proof.origin,
            "session_hash_length": len(hash_session_token(proof.session_token)),
        }

    return application


def valid_headers(security: BrowserSecurity) -> list[tuple[str, str]]:
    return [
        ("Origin", ORIGIN),
        ("Sec-Fetch-Site", "same-site"),
        ("Cookie", f"unrelated=value; {SESSION_COOKIE_NAME}={SESSION_TOKEN}"),
        (CSRF_HEADER_NAME, security.issue_csrf_token(SESSION_TOKEN)),
    ]


def starlette_request(method: str, headers: list[tuple[str, str]]) -> Request:
    prepared = httpx.Request(method, "https://api.example.test/test", headers=headers)
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/test",
            "headers": prepared.headers.raw,
        }
    )


@pytest.mark.anyio
async def test_valid_session_bound_same_site_mutation_is_accepted(
    browser_security: BrowserSecurity,
):
    transport = httpx.ASGITransport(app=protected_app(browser_security))
    async with httpx.AsyncClient(transport=transport, base_url="https://api.example.test") as api:
        response = await api.post("/test/mutation", headers=valid_headers(browser_security))
    assert response.status_code == 200
    assert response.json() == {"origin": ORIGIN, "session_hash_length": 32}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "missing_origin",
        "untrusted_origin",
        "cross_site",
        "missing_cookie",
        "malformed_cookie",
        "duplicate_cookie",
        "missing_csrf",
        "wrong_csrf",
        "duplicate_csrf",
        "duplicate_origin",
        "duplicate_fetch_site",
    ],
)
async def test_missing_ambiguous_or_invalid_proofs_are_indistinguishable(
    browser_security: BrowserSecurity, mutation: str
):
    headers = valid_headers(browser_security)
    if mutation == "missing_origin":
        headers = [header for header in headers if header[0].lower() != "origin"]
    elif mutation == "untrusted_origin":
        headers[0] = ("Origin", "https://attacker.example")
    elif mutation == "cross_site":
        headers[1] = ("Sec-Fetch-Site", "cross-site")
    elif mutation == "missing_cookie":
        headers = [header for header in headers if header[0].lower() != "cookie"]
    elif mutation == "malformed_cookie":
        headers[2] = ("Cookie", f"{SESSION_COOKIE_NAME}=short")
    elif mutation == "duplicate_cookie":
        headers.append(("Cookie", f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}"))
    elif mutation == "missing_csrf":
        headers = [header for header in headers if header[0].lower() != "x-csrf-token"]
    elif mutation == "wrong_csrf":
        headers[3] = (CSRF_HEADER_NAME, "x" * 43)
    elif mutation == "duplicate_csrf":
        headers.append((CSRF_HEADER_NAME, browser_security.issue_csrf_token(SESSION_TOKEN)))
    elif mutation == "duplicate_origin":
        headers.append(("Origin", ORIGIN))
    elif mutation == "duplicate_fetch_site":
        headers.append(("Sec-Fetch-Site", "same-site"))

    transport = httpx.ASGITransport(app=protected_app(browser_security))
    async with httpx.AsyncClient(transport=transport, base_url="https://api.example.test") as api:
        response = await api.post("/test/mutation", headers=headers)
    body = response.json()["error"]
    assert response.status_code == 403
    assert body["code"] == "BROWSER_REQUEST_REJECTED"
    assert body["message"] == "The browser request could not be authorized."
    assert body["retryable"] is False
    assert SESSION_TOKEN not in response.text
    assert "attacker" not in response.text


@pytest.mark.anyio
async def test_unconfigured_browser_security_fails_closed():
    headers = [
        ("Origin", ORIGIN),
        ("Cookie", f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}"),
        (CSRF_HEADER_NAME, "x" * 43),
    ]
    transport = httpx.ASGITransport(app=protected_app(None))
    async with httpx.AsyncClient(transport=transport, base_url="https://api.example.test") as api:
        response = await api.post("/test/mutation", headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "BROWSER_SECURITY_NOT_READY"


def test_csrf_proof_is_session_and_key_bound(browser_security: BrowserSecurity):
    first = browser_security.issue_csrf_token(SESSION_TOKEN)
    assert first != browser_security.issue_csrf_token("t" * 43)
    rotated = BrowserSecurity(
        csrf_hmac_key=b"r" * 32, allowed_origins=browser_security.allowed_origins
    )
    assert first != rotated.issue_csrf_token(SESSION_TOKEN)
    assert len(first) == 43


def test_logout_csrf_uses_tenant_then_pretenant_identity_cookie(
    browser_security: BrowserSecurity,
):
    tenant_request = starlette_request(
        "GET",
        [
            (
                "Cookie",
                f"{IDENTITY_COOKIE_NAME}={IDENTITY_TOKEN}; {SESSION_COOKIE_NAME}={SESSION_TOKEN}",
            )
        ],
    )
    identity_request = starlette_request(
        "GET",
        [("Cookie", f"{IDENTITY_COOKIE_NAME}={IDENTITY_TOKEN}")],
    )

    assert browser_security.issue_logout_csrf_token(
        tenant_request
    ) == browser_security.issue_csrf_token(SESSION_TOKEN)
    assert browser_security.issue_logout_csrf_token(
        identity_request
    ) == browser_security.issue_csrf_token(IDENTITY_TOKEN)


@pytest.mark.parametrize(
    "cookies",
    [
        [],
        [("Cookie", f"{SESSION_COOKIE_NAME}=short")],
        [
            ("Cookie", f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}"),
            ("Cookie", f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}"),
        ],
        [
            ("Cookie", f"{IDENTITY_COOKIE_NAME}={IDENTITY_TOKEN}"),
            ("Cookie", f"{IDENTITY_COOKIE_NAME}={IDENTITY_TOKEN}"),
        ],
    ],
)
def test_logout_csrf_rejects_missing_malformed_or_duplicate_authority(
    browser_security: BrowserSecurity,
    cookies: list[tuple[str, str]],
):
    with pytest.raises(BrowserRequestRejected):
        browser_security.issue_logout_csrf_token(starlette_request("GET", cookies))


def test_cookie_helpers_preserve_host_prefix_invariants():
    issued = Response()
    set_session_cookie(issued, SESSION_TOKEN, max_age=900)
    issued_header = issued.headers["set-cookie"]
    assert issued_header.startswith(f"{SESSION_COOKIE_NAME}={SESSION_TOKEN};")
    assert "HttpOnly" in issued_header
    assert "Max-Age=900" in issued_header
    assert "Path=/" in issued_header
    assert "SameSite=lax" in issued_header
    assert "Secure" in issued_header
    assert "Domain=" not in issued_header

    cleared = Response()
    clear_session_cookie(cleared)
    cleared_header = cleared.headers["set-cookie"]
    assert cleared_header.startswith(f'{SESSION_COOKIE_NAME}="";')
    assert "Max-Age=0" in cleared_header
    assert "expires=Thu, 01 Jan 1970 00:00:00 GMT" in cleared_header
    assert "HttpOnly" in cleared_header
    assert "Path=/" in cleared_header
    assert "SameSite=lax" in cleared_header
    assert "Secure" in cleared_header
    assert "Domain=" not in cleared_header

    invitation = Response()
    set_invitation_identity_cookie(invitation, SESSION_TOKEN, max_age=600)
    invitation_header = invitation.headers["set-cookie"]
    assert invitation_header.startswith(f"{INVITATION_IDENTITY_COOKIE_NAME}={SESSION_TOKEN};")
    assert "HttpOnly" in invitation_header
    assert "Max-Age=600" in invitation_header
    assert "SameSite=lax" in invitation_header
    assert "Secure" in invitation_header


@pytest.mark.parametrize(
    "origin",
    [
        "http://dashboard.example.test",
        "https://*.example.test",
        "https://user@example.test",
        "https://example.test/",
        "https://example.test/path",
        "https://example.test?query=1",
        "https://example.test#fragment",
        " https://example.test",
    ],
)
def test_unsafe_or_ambiguous_configured_origins_are_rejected(origin: str):
    with pytest.raises(ValueError):
        BrowserSecurity(csrf_hmac_key=b"k" * 32, allowed_origins=frozenset({origin}))


def test_local_http_origin_is_normalized_and_secrets_are_not_represented():
    security = BrowserSecurity(
        csrf_hmac_key=b"secret-key-material-that-must-hide",
        allowed_origins=frozenset({"http://LOCALHOST:80"}),
    )
    assert security.allowed_origins == frozenset({"http://localhost"})
    assert "secret-key-material" not in repr(security)
    proof = BrowserMutationProof(session_token=SESSION_TOKEN, origin=ORIGIN)
    assert SESSION_TOKEN not in repr(proof)
    logout = BrowserLogoutProof(
        session_token=SESSION_TOKEN,
        session_kind="tenant",
        origin=ORIGIN,
    )
    assert SESSION_TOKEN not in repr(logout)
    invitation = BrowserInvitationMutationProof(
        identity_proof_token=SESSION_TOKEN,
        origin=ORIGIN,
    )
    assert SESSION_TOKEN not in repr(invitation)


def test_invalid_key_cookie_lifetime_and_session_token_fail_closed(
    browser_security: BrowserSecurity,
):
    with pytest.raises(ValueError, match="at least 32 bytes"):
        BrowserSecurity(csrf_hmac_key=b"short", allowed_origins=frozenset({ORIGIN}))
    with pytest.raises(ValueError, match="max_age"):
        set_session_cookie(Response(), SESSION_TOKEN, max_age=0)
    with pytest.raises(InvalidOpaqueSessionToken):
        browser_security.issue_csrf_token("not-a-session")


def test_mutation_guard_rejects_safe_method_misconfiguration(
    browser_security: BrowserSecurity,
):
    request = httpx.Request("GET", "https://api.example.test/test")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": request.headers.raw,
    }
    with pytest.raises(BrowserRequestRejected):
        browser_security.validate_mutation(Request(scope))
