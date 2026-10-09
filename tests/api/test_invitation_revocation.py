"""Revocation HTTP proof, closed command, redaction and uncertain-outcome contract."""

from uuid import uuid4

import pytest
from signal_core.invitations import InvitationDenied
from test_team_invitations import Gateway, client


@pytest.fixture
def anyio_backend():
    return "asyncio"


class RevocationGateway(Gateway):
    async def revoke_team_invitation(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return dict(
            schema_version=1,
            invitation_id=str(kwargs["invitation_id"]),
            site_id=str(kwargs["site_id"]),
            revoked_at="2026-10-04T16:00:00Z",
            durability="AUTHORITY_DURABILITY_PENDING",
        )


@pytest.mark.anyio
async def test_revocation_returns_only_local_denial_with_pending_recovery_receipt():
    gateway = RevocationGateway()
    api, headers = client(gateway)
    site, invitation = uuid4(), uuid4()
    async with api:
        response = await api.post(
            f"/v1/sites/{site}/invitations/{invitation}/revoke", headers=headers, json={}
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["durability"] == "AUTHORITY_DURABILITY_PENDING"
    assert gateway.calls == [dict(session_token="s" * 43, site_id=site, invitation_id=invitation)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field,value",
    [
        ("Origin", None),
        ("Origin", "https://evil.test"),
        ("Sec-Fetch-Site", "cross-site"),
        ("X-CSRF-Token", None),
        ("X-CSRF-Token", "f" * 43),
        ("Cookie", None),
    ],
)
async def test_revocation_requires_browser_proof_before_any_authority_call(field, value):
    gateway = RevocationGateway()
    api, headers = client(gateway)
    if value is None:
        headers.pop(field)
    else:
        headers[field] = value
    async with api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/invitations/{uuid4()}/revoke", headers=headers, json={}
        )
    assert response.status_code in {401, 403}
    assert gateway.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body", ['{"role_key":"owner"}', '{"tenant_id":"forged"}', "[]", "x" * 129]
)
async def test_revocation_command_has_no_extra_authority_fields(body):
    gateway = RevocationGateway()
    api, headers = client(gateway)
    headers["Content-Type"] = "application/json"
    async with api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/invitations/{uuid4()}/revoke", headers=headers, content=body
        )
    assert response.status_code == 422
    assert gateway.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error,status,code",
    [
        (InvitationDenied(), 403, "INVITATION_REVOCATION_DENIED"),
        (RuntimeError("private database details"), 503, "INVITATION_REVOCATION_UNCONFIRMED"),
    ],
)
async def test_revocation_denial_and_database_failure_are_generic(error, status, code):
    api, headers = client(RevocationGateway(error))
    async with api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/invitations/{uuid4()}/revoke", headers=headers, json={}
        )
    assert response.status_code == status
    assert response.json() == {"code": code}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.anyio
async def test_revocation_without_composition_is_unavailable():
    api, headers = client(None)
    async with api:
        response = await api.post(
            f"/v1/sites/{uuid4()}/invitations/{uuid4()}/revoke", headers=headers, json={}
        )
    assert response.status_code == 503
