"""HTTP-facing composition for the internal OIDC login pipeline."""

import ssl
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from functools import partial
from typing import Protocol, TypeVar, runtime_checkable
from uuid import UUID

import httpx2
from anyio import to_thread
from psycopg import Connection
from signal_core.artifact_keys import ArtifactKeyUnavailable, OpenBaoBrandArtifactKey
from signal_core.audit_findings import (
    AuditFinding,
    read_authenticated_findings,
    record_authenticated_local_fixture_finding,
)
from signal_core.brand_documents import (
    BrandDocument,
    UntrustedDocumentText,
    delete_brand_document,
    list_brand_documents,
    read_brand_document,
    upload_brand_document,
)
from signal_core.candidate_recipe_inbox import (
    CandidateRecipeInboxItem,
    CandidateReviewDecision,
    decide_authenticated_candidate_recipe_revision,
    read_authenticated_candidate_recipe_inbox,
)
from signal_core.commands import (
    AcceptedHumanCommand,
    HumanCommandStatus,
    accept_authenticated_snapshot,
    read_authenticated_snapshot_command,
    read_latest_authenticated_snapshot_command,
)
from signal_core.crawl_artifacts import ArtifactEncryptionKey, EncryptedLocalArtifactStore
from signal_core.crawl_http import PinnedHttpFetcher
from signal_core.github_delivery_observation import (
    GitHubDeliveryObservation,
    read_authenticated_github_delivery_observations,
)
from signal_core.github_pr_delivery import (
    GitHubPrOperation,
    read_authenticated_github_pr_operations,
)
from signal_core.identity_memberships import IdentityMembership, list_identity_memberships
from signal_core.invitation_acceptance import AcceptedInvitation, accept_site_invitation
from signal_core.login_flow import (
    CompletedInvitationIdentityVerification,
    CompletedOidcLogin,
    InitiatedOidcLogin,
    complete_oidc_login,
    initiate_oidc_login,
)
from signal_core.model_reasoning import MetadataDraftError, OpenAIResponsesAdapter
from signal_core.oidc_login import (
    OidcClientRegistration,
    OidcLoginPurpose,
    validate_oidc_attempt_ttl,
)
from signal_core.origin_verification import (
    OriginChallenge,
    PreparedOriginVerification,
    VerifiedOrigin,
    issue_origin_challenge,
    observe_origin_proof,
    prepare_origin_verification,
    record_origin_verification,
)
from signal_core.page_observations import (
    PageAnalysis,
    PageObservation,
    observe_verified_homepage,
    prepare_authenticated_page_observation,
    read_latest_authenticated_page_observation,
    record_authenticated_page_observation,
)
from signal_core.pkce_secrets import OpenBaoPkceClient
from signal_core.proposals import (
    ApprovalDecision,
    LocalProposal,
    ProposalNotReady,
    begin_authenticated_fixture_model_run,
    begin_authenticated_verified_homepage_model_run,
    build_fixture_metadata_task,
    build_verified_homepage_metadata_task,
    complete_authenticated_fixture_model_proposal,
    complete_authenticated_verified_homepage_model_proposal,
    decide_authenticated_local_fixture_approval,
    fail_authenticated_fixture_model_run,
    fail_authenticated_verified_homepage_model_run,
    prepare_authenticated_local_fixture_proposal,
    read_authenticated_local_fixture_proposals,
)
from signal_core.recovery_authority import OpenBaoRecoveryAuthority, RecoveryAuthorityError
from signal_core.session_issuance import (
    DEFAULT_SESSION_POLICY,
    IssuedTenantSession,
    SessionPolicy,
    issue_tenant_session,
)
from signal_core.session_management import (
    BrowserSessionKind,
    CurrentTenantSession,
    SelectedSiteContext,
    TenantSiteDirectory,
    inspect_tenant_session,
    list_tenant_sites,
    revoke_browser_session,
    select_session_site,
)
from signal_core.site_onboarding import OnboardedSite, onboard_site
from signal_core.standing_authorization import (
    RevokedGrant,
    StandingGrant,
    StandingGrantRequest,
    StandingGrantView,
    grant_standing_authorization,
    read_standing_authorization,
    revoke_standing_authorization,
)
from signal_core.team_invitations import (
    issue_owner_team_invitation,
    read_owner_team,
    revoke_owner_team_invitation,
)
from signal_core.weekly_control import (
    PauseResult,
    read_site_pause,
    read_weekly_report,
    set_site_paused,
)

_Result = TypeVar("_Result")


class BrowserAuthenticationUnavailable(Exception):
    """The configured login service cannot safely acquire its dependencies."""


class TenantSelectionDenied(Exception):
    """The identity has no selectable current membership for that tenant."""


@runtime_checkable
class BrowserLoginGateway(Protocol):
    """Narrow interface consumed by the FastAPI ingress."""

    attempt_ttl_seconds: int

    async def initiate(
        self, *, return_path: str, purpose: OidcLoginPurpose = "login"
    ) -> InitiatedOidcLogin: ...

    async def complete(
        self, *, state: str, browser_binding: str, code: str
    ) -> CompletedOidcLogin | CompletedInvitationIdentityVerification: ...


@runtime_checkable
class BrowserInvitationGateway(Protocol):
    """Invitation acceptance backed by a short-lived verified-identity proof."""

    async def accept_invitation(
        self,
        *,
        identity_proof_token: str,
        invitation_id: UUID,
        token: str,
        display_name: str,
    ) -> AcceptedInvitation: ...


@runtime_checkable
class BrowserTenantGateway(Protocol):
    """Pre-tenant account operations backed by the global identity session."""

    async def memberships(
        self, *, identity_session_token: str
    ) -> tuple[IdentityMembership, ...]: ...

    async def select_tenant(
        self, *, identity_session_token: str, tenant_id: object
    ) -> IssuedTenantSession: ...


@runtime_checkable
class BrowserSessionGateway(Protocol):
    """Current-session inspection and explicit browser logout operations."""

    async def current_session(self, *, session_token: str) -> CurrentTenantSession: ...

    async def logout(
        self, *, session_token: str, presented_session_kind: BrowserSessionKind
    ) -> bool: ...


