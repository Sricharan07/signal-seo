"""Owner invitation HTTP boundary: proof, strict inputs and honest delivery."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.invitations import InvitationConflict, InvitationDenied, IssuedInvitation

ORIGIN = "https://dashboard.example.test"
TOKEN = "s" * 43


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Gateway:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def owner_team(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"site_id": str(kwargs["site_id"]), "members": [], "invitations": []}

    async def issue_team_invitation(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return IssuedInvitation(
            id=uuid4(),
            audit_event_id=uuid4(),
            tenant_id=uuid4(),
            site_id=kwargs["site_id"],
            email_normalized=kwargs["email"],
            role_key=kwargs["role_key"],
            token="i" * 43,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )


def client(gateway):
    security = BrowserSecurity(
        csrf_hmac_key=b"team-invitation-test-proof-key!!!", allowed_origins=frozenset({ORIGIN})
    )
    app = create_app(
        settings=ApiSettings(environment="test"), browser_login=gateway, browser_security=security
    )
    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": security.issue_csrf_token(TOKEN),
    }
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://api.example.test",
    ), headers


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["viewer", "analyst", "editor", "approver", "admin"])
async def test_issue_returns_one_time_token_and_never_claims_email_delivery(role):
    gateway = Gateway()
    api, headers = client(gateway)
    site = uuid4()
    async with api:
        response = await api.post(
            f"/v1/sites/{site}/invitations",
            headers=headers,
            json={"email": "Invitee@example.test", "role_key": role},
        )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["delivery"] == "not_emailed"
    assert response.json()["token"] == "i" * 43
    assert "email" not in response.json()
    assert gateway.calls == [
        dict(session_token=TOKEN, site_id=site, email="invitee@example.test", role_key=role)
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field,value",
    [
        ("Origin", "https://evil.test"),
        ("Origin", None),
        ("Sec-Fetch-Site", "cross-site"),
        ("X-CSRF-Token", None),
        ("X-CSRF-Token", "f" * 43),
        ("Cookie", None),
    ],
)
async def test_missing_or_forged_browser_proof_never_reaches_issuance(field, value):
    gateway = Gateway()
    api, headers = client(gateway)
    if value is None:
        headers.pop(field)
    else:
        headers[field] = value
    async with api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/invitations",
            headers=headers,
            json={"email": "invitee@example.test", "role_key": "viewer"},
        )
    assert response.status_code in {401, 403}
    assert not gateway.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        '{"email":"invitee@example.test","role_key":"owner"}',
        '{"email":"invitee@example.test","role_key":"viewer","tenant_id":"forged"}',
        '{"email":"invitee@example.test","role_key":"viewer","role_key":"admin"}',
        '{"email":"bad","role_key":"viewer"}',
        '{"email":"invitee@example.test","role_key":true}',
        '{"email":"invitee@example.test","role_key":"viewer","ttl_seconds":999999}',
        "x" * 2049,
    ],
)
async def test_strict_issuance_rejects_escalation_unknown_fields_duplicates_and_size(body):
    gateway = Gateway()
    api, headers = client(gateway)
    headers["Content-Type"] = "application/json"
    async with api:
        response = await api.post(f"/v1/sites/{uuid4()}/invitations", headers=headers, content=body)
    assert response.status_code == 422
    assert not gateway.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error,status,code",
    [
        (InvitationDenied(), 403, "INVITATION_ISSUANCE_DENIED"),
        (InvitationConflict(), 409, "INVITATION_PENDING"),
        (RuntimeError("private account detail"), 503, "INVITATION_ISSUANCE_UNCONFIRMED"),
    ],
)
async def test_issuance_denial_conflict_and_provider_failure_are_redacted(error, status, code):
    api, headers = client(Gateway(error))
    async with api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/invitations",
            headers=headers,
            json={"email": "invitee@example.test", "role_key": "viewer"},
        )
    assert response.status_code == status
    assert response.json() == {"code": code}
    assert "account" not in response.text


@pytest.mark.anyio
async def test_no_account_existence_signal_and_owner_read_route():
    gateway = Gateway()
    api, headers = client(gateway)
    site = uuid4()
    async with api:
        responses = [
            await api.post(
                f"/v1/sites/{site}/invitations",
                headers=headers,
                json={"email": email, "role_key": "viewer"},
            )
            for email in ("known@example.test", "unknown@example.test")
        ]
        team = await api.get(f"/v1/sites/{site}/team", headers=headers)
    assert [r.status_code for r in responses] == [201, 201]
    assert [set(r.json()) for r in responses][0] == set(responses[1].json())
    assert all(r.json()["delivery"] == "not_emailed" for r in responses)
    assert team.status_code == 200


@pytest.mark.anyio
async def test_routes_without_composition_are_visibly_unavailable():
    api, headers = client(None)
    async with api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/invitations",
            headers=headers,
            json={"email": "invitee@example.test", "role_key": "viewer"},
        )
        team = await api.get(f"/v1/sites/{uuid4()}/team", headers=headers)
    assert response.status_code == team.status_code == 503


@pytest.mark.anyio
async def test_team_read_denies_nonowner_without_disclosing_members():
    api, headers = client(Gateway(InvitationDenied()))
    async with api:
        response = await api.get(f"/v1/sites/{uuid4()}/team", headers=headers)
    assert response.status_code == 403
    assert response.json() == {"code": "TEAM_DENIED"}
