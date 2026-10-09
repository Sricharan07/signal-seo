"""Owner-only team settings; invitations never query recipient account existence."""

import json
from typing import Annotated, Literal, Protocol, runtime_checkable
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.invitations import (
    InvitationConflict,
    InvitationDenied,
    IssuedInvitation,
    normalize_invitation_email,
)

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    BrowserRequestRejected,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class TeamInvitationCommand(Contract):
    email: str = Field(min_length=3, max_length=320)
    role_key: Literal["viewer", "analyst", "editor", "approver", "admin"]

    @field_validator("email")
    @classmethod
    def email_condition(cls, value: str) -> str:
        return normalize_invitation_email(value)


@runtime_checkable
class BrowserTeamGateway(Protocol):
    async def owner_team(self, *, session_token: str, site_id: UUID) -> dict: ...

    async def issue_team_invitation(
        self,
        *,
        session_token: str,
        site_id: UUID,
        email: str,
        role_key: str,
    ) -> IssuedInvitation: ...


@runtime_checkable
class BrowserInvitationRevocationGateway(Protocol):
    async def revoke_team_invitation(
        self, *, session_token: str, site_id: UUID, invitation_id: UUID
    ) -> dict: ...


def mount_team_http(application: FastAPI, gateway: object | None) -> None:
    def result(status: int, code: str):
        return JSONResponse(
            status_code=status, content={"code": code}, headers={"Cache-Control": "no-store"}
        )

    @application.get("/v1/sites/{site_id}/team", tags=["identity"])
    async def read(request: Request, site_id: UUID):
        if not isinstance(gateway, BrowserTeamGateway):
            return result(503, "TEAM_UNAVAILABLE")
        try:
            token = exact_cookie(request, SESSION_COOKIE_NAME)
            return JSONResponse(
                await gateway.owner_team(session_token=token, site_id=site_id),
                headers={"Cache-Control": "no-store"},
            )
        except (BrowserRequestRejected, InvitationDenied, InvalidSession, AuthorizationDenied):
            return result(403, "TEAM_DENIED")
        except Exception:
            return result(503, "TEAM_UNAVAILABLE")

    @application.post("/v1/sites/{site_id}/invitations", tags=["identity"])
    async def issue(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        if not isinstance(gateway, BrowserTeamGateway):
            return result(503, "TEAM_UNAVAILABLE")
        try:
            if request.headers.get("content-type") != "application/json" or request.headers.get(
                "content-encoding"
            ):
                raise ValueError
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 2048:
                    raise ValueError

            def unique(pairs):
                document = {}
                for key, value in pairs:
                    if key in document:
                        raise ValueError
                    document[key] = value
                return document

            command = TeamInvitationCommand.model_validate(
                json.loads(raw.decode("utf-8"), object_pairs_hook=unique),
                strict=True,
            )
        except ValueError:
            return result(422, "INVITATION_REQUEST_INVALID")
        try:
            invitation = await gateway.issue_team_invitation(
                session_token=proof.session_token,
                site_id=site_id,
                email=command.email,
                role_key=command.role_key,
            )
            if invitation.site_id != site_id or invitation.role_key != command.role_key:
                raise RuntimeError
            return JSONResponse(
                {
                    "schema_version": 1,
                    "invitation_id": str(invitation.id),
                    "site_id": str(invitation.site_id),
                    "token": invitation.token,
                    "expires_at": invitation.expires_at.isoformat(),
                    "delivery": "not_emailed",
                },
                status_code=201,
                headers={"Cache-Control": "no-store"},
            )
        except (InvitationDenied, AuthorizationDenied, InvalidSession):
            return result(403, "INVITATION_ISSUANCE_DENIED")
        except InvitationConflict:
            return result(409, "INVITATION_PENDING")
        except Exception:
            return result(503, "INVITATION_ISSUANCE_UNCONFIRMED")

    @application.post("/v1/sites/{site_id}/invitations/{invitation_id}/revoke", tags=["identity"])
    async def revoke(
        request: Request,
        site_id: UUID,
        invitation_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        if not isinstance(gateway, BrowserInvitationRevocationGateway):
            return result(503, "TEAM_UNAVAILABLE")
        try:
            if request.headers.get("content-type") != "application/json" or request.headers.get(
                "content-encoding"
            ):
                raise ValueError
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 128:
                    raise ValueError
            if json.loads(raw.decode("utf-8")) != {} or invitation_id.version != 4:
                raise ValueError
        except ValueError:
            return result(422, "INVITATION_REQUEST_INVALID")
        try:
            revoked = await gateway.revoke_team_invitation(
                session_token=proof.session_token, site_id=site_id, invitation_id=invitation_id
            )
            if (
                set(revoked)
                != {"schema_version", "invitation_id", "site_id", "revoked_at", "durability"}
                or revoked["schema_version"] != 1
                or revoked["invitation_id"] != str(invitation_id)
                or revoked["site_id"] != str(site_id)
                or not isinstance(revoked["revoked_at"], str)
                or revoked["durability"] != "AUTHORITY_DURABILITY_PENDING"
            ):
                raise RuntimeError
            return JSONResponse(revoked, headers={"Cache-Control": "no-store"})
        except (InvitationDenied, AuthorizationDenied, InvalidSession):
            return result(403, "INVITATION_REVOCATION_DENIED")
        except Exception:
            return result(503, "INVITATION_REVOCATION_UNCONFIRMED")