@runtime_checkable
class BrowserSiteDirectoryGateway(Protocol):
    """Read current site memberships without accepting browser-owned scope."""

    async def site_directory(self, *, session_token: str) -> TenantSiteDirectory: ...


@runtime_checkable
class BrowserSiteSelectionGateway(Protocol):
    """Select one current site without trusting browser-owned tenant scope."""

    async def select_site(
        self,
        *,
        session_token: str,
        site_id: UUID,
        expected_session_version: int,
    ) -> SelectedSiteContext: ...


@runtime_checkable
class BrowserSiteOnboardingGateway(Protocol):
    """Create an unverified site without browser-owned tenant or user scope."""

    async def onboard_site(
        self,
        *,
        session_token: str,
        name: str,
        primary_origin: str,
        timezone: str,
        reporting_currency: str,
        expected_session_version: int,
        idempotency_key: UUID,
    ) -> OnboardedSite: ...


@runtime_checkable
class BrowserStandingGrantGateway(Protocol):
    """Owner-only grant controls bound to current selected site and OpenBao generation."""

    async def grant_standing_authorization(
        self, *, session_token: str, request: StandingGrantRequest
    ) -> StandingGrant: ...

    async def revoke_standing_authorization(
        self, *, session_token: str, site_id: UUID, grant_id: UUID
    ) -> RevokedGrant: ...

    async def read_standing_authorization(
        self, *, session_token: str, site_id: UUID
    ) -> StandingGrantView: ...


@runtime_checkable
class BrowserWeeklyGateway(Protocol):
    async def read_site_pause(self, *, session_token: str, site_id: UUID) -> dict: ...

    async def set_site_paused(
        self, *, session_token: str, site_id: UUID, paused: bool
    ) -> PauseResult: ...

    async def read_weekly_report(
        self, *, session_token: str, site_id: UUID, week_start: date | None
    ) -> dict | None: ...


@runtime_checkable
class BrowserOriginVerificationGateway(Protocol):
    """Verify only the currently selected site's exact configured origin."""

    async def issue_origin_challenge(
        self,
        *,
        session_token: str,
        site_id: UUID,
        origin: str,
        idempotency_key: UUID,
    ) -> OriginChallenge: ...

    async def verify_origin(
        self,
        *,
        session_token: str,
        site_id: UUID,
        challenge_id: UUID,
        origin: str,
        idempotency_key: UUID,
    ) -> VerifiedOrigin: ...


