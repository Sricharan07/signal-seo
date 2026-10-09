"""Current-owner connector controls; PR grants never perform repository writes."""

import json
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import Field, TypeAdapter, field_validator
from signal_core.bing_binding import (
    begin_bing_authorization,
    complete_bing_authorization,
    confirm_bing_binding,
    revoke_bing_binding,
)
from signal_core.bing_secrets import OpenBaoBingSecrets
from signal_core.database import _clean_transaction
from signal_core.github_app import GitHubRepositoryTarget
from signal_core.github_pr_extension import (
    GitHubPrExtensionUnavailable,
    observe_github_pr_extension,
    prepare_github_pr_extension,
    revoke_github_pr_extension,
)
from signal_core.github_read_binding import (
    GitHubBindingUnavailable,
    GitHubSharedEgressTransport,
    OpenBaoGitHubAppCredential,
    accept_github_unprotected_base,
    bind_github_read_repository,
    inspect_current_github_read_binding,
    revoke_github_read_binding,
)
from signal_core.gsc_binding import (
    begin_gsc_authorization,
    complete_gsc_authorization,
    confirm_gsc_binding,
    revoke_gsc_binding,
)
from signal_core.gsc_secrets import OpenBaoGscSecrets
from signal_core.integration_scope import IntegrationScope, load_integration_scope
from signal_core.json_objects import unique_object
from signal_core.session_tokens import hash_session_token

from signal_api.browser_security import (
    SESSION_COOKIE_NAME,
    BrowserMutationProof,
    exact_cookie,
    require_browser_mutation,
)
from signal_api.contracts import Contract


class GscBegin(Contract):
    operation: Literal["authorize"]


class GscComplete(Contract):
    operation: Literal["complete"]
    attempt_id: UUID
    state: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    code: str = Field(pattern=r"^[\x21-\x7e]{1,2048}$")


class GscConfirm(Contract):
    operation: Literal["confirm"]
    attempt_id: UUID
    property_resource_name: str = Field(min_length=1, max_length=2048)


class BingConfirm(Contract):
    operation: Literal["confirm"]
    attempt_id: UUID
    site_url: str = Field(min_length=1, max_length=2048)


class Disconnect(Contract):
    operation: Literal["revoke"]
    binding_id: UUID


class GithubBind(Contract):
    operation: Literal["bind"]
    idempotency_key: UUID


class GithubInspect(Contract):
    operation: Literal["inspect"]
    binding_id: UUID


class GithubPrPrepare(Contract):
    operation: Literal["prepare"]
    binding_id: UUID
    idempotency_key: UUID


class GithubPrFinish(Contract):
    operation: Literal["finish"]
    binding_id: UUID
    extension_id: UUID
    idempotency_key: UUID


class GithubPrRevoke(Contract):
    operation: Literal["revoke"]
    extension_id: UUID


