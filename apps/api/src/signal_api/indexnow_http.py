"""Owner-scoped, evidence-only IndexNow connector projection."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg import Connection
from pydantic import UUID4, Field
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.database import _clean_transaction
from signal_core.indexnow import IndexNowService, IndexNowUnavailable
from signal_core.indexnow_recipe import seal_indexnow_key_recipe
from signal_core.indexnow_secrets import OpenBaoIndexNowKeys
from signal_core.recovery_authority import OpenBaoRecoveryAuthority
from signal_core.session_tokens import hash_session_token

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    BrowserRequestRejected,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class IndexNowReceipt(Contract):
    change_id: UUID
    urls: tuple[str, ...] = Field(min_length=1, max_length=32)
    state: Literal["accepted", "rejected", "skipped", "retry", "outcome_unknown", "exhausted"]
    provider_status: int | None = Field(default=None, ge=100, le=599)
    reason: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,99}$")
    recorded_at: datetime


class IndexNowProjection(Contract):
    key_status: Literal["not_created", "pr_open", "deployed", "mismatch"]
    key_id: UUID | None
    reason: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,99}$")
    submissions: tuple[IndexNowReceipt, ...] = Field(max_length=20)


class CreateIndexNowKey(Contract):
    schema_version: Literal[1]
    request_id: UUID4


@dataclass(frozen=True)
class ComposedIndexNowReadGateway:
    connection_factory: Callable[[], Connection]
    keys: OpenBaoIndexNowKeys
    recovery_authority: OpenBaoRecoveryAuthority
    release_connection_factory: Callable[[], Connection] | None = None
    recipe_release_id: UUID | None = None
    credential: object | None = field(default=None, repr=False)
    github_transport_factory: object | None = field(default=None, repr=False)
    runner: object | None = None
    recipe_options: dict = field(default_factory=dict, repr=False)

    @property
    def key_creation_available(self) -> bool:
        return all(
            value is not None
            for value in (
                self.release_connection_factory,
                self.recipe_release_id,
                self.credential,
                self.github_transport_factory,
                self.runner,
            )
        )

    async def read(self, token: str, site_id: UUID) -> dict:
        generation = await self.recovery_authority.current_generation()
        with self.connection_factory() as connection:
            return IndexNowService(connection, self.keys, generation.value).read(
                session_token=token, site_id=site_id
            )

    async def create(self, token: str, site_id: UUID, request_id: UUID) -> dict:
        if not self.key_creation_available:
            raise IndexNowUnavailable("INDEXNOW_BUILD_UNAVAILABLE")
        generation = await self.recovery_authority.current_generation()
        with self.connection_factory() as connection:
            with _clean_transaction(connection):
                state = connection.execute(
                    "SELECT control.read_owner_github_pr_extension(%s,%s,%s)",
                    (hash_session_token(token), generation.value, site_id),
                ).fetchone()[0]
            if not isinstance(state, dict) or state.get("availability") != "observed":
                raise AuthorizationDenied()
            with _clean_transaction(connection):
                outcome = connection.execute(
                    "SELECT control.owner_github_pr_preflight(%s,%s,%s,%s)",
                    (
                        hash_session_token(token),
                        site_id,
                        generation.value,
                        UUID(state["binding_id"]),
                    ),
                ).fetchone()[0]
            if outcome == "step_up_required":
                raise IndexNowUnavailable("INDEXNOW_STEP_UP_REQUIRED")
            if outcome != "authorized":
                raise AuthorizationDenied()
            transport = await self.github_transport_factory(token, site_id)
            with self.release_connection_factory() as release_connection:
                sealed = await seal_indexnow_key_recipe(
                    IndexNowService(connection, self.keys, generation.value),
                    release_connection,
                    session_token=token,
                    site_id=site_id,
                    key_id=request_id,
                    extension_id=UUID(state["extension_id"]),
                    recipe_release_id=self.recipe_release_id,
                    build_idempotency_key=uuid5(
                        NAMESPACE_URL, f"indexnow-build:{site_id}:{request_id}"
                    ),
                    credential=self.credential,
                    github_transport=transport,
                    runner=self.runner,
                    **self.recipe_options,
                )
            return {
                "schema_version": 1,
                "state": "sealed",
                "revision_id": sealed.id,
                "revision_sha256": sealed.revision_sha256,
            }


def mount_indexnow_http(app: FastAPI, gateway: ComposedIndexNowReadGateway | None) -> None:
    @app.post("/v1/sites/{site_id}/indexnow/key", tags=["connectors"])
    async def create(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        from signal_api.main import _strict_json_request

        if gateway is None:
            return JSONResponse(status_code=503, content={"code": "INDEXNOW_UNAVAILABLE"})
        try:
            command = await _strict_json_request(request, CreateIndexNowKey, maximum=1024)
            return JSONResponse(
                content=jsonable_encoder(
                    await gateway.create(proof.session_token, site_id, command.request_id)
                )
            )
        except IndexNowUnavailable as error:
            return JSONResponse(
                status_code=403 if error.code == "INDEXNOW_STEP_UP_REQUIRED" else 503,
                content={
                    "code": "INDEXNOW_STEP_UP_REQUIRED"
                    if error.code == "INDEXNOW_STEP_UP_REQUIRED"
                    else "INDEXNOW_UNAVAILABLE"
                },
            )
        except (InvalidSession, BrowserRequestRejected):
            return JSONResponse(status_code=401, content={"code": "INDEXNOW_SESSION_REJECTED"})
        except AuthorizationDenied:
            return JSONResponse(status_code=403, content={"code": "INDEXNOW_AUTHORITY_DENIED"})
        except ValueError:
            return JSONResponse(status_code=422, content={"code": "INDEXNOW_REQUEST_INVALID"})
        except Exception:
            return JSONResponse(status_code=503, content={"code": "INDEXNOW_UNAVAILABLE"})

    @app.get("/v1/sites/{site_id}/indexnow", tags=["connectors"])
    async def read(request: Request, site_id: UUID):
        if gateway is None:
            return JSONResponse(status_code=503, content={"code": "INDEXNOW_UNAVAILABLE"})
        try:
            token = exact_cookie(request, SESSION_COOKIE_NAME)
            if token is None:
                raise InvalidSession()
            projection = IndexNowProjection.model_validate(await gateway.read(token, site_id))
            return JSONResponse(
                content=jsonable_encoder(
                    {
                        "schema_version": 1,
                        "site_id": site_id,
                        "correlation_id": request.state.correlation_id,
                        "key_creation_available": gateway.key_creation_available,
                        **projection.model_dump(),
                    }
                )
            )
        except (InvalidSession, BrowserRequestRejected):
            return JSONResponse(status_code=401, content={"code": "INDEXNOW_SESSION_REJECTED"})
        except AuthorizationDenied:
            return JSONResponse(status_code=403, content={"code": "INDEXNOW_AUTHORITY_DENIED"})
        except Exception:
            return JSONResponse(status_code=503, content={"code": "INDEXNOW_UNAVAILABLE"})