@runtime_checkable
class BrowserCommandGateway(Protocol):
    """Harmless command operations under a current tenant-session proof."""

    async def accept_snapshot_command(
        self,
        *,
        session_token: str,
        site_id: UUID,
        idempotency_key: str,
    ) -> AcceptedHumanCommand: ...

    async def snapshot_command_status(
        self,
        *,
        session_token: str,
        site_id: UUID,
        command_id: UUID,
    ) -> HumanCommandStatus: ...

    async def latest_snapshot_command(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> HumanCommandStatus: ...


@runtime_checkable
class BrowserFindingGateway(Protocol):
    """Page evidence and finding operations under current site authority."""

    async def analyze_local_fixture(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> AuditFinding: ...

    async def findings(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> tuple[AuditFinding, ...]: ...

    async def analyze_verified_homepage(
        self,
        *,
        session_token: str,
        site_id: UUID,
        idempotency_key: UUID,
    ) -> PageAnalysis: ...

    async def latest_page_observation(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> PageObservation | None: ...


@runtime_checkable
class BrowserProposalGateway(Protocol):
    """Evidence-bound proposal and decision operations under current authority."""

    async def prepare_local_fixture_proposal(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> LocalProposal: ...

    async def prepare_model_fixture_proposal(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> LocalProposal: ...

    async def prepare_verified_homepage_proposal(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> LocalProposal: ...

    async def local_fixture_proposals(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> tuple[LocalProposal, ...]: ...

    async def decide_local_fixture_approval(
        self,
        *,
        session_token: str,
        site_id: UUID,
        approval_request_id: UUID,
        revision_sha256: str,
        decision_id: UUID,
        decision: ApprovalDecision,
    ) -> LocalProposal: ...


@runtime_checkable
class BrowserBrandDocumentGateway(Protocol):
    async def upload_brand_document(
        self,
        *,
        session_token: str,
        site_id: UUID,
        filename: str,
        body: bytes,
        supersedes_id: UUID | None,
    ) -> BrandDocument: ...

    async def list_brand_documents(
        self, *, session_token: str, site_id: UUID
    ) -> tuple[BrandDocument, ...]: ...

    async def delete_brand_document(
        self, *, session_token: str, site_id: UUID, document_id: UUID
    ) -> str: ...

    async def read_brand_document(
        self, *, session_token: str, site_id: UUID, document_id: UUID
    ) -> UntrustedDocumentText: ...


@runtime_checkable
class BrowserCandidateInboxGateway(Protocol):
    """Exact sealed candidate revision review operations."""

    async def candidate_recipe_inbox(
        self, *, session_token: str, site_id: UUID
    ) -> tuple[CandidateRecipeInboxItem, ...]: ...

    async def decide_candidate_recipe_revision(
        self,
        *,
        session_token: str,
        site_id: UUID,
        revision_id: UUID,
        revision_sha256: str,
        decision_id: UUID,
        decision: CandidateReviewDecision,
    ) -> CandidateRecipeInboxItem: ...


@runtime_checkable
class BrowserGithubPrOperationsGateway(Protocol):
    """Read owner-visible PR operation evidence without granting write authority."""

    async def github_pr_operations(
        self, *, session_token: str, site_id: UUID
    ) -> tuple[GitHubPrOperation, ...]: ...


@runtime_checkable
class BrowserGithubDeliveryGateway(Protocol):
    async def github_delivery_observations(
        self, *, session_token: str, site_id: UUID
    ) -> tuple[GitHubDeliveryObservation, ...]: ...


@dataclass(frozen=True)
class ComposedBrowserLogin:
    """Acquire one clean identity connection per composed login operation."""

    connection_factory: Callable[[], Connection] = field(repr=False)
    registration: OidcClientRegistration
    pkce_writer: OpenBaoPkceClient
    pkce_consumer: OpenBaoPkceClient
    recovery_authority: OpenBaoRecoveryAuthority
    origin_fetcher: PinnedHttpFetcher | None = field(default=None, repr=False)
    policy: SessionPolicy = DEFAULT_SESSION_POLICY
    attempt_ttl_seconds: int = 300
    oidc_transport: httpx2.AsyncBaseTransport | None = field(default=None, repr=False)
    oidc_verify: ssl.SSLContext | bool = field(default=True, repr=False)
    pkce_transport: httpx2.AsyncBaseTransport | None = field(default=None, repr=False)
    pkce_verify: ssl.SSLContext | bool = field(default=True, repr=False)
    recovery_transport: httpx2.AsyncBaseTransport | None = field(default=None, repr=False)
    recovery_verify: ssl.SSLContext | bool = field(default=True, repr=False)
    model_adapter: OpenAIResponsesAdapter | None = field(default=None, repr=False)
    brand_store: EncryptedLocalArtifactStore | None = field(default=None, repr=False)
    brand_key_reader: OpenBaoBrandArtifactKey | None = field(default=None, repr=False)
    brain_extractor: object | None = field(default=None, repr=False)
    writer_service: object | None = field(default=None, repr=False)
    writer_connection_factory: Callable[[], Connection] | None = field(default=None, repr=False)
    visibility_connection_factory: Callable[[], Connection] | None = field(default=None, repr=False)
    visibility_runtime_configured: bool = False
    strategy_research: object | None = field(default=None, repr=False)
    verified_assertion_observer: Callable | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            not callable(self.connection_factory)
            or not isinstance(self.registration, OidcClientRegistration)
            or not isinstance(self.pkce_writer, OpenBaoPkceClient)
            or not isinstance(self.pkce_consumer, OpenBaoPkceClient)
            or not isinstance(self.recovery_authority, OpenBaoRecoveryAuthority)
            or (
                self.brand_store is not None
                and not isinstance(self.brand_store, EncryptedLocalArtifactStore)
            )
            or (
                self.brand_key_reader is not None
                and not isinstance(self.brand_key_reader, OpenBaoBrandArtifactKey)
            )
            or (
                self.origin_fetcher is not None
                and not isinstance(self.origin_fetcher, PinnedHttpFetcher)
            )
            or not isinstance(self.policy, SessionPolicy)
            or (
                self.verified_assertion_observer is not None
                and not callable(self.verified_assertion_observer)
            )
            or (
                self.model_adapter is not None
                and not isinstance(self.model_adapter, OpenAIResponsesAdapter)
            )
        ):
            raise ValueError("Browser login dependencies must be validated configurations.")
        validate_oidc_attempt_ttl(self.attempt_ttl_seconds)

    async def upload_brand_document(
        self,
        *,
        session_token: str,
        site_id: UUID,
        filename: str,
        body: bytes,
        supersedes_id: UUID | None,
    ) -> BrandDocument:
        from signal_core.brand_documents import BrandDocumentUnavailable

        if self.brand_store is None or self.brand_key_reader is None:
            raise BrandDocumentUnavailable("document_storage_unavailable")
        key = await self._brand_key()
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                upload_brand_document,
                store=self.brand_store,
                key=key,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
                filename=filename,
                body=body,
                supersedes_id=supersedes_id,
            )
        )

    async def owner_team(self, *, session_token: str, site_id: UUID) -> dict:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_owner_team,
                session_token=session_token,
                site_id=site_id,
                current_recovery_generation=generation,
            )
        )

    async def issue_team_invitation(
        self,
        *,
        session_token: str,
        site_id: UUID,
        email: str,
        role_key: str,
    ):
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                issue_owner_team_invitation,
                session_token=session_token,
                site_id=site_id,
                current_recovery_generation=generation,
                email=email,
                role_key=role_key,
            )
        )

    async def revoke_team_invitation(
        self, *, session_token: str, site_id: UUID, invitation_id: UUID
    ) -> dict:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                revoke_owner_team_invitation,
                session_token=session_token,
                site_id=site_id,
                invitation_id=invitation_id,
                current_recovery_generation=generation,
            )
        )

    @property
    def business_brain_configured(self) -> bool:
        from signal_core.business_brain_extraction import BusinessBrainExtractor

        return (
            isinstance(self.brain_extractor, BusinessBrainExtractor)
            and self.brain_extractor.configured
        )

    @property
    def content_writer_configured(self) -> bool:
        from signal_core.content_writer_service import ContentWriterService

        return (
            isinstance(self.writer_service, ContentWriterService) and self.writer_service.configured
        )

    @property
    def content_candidate_configured(self) -> bool:
        from signal_core.content_writer_service import ContentWriterService

        return (
            isinstance(self.writer_service, ContentWriterService)
            and self.writer_service.candidate_configured
        )

    async def read_content_writer(self, *, session_token: str, site_id: UUID) -> dict:
        from signal_core.content_writer_service import writer_call

        generation = await self._current_generation()
        return await self._run_writer_database(
            partial(
                writer_call,
                session_token=session_token,
                generation=generation,
                site_id=site_id,
                name="read",
            )
        )

    async def read_seo_strategy(
        self, *, session_token: str, site_id: UUID, snapshot_id: UUID | None = None
    ) -> dict:
        from signal_core.seo_strategy_service import strategy_call

        generation = await self._current_generation()
        return await self._run_writer_database(
            partial(
                strategy_call,
                session_token=session_token,
                generation=generation,
                site_id=site_id,
                action="read",
                args=(snapshot_id,),
            )
        )

    async def assistant(
        self, *, session_token: str, site_id: UUID, action: str, values: dict
    ) -> dict:
        from signal_core.ask_signal_service import (
            add_memory,
            database_call,
            read_assistant,
            send_message,
        )

        generation = await self._current_generation()
        common = {"session_token": session_token, "generation": generation, "site_id": site_id}
        reasoner = self.model_adapter._reasoner() if self.model_adapter else None
        if reasoner is None and self.brain_extractor is not None:
            reasoner = self.brain_extractor.model
        if reasoner is None and self.writer_service is not None:
            reasoner = self.writer_service.model.reasoner if self.writer_service.model else None
        connection = await self._open_connection()
        try:
            if action == "message":
                return await send_message(connection, reasoner=reasoner, **common, **values)
            if action == "overview":
                operation = partial(read_assistant, reasoner=reasoner, **common)
            elif action == "add_memory":
                operation = partial(add_memory, **common, **values)
            elif action in {"create", "read", "memory", "forget"}:
                operation = partial(database_call, **common, action=action, args=values)
            else:
                raise ValueError("Unknown assistant operation.")
            return await to_thread.run_sync(operation, connection)
        finally:
            await self._close_connection(connection)

    async def mutate_seo_strategy(
        self, *, session_token: str, site_id: UUID, action: str, values: dict
    ) -> dict:
        from signal_core.seo_strategy_service import decide_strategy, rebuild_strategy

        generation = await self._current_generation()
        common = {"session_token": session_token, "generation": generation, "site_id": site_id}
        if action == "refresh":
            connection = await self._open_writer_connection()
            try:
                return await rebuild_strategy(connection, **common, research=self.strategy_research)
            finally:
                await self._close_connection(connection)
        if action == "decide":
            return await self._run_writer_database(
                partial(
                    decide_strategy,
                    **common,
                    snapshot_id=UUID(values["snapshot_id"]),
                    item_id=UUID(values["item_id"]),
                    decision=values["decision"],
                )
            )
        if action == "ideas":
            from signal_core.keyword_ideas import expand_ideas

            reasoner = self.model_adapter._reasoner() if self.model_adapter else None
            if reasoner is None and self.brain_extractor is not None:
                reasoner = self.brain_extractor.model
            if reasoner is None and self.writer_service is not None:
                reasoner = self.writer_service.model.reasoner if self.writer_service.model else None
            connection = await self._open_writer_connection()
            try:
                return await expand_ideas(
                    connection, reasoner=reasoner, snapshot_id=UUID(values["snapshot_id"]), **common
                )
            finally:
                await self._close_connection(connection)
        raise ValueError("Invalid strategy action.")

    async def read_ai_visibility(self, *, session_token: str, site_id: UUID) -> dict:
        from signal_core.ai_visibility_agent import read_agent

        generation = await self._current_generation()
        return await self._run_writer_database(
            partial(
                read_agent,
                session_token=session_token,
                generation=generation,
                site_id=site_id,
                runtime_configured=self.visibility_runtime_configured,
                recipe_configured=self.content_candidate_configured,
            )
        )

    async def mutate_ai_visibility(
        self, *, session_token: str, site_id: UUID, action: str, values: dict
    ) -> dict:
        from signal_core.ai_visibility_agent import decide_proposal, prepare_proposals
        from signal_core.content_writer import ContentWriterRejected
        from signal_core.owner_ai_questions import approve_questions, propose_questions

        generation = await self._current_generation()
        common = {"session_token": session_token, "generation": generation, "site_id": site_id}
        if action == "prepare":
            operation = partial(prepare_proposals, **common)
        elif action == "decide":
            operation = partial(
                decide_proposal, **common, **{**values, "proposal_id": UUID(values["proposal_id"])}
            )
        elif action == "questions-propose":
            operation = partial(propose_questions, **common)
        elif action == "questions-approve":
            operation = partial(
                approve_questions,
                **common,
                **{
                    **values,
                    "request_id": UUID(values["request_id"]),
                    "crawl_manifest_id": UUID(values["crawl_manifest_id"]),
                    "supersedes_id": UUID(values["supersedes_id"])
                    if values["supersedes_id"]
                    else None,
                },
            )
        elif action == "seal":
            from signal_core.ai_visibility_recipe import seal_visibility_recipe, structured_context
            from signal_core.content_writer import ContentWriterUnavailable

            recipe_common = {
                **common,
                "proposal_id": UUID(values["proposal_id"]),
                "digest": values["digest"],
            }
            await self._run_writer_database(partial(structured_context, **recipe_common))
            if not self.content_candidate_configured:
                raise ContentWriterUnavailable("RECIPE_WORKER_UNAVAILABLE")
            connection = await self._open_writer_connection()
            try:
                with self.writer_service.candidate_connection_factory() as identity:
                    return await seal_visibility_recipe(
                        connection,
                        identity,
                        **recipe_common,
                        fact_fields=values["fact_fields"],
                        credential=self.writer_service.github_credential,
                        github_transport=self.writer_service.github_transport,
                        runner=self.writer_service.runner,
                    )
            finally:
                await self._close_connection(connection)
        else:
            raise ContentWriterRejected("INVALID_VISIBILITY_ACTION")
        return await self._run_writer_database(operation)

    async def mutate_content_writer(
        self, *, session_token: str, site_id: UUID, action: str, values: dict
    ) -> dict:
        from signal_core.content_writer import ContentWriterRejected
        from signal_core.content_writer_service import create_brief, writer_call

        generation = await self._current_generation()
        common = {"session_token": session_token, "generation": generation, "site_id": site_id}
        if action == "briefs":
            return await self._run_writer_database(
                partial(
                    create_brief,
                    **common,
                    **{
                        **values,
                        "supersedes_id": UUID(values["supersedes_id"])
                        if values["supersedes_id"]
                        else None,
                    },
                )
            )
        if action in {"accept-brief", "caps", "review", "approve-delivery"}:
            name, args = None, []
            if action == "accept-brief":
                name, args = "accept_brief", [UUID(values["brief_id"])]
            if action == "caps":
                name, args = "set_cap", [values["cap"]]
            if action == "review":
                name, args = (
                    "review",
                    [
                        UUID(values["candidate_id"]),
                        bytes.fromhex(values["revision_sha256"]),
                        values["decision"],
                    ],
                )
            if action == "approve-delivery":
                from psycopg.types.json import Jsonb

                name, args = (
                    "approve_delivery",
                    [
                        UUID(values["candidate_id"]),
                        bytes.fromhex(values["revision_sha256"]),
                        Jsonb(values["acknowledged_sentences"]),
                    ],
                )
            result = await self._run_writer_database(
                lambda connection: writer_call(
                    connection, session_token, generation, site_id, name, *args
                )
            )
            if result not in {"accepted", "updated", "reviewed", "replayed", "approved"}:
                raise ContentWriterRejected(result)
            return {"state": result}
        await self.read_content_writer(session_token=session_token, site_id=site_id)
        if action not in {"drafts", "candidates"}:
            raise ContentWriterRejected("INVALID_WRITER_ACTION")
        if self.writer_service is None:
            return {
                "state": "unavailable",
                "reason": "MODEL_UNCONFIGURED" if action == "drafts" else "CANDIDATE_UNCONFIGURED",
            }
        connection = await self._open_writer_connection()
        try:
            result = await (
                self.writer_service.draft(connection, **common, brief_id=UUID(values["brief_id"]))
                if action == "drafts"
                else self.writer_service.seal(
                    connection,
                    **common,
                    draft_id=UUID(values["draft_id"]),
                    extension_id=UUID(values["extension_id"]),
                    destination=values["destination"],
                )
            )
        except BaseException:
            await self._discard_connection(connection)
            raise
        await self._close_connection(connection)
        return result

    async def read_webflow(self, *, session_token: str, site_id: UUID) -> dict:
        from signal_core.webflow_service import webflow_call

        generation = await self._current_generation()
        return await self._run_writer_database(
            partial(
                webflow_call,
                session_token=session_token,
                generation=generation,
                site_id=site_id,
                action="read",
            )
        )

    async def review_webflow(self, *, session_token: str, site_id: UUID, values: dict) -> dict:
        from signal_core.webflow import WebflowUnavailable
        from signal_core.webflow_service import webflow_call

        generation = await self._current_generation()
        result = await self._run_writer_database(
            partial(
                webflow_call,
                session_token=session_token,
                generation=generation,
                site_id=site_id,
                action="review",
                payload=values,
            )
        )
        if result.get("state") not in {"reviewed", "replayed"}:
            raise WebflowUnavailable("WEBFLOW_REVIEW_CONFLICT")
        return result

    async def _open_writer_connection(self) -> Connection:
        from signal_core.content_writer import ContentWriterUnavailable

        if not callable(self.writer_connection_factory):
            raise ContentWriterUnavailable("WRITER_DATABASE_UNCONFIGURED")
        connection = await to_thread.run_sync(self.writer_connection_factory)
        if getattr(connection, "autocommit", None) is not True:
            await self._discard_connection(connection)
            raise ContentWriterUnavailable("WRITER_DATABASE_UNAVAILABLE")
        return connection

    async def _run_writer_database(self, operation):
        connection = await self._open_writer_connection()
        try:
            result = await to_thread.run_sync(operation, connection)
        finally:
            await self._close_connection(connection)
        return result

    async def read_pagespeed(self, *, session_token: str, site_id: UUID) -> dict:
        from signal_core.pagespeed_collection import read_pagespeed

        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_pagespeed,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
            )
        )

    async def read_business_brain(self, *, session_token: str, site_id: UUID) -> dict:
        from signal_core.business_brain import read_brain

        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_brain,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
            )
        )

    async def mutate_business_brain(
        self, *, session_token: str, site_id: UUID, action: str, values: dict
    ) -> str:
        from signal_core.business_brain import (
            FactCategory,
            FactProvenance,
            approve_fact,
            correct_fact,
            propose_fact,
            remove_fact,
            set_voice,
        )

        functions = {
            "approve": approve_fact,
            "correct": correct_fact,
            "remove": remove_fact,
            "propose": propose_fact,
            "voice": set_voice,
        }
        if action not in functions:
            raise ValueError("Invalid Business Brain action.")
        if action == "propose":
            values = {
                **values,
                "category": FactCategory(values["category"]),
                "provenance": FactProvenance("owner_statement"),
            }
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                functions[action],
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
                **values,
            )
        )

    async def extract_business_brain(self, *, session_token: str, site_id: UUID, values: dict):
        from signal_core.business_brain import FactProvenance
        from signal_core.business_brain_extraction import ExtractionState

        await self.read_business_brain(session_token=session_token, site_id=site_id)
        if not self.business_brain_configured:
            return ExtractionState("unavailable", reason="MODEL_UNCONFIGURED")
        generation = await self._current_generation()
        connection = await self._open_connection()
        try:
            result = await self.brain_extractor.extract(
                connection,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
                provenance=FactProvenance(
                    values["source_kind"], values["source_id"], values["extracted_range"]
                ),
            )
        except BaseException:
            await self._discard_connection(connection)
            raise
        await self._close_connection(connection)
        return result

    async def list_brand_documents(
        self, *, session_token: str, site_id: UUID
    ) -> tuple[BrandDocument, ...]:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                list_brand_documents,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
            )
        )

    async def delete_brand_document(
        self, *, session_token: str, site_id: UUID, document_id: UUID
    ) -> str:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                delete_brand_document,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
                document_id=document_id,
            )
        )

    async def read_brand_document(
        self, *, session_token: str, site_id: UUID, document_id: UUID
    ) -> UntrustedDocumentText:
        from signal_core.brand_documents import BrandDocumentUnavailable

        if self.brand_store is None or self.brand_key_reader is None:
            raise BrandDocumentUnavailable("document_storage_unavailable")
        key = await self._brand_key()
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_brand_document,
                store=self.brand_store,
                key=key,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
                document_id=document_id,
            )
        )

    async def _brand_key(self) -> ArtifactEncryptionKey:
        from signal_core.brand_documents import BrandDocumentUnavailable

        if self.brand_key_reader is None:
            raise BrandDocumentUnavailable("document_storage_unavailable")
        try:
            return await self.brand_key_reader.read(
                transport=self.recovery_transport, verify=self.recovery_verify
            )
        except ArtifactKeyUnavailable:
            raise BrandDocumentUnavailable("document_key_unavailable") from None

    async def initiate(
        self, *, return_path: str, purpose: OidcLoginPurpose = "login"
    ) -> InitiatedOidcLogin:
        connection = await self._open_connection()
        try:
            result = await initiate_oidc_login(
                connection,
                registration=self.registration,
                pkce_writer=self.pkce_writer,
                return_path=return_path,
                purpose=purpose,
                ttl_seconds=self.attempt_ttl_seconds,
                oidc_transport=self.oidc_transport,
                oidc_verify=self.oidc_verify,
                pkce_transport=self.pkce_transport,
                pkce_verify=self.pkce_verify,
            )
        except BaseException:
            await self._discard_connection(connection)
            raise
        await self._close_connection(connection)
        return result

    async def complete(
        self, *, state: str, browser_binding: str, code: str
    ) -> CompletedOidcLogin | CompletedInvitationIdentityVerification:
        connection = await self._open_connection()
        try:
            result = await complete_oidc_login(
                connection,
                state=state,
                browser_binding=browser_binding,
                code=code,
                pkce_consumer=self.pkce_consumer,
                recovery_authority=self.recovery_authority,
                policy=self.policy,
                oidc_transport=self.oidc_transport,
                oidc_verify=self.oidc_verify,
                pkce_transport=self.pkce_transport,
                pkce_verify=self.pkce_verify,
                recovery_transport=self.recovery_transport,
                recovery_verify=self.recovery_verify,
                verified_assertion_observer=self.verified_assertion_observer,
            )
        except BaseException:
            await self._discard_connection(connection)
            raise
        await self._close_connection(connection)
        return result

    async def accept_invitation(
        self,
        *,
        identity_proof_token: str,
        invitation_id: UUID,
        token: str,
        display_name: str,
    ) -> AcceptedInvitation:
        return await self._run_database(
            partial(
                accept_site_invitation,
                identity_proof_token=identity_proof_token,
                invitation_id=invitation_id,
                token=token,
                display_name=display_name,
            )
        )

    async def candidate_recipe_inbox(
        self, *, session_token: str, site_id: UUID
    ) -> tuple[CandidateRecipeInboxItem, ...]:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_authenticated_candidate_recipe_inbox,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )

    async def github_pr_operations(
        self, *, session_token: str, site_id: UUID
    ) -> tuple[GitHubPrOperation, ...]:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_authenticated_github_pr_operations,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )

    async def github_delivery_observations(
        self, *, session_token: str, site_id: UUID
    ) -> tuple[GitHubDeliveryObservation, ...]:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_authenticated_github_delivery_observations,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )

    async def decide_candidate_recipe_revision(
        self,
        *,
        session_token: str,
        site_id: UUID,
        revision_id: UUID,
        revision_sha256: str,
        decision_id: UUID,
        decision: CandidateReviewDecision,
    ) -> CandidateRecipeInboxItem:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                decide_authenticated_candidate_recipe_revision,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
                revision_id=revision_id,
                expected_revision_sha256=revision_sha256,
                decision_id=decision_id,
                decision=decision,
            )
        )

    async def memberships(self, *, identity_session_token: str) -> tuple[IdentityMembership, ...]:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                list_identity_memberships,
                identity_session_token=identity_session_token,
                current_recovery_generation=generation,
            )
        )

    async def select_tenant(
        self, *, identity_session_token: str, tenant_id: object
    ) -> IssuedTenantSession:
        if not isinstance(tenant_id, UUID):
            raise TenantSelectionDenied()
        generation = await self._current_generation()

        def select(connection: Connection) -> IssuedTenantSession:
            memberships = list_identity_memberships(
                connection,
                identity_session_token=identity_session_token,
                current_recovery_generation=generation,
            )
            if tenant_id not in {membership.tenant_id for membership in memberships}:
                raise TenantSelectionDenied()
            return issue_tenant_session(
                connection,
                identity_session_token=identity_session_token,
                requested_tenant_id=tenant_id,
                current_recovery_generation=generation,
                policy=self.policy,
            )

        return await self._run_database(select)

    async def current_session(self, *, session_token: str) -> CurrentTenantSession:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                inspect_tenant_session,
                session_token=session_token,
                current_recovery_generation=generation,
            )
        )

    async def site_directory(self, *, session_token: str) -> TenantSiteDirectory:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                list_tenant_sites,
                session_token=session_token,
                current_recovery_generation=generation,
            )
        )

    async def select_site(
        self,
        *,
        session_token: str,
        site_id: UUID,
        expected_session_version: int,
    ) -> SelectedSiteContext:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                select_session_site,
                session_token=session_token,
                current_recovery_generation=generation,
                requested_site_id=site_id,
                expected_session_version=expected_session_version,
            )
        )

    async def onboard_site(
        self,
        *,
        session_token: str,
        name: str,
        primary_origin: str,
        timezone: str,
        reporting_currency: str,
        expected_session_version: int,
        idempotency_key: UUID,
    ) -> OnboardedSite:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                onboard_site,
                session_token=session_token,
                current_recovery_generation=generation,
                name=name,
                primary_origin=primary_origin,
                timezone=timezone,
                reporting_currency=reporting_currency,
                expected_session_version=expected_session_version,
                idempotency_key=idempotency_key,
            )
        )

    async def grant_standing_authorization(
        self, *, session_token: str, request: StandingGrantRequest
    ) -> StandingGrant:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                grant_standing_authorization,
                session_token=session_token,
                current_recovery_generation=generation,
                request=request,
            )
        )

    async def revoke_standing_authorization(
        self, *, session_token: str, site_id: UUID, grant_id: UUID
    ) -> RevokedGrant:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                revoke_standing_authorization,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
                grant_id=grant_id,
            )
        )

    async def read_standing_authorization(
        self, *, session_token: str, site_id: UUID
    ) -> StandingGrantView:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_standing_authorization,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
            )
        )

    async def set_site_paused(
        self, *, session_token: str, site_id: UUID, paused: bool
    ) -> PauseResult:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                set_site_paused,
                session_token=session_token,
                site_id=site_id,
                recovery_generation=generation,
                paused=paused,
            )
        )

    async def read_site_pause(self, *, session_token: str, site_id: UUID) -> dict:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_site_pause,
                session_token=session_token,
                site_id=site_id,
                recovery_generation=generation,
            )
        )

    async def _visibility_database(self, operation):
        from signal_core.ai_visibility_schedule import VisibilityScheduleUnavailable

        if not callable(self.visibility_connection_factory):
            raise VisibilityScheduleUnavailable("SCHEDULE_UNCONFIGURED")

        def execute():
            with self.visibility_connection_factory() as connection:
                return operation(connection)

        return await to_thread.run_sync(execute)

    async def read_visibility_schedule(self, *, session_token: str, site_id: UUID) -> dict:
        from signal_core.ai_visibility_schedule import read_visibility_schedule

        generation = await self._current_generation()
        value = await self._visibility_database(
            partial(
                read_visibility_schedule,
                session_token=session_token,
                site_id=site_id,
                generation=generation,
            )
        )
        value["runtime_state"] = (
            "available" if self.visibility_runtime_configured else "unavailable"
        )
        return value

    async def update_visibility_schedule(
        self, *, session_token: str, site_id: UUID, values: dict
    ) -> dict:
        from signal_core.ai_visibility_schedule import set_visibility_schedule

        generation = await self._current_generation()
        await self._visibility_database(
            partial(
                set_visibility_schedule,
                session_token=session_token,
                site_id=site_id,
                generation=generation,
                **values,
            )
        )
        return await self.read_visibility_schedule(session_token=session_token, site_id=site_id)

    async def read_weekly_report(
        self, *, session_token: str, site_id: UUID, week_start: date | None
    ) -> dict | None:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_weekly_report,
                session_token=session_token,
                site_id=site_id,
                recovery_generation=generation,
                week_start=week_start,
            )
        )

    async def issue_origin_challenge(
        self,
        *,
        session_token: str,
        site_id: UUID,
        origin: str,
        idempotency_key: UUID,
    ) -> OriginChallenge:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                issue_origin_challenge,
                session_token=session_token,
                current_recovery_generation=generation,
                requested_site_id=site_id,
                origin=origin,
                idempotency_key=idempotency_key,
            )
        )

    async def verify_origin(
        self,
        *,
        session_token: str,
        site_id: UUID,
        challenge_id: UUID,
        origin: str,
        idempotency_key: UUID,
    ) -> VerifiedOrigin:
        if self.origin_fetcher is None:
            raise BrowserAuthenticationUnavailable()
        generation = await self._current_generation()
        prepared = await self._run_database(
            partial(
                prepare_origin_verification,
                session_token=session_token,
                current_recovery_generation=generation,
                requested_site_id=site_id,
                challenge_id=challenge_id,
                origin=origin,
                idempotency_key=idempotency_key,
            )
        )
        if isinstance(prepared, VerifiedOrigin):
            return prepared
        if not isinstance(prepared, PreparedOriginVerification):
            raise RuntimeError("Origin verification preparation failed.")
        observation = await to_thread.run_sync(
            observe_origin_proof,
            self.origin_fetcher,
            prepared,
        )
        return await self._run_database(
            partial(
                record_origin_verification,
                session_token=session_token,
                current_recovery_generation=generation,
                requested_site_id=site_id,
                challenge_id=challenge_id,
                origin=origin,
                idempotency_key=idempotency_key,
                observation=observation,
            )
        )

    async def logout(
        self, *, session_token: str, presented_session_kind: BrowserSessionKind
    ) -> bool:
        return await self._run_database(
            partial(
                revoke_browser_session,
                session_token=session_token,
                presented_session_kind=presented_session_kind,
            )
        )

    async def accept_snapshot_command(
        self,
        *,
        session_token: str,
        site_id: UUID,
        idempotency_key: str,
    ) -> AcceptedHumanCommand:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                accept_authenticated_snapshot,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
                idempotency_key=idempotency_key,
            )
        )

    async def snapshot_command_status(
        self,
        *,
        session_token: str,
        site_id: UUID,
        command_id: UUID,
    ) -> HumanCommandStatus:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_authenticated_snapshot_command,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
                command_id=command_id,
            )
        )

    async def latest_snapshot_command(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> HumanCommandStatus:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_latest_authenticated_snapshot_command,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )

    async def analyze_local_fixture(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> AuditFinding:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                record_authenticated_local_fixture_finding,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )

    async def findings(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> tuple[AuditFinding, ...]:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_authenticated_findings,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )

    async def analyze_verified_homepage(
        self,
        *,
        session_token: str,
        site_id: UUID,
        idempotency_key: UUID,
    ) -> PageAnalysis:
        if self.origin_fetcher is None:
            raise BrowserAuthenticationUnavailable()
        generation = await self._current_generation()
        prepared = await self._run_database(
            partial(
                prepare_authenticated_page_observation,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
                idempotency_key=idempotency_key,
            )
        )
        observed = await to_thread.run_sync(
            observe_verified_homepage,
            self.origin_fetcher,
            prepared,
        )
        observation = await self._run_database(
            partial(
                record_authenticated_page_observation,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
                prepared=prepared,
                observed=observed,
            )
        )
        current = await self._run_database(
            partial(
                read_authenticated_findings,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )
        finding = next((item for item in current if item.id == observation.finding_id), None)
        if observation.finding_id is not None and finding is None:
            raise RuntimeError("Page observation finding projection failed.")
        return PageAnalysis(observation=observation, finding=finding)

    async def latest_page_observation(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> PageObservation | None:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_latest_authenticated_page_observation,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )

    async def prepare_local_fixture_proposal(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> LocalProposal:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                prepare_authenticated_local_fixture_proposal,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )

    async def prepare_model_fixture_proposal(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> LocalProposal:
        if self.model_adapter is None:
            raise MetadataDraftError("MODEL_NOT_CONFIGURED", retryable=False)
        generation = await self._current_generation()
        findings = await self._run_database(
            partial(
                read_authenticated_findings,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )
        if not findings:
            raise ProposalNotReady()
        task = build_fixture_metadata_task(site_id=site_id, finding=findings[0])
        run = await self._run_database(
            partial(
                begin_authenticated_fixture_model_run,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
                finding=findings[0],
                task=task,
                model_requested=self._metadata_model_requested(),
            )
        )
        if run.outcome == "completed":
            proposals = await self._run_database(
                partial(
                    read_authenticated_local_fixture_proposals,
                    session_token=session_token,
                    requested_site_id=site_id,
                    current_recovery_generation=generation,
                )
            )
            for proposal in proposals:
                model = proposal.manifest.get("model")
                if isinstance(model, dict) and model.get("call_id") == str(run.model_call_id):
                    return proposal
            raise RuntimeError("Completed model proposal is unavailable.")
        try:
            draft = await self._budgeted_metadata(
                session_token, generation, site_id, task, verified=False
            )
        except MetadataDraftError as error:
            await self._run_database(
                partial(
                    fail_authenticated_fixture_model_run,
                    session_token=session_token,
                    requested_site_id=site_id,
                    current_recovery_generation=generation,
                    run=run,
                    error_code=error.code,
                    outcome_unknown=error.code == "MODEL_TRANSPORT_UNAVAILABLE",
                )
            )
            raise
        try:
            return await self._run_database(
                partial(
                    complete_authenticated_fixture_model_proposal,
                    session_token=session_token,
                    requested_site_id=site_id,
                    current_recovery_generation=generation,
                    run=run,
                    draft=draft,
                )
            )
        except BaseException:
            try:
                await self._run_database(
                    partial(
                        fail_authenticated_fixture_model_run,
                        session_token=session_token,
                        requested_site_id=site_id,
                        current_recovery_generation=generation,
                        run=run,
                        error_code="MODEL_PERSISTENCE_UNKNOWN",
                        outcome_unknown=True,
                    )
                )
            except BaseException:
                pass
            raise

    async def prepare_verified_homepage_proposal(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> LocalProposal:
        if self.model_adapter is None:
            raise MetadataDraftError("MODEL_NOT_CONFIGURED", retryable=False)
        generation = await self._current_generation()
        findings = await self._run_database(
            partial(
                read_authenticated_findings,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )
        observation = await self._run_database(
            partial(
                read_latest_authenticated_page_observation,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )
        finding = next((item for item in findings if item.source_kind == "verified_origin"), None)
        if finding is None or observation is None:
            raise ProposalNotReady()
        task = build_verified_homepage_metadata_task(
            site_id=site_id, finding=finding, observation=observation
        )
        run = await self._run_database(
            partial(
                begin_authenticated_verified_homepage_model_run,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
                finding=finding,
                task=task,
                model_requested=self._metadata_model_requested(),
            )
        )
        if run.outcome == "completed":
            proposals = await self._run_database(
                partial(
                    read_authenticated_local_fixture_proposals,
                    session_token=session_token,
                    requested_site_id=site_id,
                    current_recovery_generation=generation,
                )
            )
            for proposal in proposals:
                model = proposal.manifest.get("model")
                if isinstance(model, dict) and model.get("call_id") == str(run.model_call_id):
                    return proposal
            raise RuntimeError("Completed verified-homepage proposal is unavailable.")
        try:
            draft = await self._budgeted_metadata(
                session_token, generation, site_id, task, verified=True
            )
        except MetadataDraftError as error:
            await self._run_database(
                partial(
                    fail_authenticated_verified_homepage_model_run,
                    session_token=session_token,
                    requested_site_id=site_id,
                    current_recovery_generation=generation,
                    run=run,
                    error_code=error.code,
                    outcome_unknown=error.code == "MODEL_TRANSPORT_UNAVAILABLE",
                )
            )
            raise
        try:
            return await self._run_database(
                partial(
                    complete_authenticated_verified_homepage_model_proposal,
                    session_token=session_token,
                    requested_site_id=site_id,
                    current_recovery_generation=generation,
                    run=run,
                    draft=draft,
                )
            )
        except BaseException:
            try:
                await self._run_database(
                    partial(
                        fail_authenticated_verified_homepage_model_run,
                        session_token=session_token,
                        requested_site_id=site_id,
                        current_recovery_generation=generation,
                        run=run,
                        error_code="MODEL_PERSISTENCE_UNKNOWN",
                        outcome_unknown=True,
                    )
                )
            except BaseException:
                pass
            raise

    async def local_fixture_proposals(
        self,
        *,
        session_token: str,
        site_id: UUID,
    ) -> tuple[LocalProposal, ...]:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                read_authenticated_local_fixture_proposals,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
            )
        )

    async def decide_local_fixture_approval(
        self,
        *,
        session_token: str,
        site_id: UUID,
        approval_request_id: UUID,
        revision_sha256: str,
        decision_id: UUID,
        decision: ApprovalDecision,
    ) -> LocalProposal:
        generation = await self._current_generation()
        return await self._run_database(
            partial(
                decide_authenticated_local_fixture_approval,
                session_token=session_token,
                requested_site_id=site_id,
                current_recovery_generation=generation,
                approval_request_id=approval_request_id,
                expected_revision_sha256=revision_sha256,
                decision=decision,
                decision_id=decision_id,
            )
        )

    async def _current_generation(self) -> str:
        try:
            generation = await self.recovery_authority.current_generation(
                transport=self.recovery_transport,
                verify=self.recovery_verify,
            )
        except RecoveryAuthorityError:
            raise BrowserAuthenticationUnavailable() from None
        return generation.value

    def _metadata_model_requested(self):
        from signal_core.model_roles import DEFAULT_MODEL

        roles = getattr(self.model_adapter, "roles", None)
        return roles.for_role("metadata_draft").model if roles is not None else DEFAULT_MODEL

    async def _budgeted_metadata(self, session_token, generation, site_id, task, *, verified):
        from signal_core.model_budget import PostgresModelBudget

        connection = await self._open_connection()
        try:
            model = replace(
                self.model_adapter,
                budget=PostgresModelBudget(connection, session_token, generation, site_id),
            )
            return await (
                model.draft_verified_metadata(task) if verified else model.draft_metadata(task)
            )
        finally:
            await self._close_connection(connection)

    async def _run_database(self, operation: Callable[[Connection], _Result]) -> _Result:
        connection = await self._open_connection()
        try:
            result = await to_thread.run_sync(operation, connection)
        except BaseException:
            await self._discard_connection(connection)
            raise
        await self._close_connection(connection)
        return result

    async def _open_connection(self) -> Connection:
        try:
            connection = await to_thread.run_sync(self.connection_factory)
        except Exception:
            raise BrowserAuthenticationUnavailable() from None
        if getattr(connection, "autocommit", None) is not True or not callable(
            getattr(connection, "close", None)
        ):
            try:
                await to_thread.run_sync(connection.close)
            except Exception:
                pass
            raise BrowserAuthenticationUnavailable()
        return connection

    @staticmethod
    async def _close_connection(connection: Connection) -> None:
        try:
            await to_thread.run_sync(connection.close)
        except Exception:
            raise BrowserAuthenticationUnavailable() from None

    @staticmethod
    async def _discard_connection(connection: Connection) -> None:
        try:
            await to_thread.run_sync(connection.close)
        except Exception:
            pass