class GithubAcceptUnprotected(Contract):
    operation: Literal["accept_unprotected"]
    binding_id: UUID
    accept_default_branch_unprotected: Literal[True]

    @field_validator("accept_default_branch_unprotected", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Explicit boolean risk acceptance required.")
        return value


GSC_COMMAND = TypeAdapter(
    Annotated[GscBegin | GscComplete | GscConfirm | Disconnect, Field(discriminator="operation")]
)
GITHUB_COMMAND = TypeAdapter(
    Annotated[
        GithubBind | GithubInspect | GithubAcceptUnprotected | Disconnect,
        Field(discriminator="operation"),
    ]
)
GITHUB_PR_COMMAND = TypeAdapter(
    Annotated[GithubPrPrepare | GithubPrFinish | GithubPrRevoke, Field(discriminator="operation")]
)
BING_COMMAND = TypeAdapter(
    Annotated[GscBegin | GscComplete | BingConfirm | Disconnect, Field(discriminator="operation")]
)


@dataclass(frozen=True, repr=False)
class OwnerConnectorGateway:
    connection_factory: object
    recovery_authority: object
    egress_factory: object
    recovery_options: dict = field(default_factory=dict, repr=False)
    integration_scope: IntegrationScope = field(
        default_factory=load_integration_scope, kw_only=True
    )

    @property
    def github_target(self):
        scope = self.integration_scope
        return GitHubRepositoryTarget(
            scope.github_installation_id,
            scope.github_owner,
            scope.github_repository,
            scope.github_base_branch,
            scope.github_content_path,
        )

    def scope(self, connection, token, site, generation, connector):
        if site != self.integration_scope.site or connector not in {"gsc", "github", "bing"}:
            raise ValueError("Exact dedicated connector scope required.")
        with _clean_transaction(connection):
            result = connection.execute(
                f"SELECT control.read_owner_{connector}_connector(%s,%s,%s)",
                (hash_session_token(token), generation, site),
            ).fetchone()[0]
        if not isinstance(result, dict) or result.get("availability") == "denied":
            raise ValueError("Current verified Owner/MFA authority required.")
        if connector == "gsc":
            if (
                result.get("availability") in {"bound", "reauth_required"}
                and result.get("property_resource_name") != self.integration_scope.gsc_property
            ):
                raise ValueError("GSC test resource changed.")
            if result.get("availability") == "selecting":
                result = {
                    **result,
                    "properties": [
                        item
                        for item in result["properties"]
                        if item
                        == {
                            "resource_name": self.integration_scope.gsc_property,
                            "property_type": "url_prefix",
                        }
                    ],
                }
                if not result["properties"]:
                    return {"availability": "unbound"}
        elif connector == "bing":
            if (
                result.get("availability") in {"bound", "reauth_required"}
                and result.get("site_url") != self.integration_scope.origin + "/"
            ):
                raise ValueError("Bing site changed.")
        elif result.get("availability") != "unbound" and (
            result.get("installation_id"),
            result.get("owner"),
            result.get("repository"),
            result.get("base_branch"),
            result.get("content_path"),
        ) != (
            self.github_target.installation_id,
            self.github_target.owner,
            self.github_target.repository,
            self.github_target.base_branch,
            self.github_target.content_path,
        ):
            raise ValueError("GitHub test target changed.")
        return result


@dataclass(frozen=True, repr=False)
class ComposedBingGateway(OwnerConnectorGateway):
    secrets_store: OpenBaoBingSecrets | None = None
    secret_options: dict = field(default_factory=dict, repr=False)

    async def read(self, token, site):
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        with self.connection_factory() as connection:
            result = self.scope(connection, token, site, generation.value, "bing")
            await self.secrets_store.client_credentials(**self.secret_options)
            return result

    async def control(self, token, site, command):
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        with self.connection_factory() as connection:
            current = self.scope(connection, token, site, generation.value, "bing")
            args = dict(session_token=token, site_id=site, recovery_generation=generation.value)
            redirect = self.integration_scope.origin + "/auth/bing/callback"
            if isinstance(command, GscBegin):
                if current["availability"] == "bound":
                    raise ValueError("Bing already bound.")
                started = await begin_bing_authorization(
                    connection,
                    self.secrets_store,
                    **args,
                    redirect_uri=redirect,
                    openbao_options=self.secret_options,
                )
                return {
                    "attempt_id": started.attempt_id,
                    "authorization_url": started.authorization_url,
                    "expires_in_seconds": started.expires_in_seconds,
                }
            if isinstance(command, BingConfirm):
                if command.site_url != self.integration_scope.origin + "/":
                    raise ValueError("Exact verified Bing site required.")
                bound = confirm_bing_binding(
                    connection, **args, attempt_id=command.attempt_id, site_url=command.site_url
                )
                return {"binding_id": bound.binding_id, "site_url": bound.site_url}
            if isinstance(command, Disconnect):
                await revoke_bing_binding(
                    connection,
                    self.secrets_store,
                    **args,
                    binding_id=command.binding_id,
                    openbao_options=self.secret_options,
                )
                return {"state": "revoked"}
            with self.egress_factory(token, site, generation.value) as egress:
                selected = await complete_bing_authorization(
                    connection,
                    self.secrets_store,
                    egress,
                    **args,
                    attempt_id=command.attempt_id,
                    state=command.state,
                    code=command.code,
                    redirect_uri=redirect,
                    token_operation_id=uuid4(),
                    discovery_operation_id=uuid4(),
                    openbao_options=self.secret_options,
                )
                return {"attempt_id": selected.attempt_id, "state": "selecting"}


@dataclass(frozen=True, repr=False)
class ComposedGscGateway(OwnerConnectorGateway):
    secrets_store: OpenBaoGscSecrets | None = None
    secret_options: dict = field(default_factory=dict, repr=False)

    async def read(self, token, site):
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        with self.connection_factory() as connection:
            result = self.scope(connection, token, site, generation.value, "gsc")
            await self.secrets_store.client_credentials(**self.secret_options)
            return result

    async def control(self, token, site, command):
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        with self.connection_factory() as connection:
            current = self.scope(connection, token, site, generation.value, "gsc")
            args = {
                "session_token": token,
                "site_id": site,
                "recovery_generation": generation.value,
            }
            redirect = self.integration_scope.origin + "/auth/gsc/callback"
            if isinstance(command, GscBegin):
                if current["availability"] == "bound":
                    raise ValueError("GSC already bound.")
                started = await begin_gsc_authorization(
                    connection,
                    self.secrets_store,
                    **args,
                    redirect_uri=redirect,
                    openbao_options=self.secret_options,
                )
                return {
                    "attempt_id": started.attempt_id,
                    "authorization_url": started.authorization_url,
                    "expires_in_seconds": started.expires_in_seconds,
                }
            if isinstance(command, GscConfirm):
                if command.property_resource_name != self.integration_scope.gsc_property:
                    raise ValueError("Only the approved URL-prefix property may be bound.")
                binding = confirm_gsc_binding(
                    connection,
                    **args,
                    attempt_id=command.attempt_id,
                    property_resource_name=command.property_resource_name,
                )
                return {
                    "binding_id": binding.binding_id,
                    "property_resource_name": binding.property_resource_name,
                }
            with self.egress_factory(token, site, generation.value) as egress:
                if isinstance(command, GscComplete):
                    selection = await complete_gsc_authorization(
                        connection,
                        self.secrets_store,
                        egress,
                        **args,
                        attempt_id=command.attempt_id,
                        state=command.state,
                        code=command.code,
                        redirect_uri=redirect,
                        token_operation_id=uuid4(),
                        discovery_operation_id=uuid4(),
                        openbao_options=self.secret_options,
                        allowed_property_resources=frozenset({self.integration_scope.gsc_property}),
                    )
                    return {"attempt_id": selection.attempt_id, "state": "selecting"}
                upstream = await revoke_gsc_binding(
                    connection,
                    self.secrets_store,
                    **args,
                    binding_id=command.binding_id,
                    egress=egress,
                    revocation_operation_id=uuid4(),
                    openbao_options=self.secret_options,
                )
                return {"state": "revoked", "upstream": "accepted" if upstream else "unknown"}


@dataclass(frozen=True, repr=False)
class ComposedGithubGateway(OwnerConnectorGateway):
    credential: OpenBaoGitHubAppCredential | None = None
    credential_options: dict = field(default_factory=dict, repr=False)

    def pr_state(self, connection, token, site, generation):
        with _clean_transaction(connection):
            result = connection.execute(
                "SELECT control.read_owner_github_pr_extension(%s,%s,%s)",
                (hash_session_token(token), generation, site),
            ).fetchone()[0]
        if not isinstance(result, dict) or result.get("availability") == "denied":
            raise ValueError("Current verified Owner/MFA authority required.")
        return result

    async def read_pr(self, token, site):
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        with self.connection_factory() as connection:
            self.scope(connection, token, site, generation.value, "github")
            return self.pr_state(connection, token, site, generation.value)

    async def control_pr(self, token, site, command):
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        with self.connection_factory() as connection:
            current = self.scope(connection, token, site, generation.value, "github")
            if current["availability"] != "active":
                raise ValueError("Current active read binding required.")
            binding_id = UUID(current["binding_id"])
            with _clean_transaction(connection):
                outcome = connection.execute(
                    "SELECT control.owner_github_pr_preflight(%s,%s,%s,%s)",
                    (hash_session_token(token), site, generation.value, binding_id),
                ).fetchone()[0]
            if outcome != "authorized":
                raise GitHubPrExtensionUnavailable(
                    "GITHUB_STEP_UP_REQUIRED"
                    if outcome == "step_up_required"
                    else "GITHUB_AUTHORITY_DENIED"
                )
            args = {
                "session_token": token,
                "site_id": site,
                "current_recovery_generation": generation.value,
                "owner_flow": True,
            }
            if isinstance(command, GithubPrRevoke):
                durability = revoke_github_pr_extension(
                    connection, **args, extension_id=command.extension_id
                )
                return {"state": "revoked", "durability": durability}
            if command.binding_id != binding_id:
                raise ValueError("Current exact read binding required.")
            if isinstance(command, GithubPrPrepare):
                prepared = prepare_github_pr_extension(
                    connection,
                    **args,
                    binding_id=binding_id,
                    idempotency_key=command.idempotency_key,
                )
                return {"state": prepared.status, "extension_id": prepared.id}
            state = self.pr_state(connection, token, site, generation.value)
            if (
                state.get("availability") != "prepared"
                or state.get("extension_id") != str(command.extension_id)
                or state.get("idempotency_key") != str(command.idempotency_key)
            ):
                raise ValueError("Current prepared PR grant required.")
            with self.egress_factory(token, site, generation.value) as egress:
                observed = await observe_github_pr_extension(
                    connection,
                    **args,
                    binding_id=binding_id,
                    idempotency_key=command.idempotency_key,
                    prepared_extension_id=command.extension_id,
                    credential=self.credential,
                    github_transport=GitHubSharedEgressTransport(egress),
                    **self.credential_options,
                )
            return {"state": observed.status, "extension_id": observed.id}

    async def read(self, token, site):
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        with self.connection_factory() as connection:
            result = self.scope(connection, token, site, generation.value, "github")
            await self.credential.credentials(
                transport=self.credential_options.get("openbao_transport"),
                verify=self.credential_options.get("openbao_verify", True),
            )
            return result

    async def control(self, token, site, command):
        generation = await self.recovery_authority.current_generation(**self.recovery_options)
        with self.connection_factory() as connection:
            current = self.scope(connection, token, site, generation.value, "github")
            args = {
                "session_token": token,
                "site_id": site,
                "current_recovery_generation": generation.value,
            }
            if isinstance(command, Disconnect):
                revoke_github_read_binding(connection, **args, binding_id=command.binding_id)
                return {"state": "revoked"}
            if (
                isinstance(command, GithubBind)
                and current["availability"] != "unbound"
                and current["availability"] != "failed"
            ):
                raise ValueError("An existing binding must be resolved or disconnected.")
            with self.egress_factory(token, site, generation.value) as egress:
                transport = GitHubSharedEgressTransport(egress)
                if isinstance(command, GithubAcceptUnprotected):
                    if current.get("binding_id") != str(command.binding_id):
                        raise ValueError("Current exact binding required.")
                    bound = await accept_github_unprotected_base(
                        connection,
                        **args,
                        binding_id=command.binding_id,
                        credential=self.credential,
                        github_transport=transport,
                        **self.credential_options,
                    )
                    return {"binding_id": bound.id, "state": bound.status}
                if isinstance(command, GithubBind):
                    bound = await bind_github_read_repository(
                        connection,
                        **args,
                        idempotency_key=command.idempotency_key,
                        target=self.github_target,
                        credential=self.credential,
                        github_transport=transport,
                        **self.credential_options,
                    )
                    return {"binding_id": bound.id, "state": bound.status}
                if current["availability"] != "active" or current.get("binding_id") != str(
                    command.binding_id
                ):
                    raise ValueError("Current active binding required.")
                snapshot = await inspect_current_github_read_binding(
                    connection,
                    **args,
                    binding_id=command.binding_id,
                    credential=self.credential,
                    github_transport=transport,
                    **self.credential_options,
                )
                return {
                    "state": "observed",
                    "repository_id": snapshot.repository_id,
                    "full_name": snapshot.full_name,
                    "base_sha": snapshot.base_sha,
                    "protected": snapshot.protected,
                }


async def _command(request, adapter):
    if request.headers.getlist("content-type") != ["application/json"] or request.headers.getlist(
        "content-encoding"
    ):
        raise ValueError
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            raise ValueError

    value = json.loads(body, object_pairs_hook=unique_object)
    return adapter.validate_json(json.dumps(value), strict=True)


def mount_owner_connector_http(application: FastAPI, connector: str, gateway):
    if connector not in {"gsc", "github", "github-pr", "bing"}:
        raise ValueError("Unknown owner connector route.")
    state_key = "browser_" + ("github" if connector == "github-pr" else connector)
    if connector != "github-pr":
        setattr(application.state, state_key, gateway)
    adapter = {
        "gsc": GSC_COMMAND,
        "github": GITHUB_COMMAND,
        "github-pr": GITHUB_PR_COMMAND,
        "bing": BING_COMMAND,
    }[connector]
    route = "/v1/sites/{site_id}/" + connector

    def unavailable():
        return JSONResponse({"availability": "unavailable"}, status_code=503)

    @application.get(route, tags=["connectors"])
    async def read(request: Request, site_id: UUID):
        gateway = getattr(application.state, state_key)
        if gateway is None:
            return unavailable()
        try:
            method = gateway.read_pr if connector == "github-pr" else gateway.read
            result = await method(exact_cookie(request, SESSION_COOKIE_NAME), site_id)
            return JSONResponse(jsonable_encoder(result), headers={"Cache-Control": "no-store"})
        except Exception:
            return JSONResponse({"code": "CONNECTOR_READ_DENIED"}, status_code=401)

    @application.post(route, tags=["connectors"])
    async def control(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ):
        gateway = getattr(application.state, state_key)
        if gateway is None:
            return unavailable()
        try:
            command = await _command(request, adapter)
        except Exception:
            return JSONResponse({"code": "CONNECTOR_COMMAND_REJECTED"}, status_code=422)
        try:
            method = gateway.control_pr if connector == "github-pr" else gateway.control
            result = await method(proof.session_token, site_id, command)
            return JSONResponse(jsonable_encoder(result), headers={"Cache-Control": "no-store"})
        except (GitHubBindingUnavailable, GitHubPrExtensionUnavailable) as error:
            if error.code == "GITHUB_STEP_UP_REQUIRED":
                return JSONResponse({"code": "CONNECTOR_STEP_UP_REQUIRED"}, status_code=403)
            return JSONResponse({"code": "CONNECTOR_COMMAND_DENIED"}, status_code=403)
        except Exception:
            return JSONResponse({"code": "CONNECTOR_COMMAND_DENIED"}, status_code=403)
