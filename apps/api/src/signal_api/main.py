"""FastAPI application factory for bounded identity and command ingress."""

import base64
import binascii
import json
import logging
import math
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import TypeAdapter
from signal_core.audit_findings import AuditFinding, AuditNotReady, EvidenceConflict
from signal_core.authorization import AuthorizationDenied, InvalidSession
from signal_core.brand_documents import BrandDocumentRejected, BrandDocumentUnavailable
from signal_core.candidate_recipe_inbox import CandidateRecipeInboxItem
from signal_core.commands import CommandNotFound, HumanCommandStatus, IdempotencyConflict
from signal_core.github_delivery_observation import GitHubObservationUnavailable
from signal_core.github_pr_delivery import GitHubPrDeliveryUnavailable
from signal_core.invitation_acceptance import InvitationAcceptanceDenied
from signal_core.login_flow import (
    CompletedInvitationIdentityVerification,
    CompletedOidcLogin,
    LoginFlowError,
)
from signal_core.model_reasoning import MetadataDraftError
from signal_core.oidc_login import validate_oidc_return_path
from signal_core.oidc_protocol import OidcProtocolError, validate_authorization_code
from signal_core.origin_verification import (
    InvalidOriginVerification,
    OriginChallengeExpired,
    OriginChallengeNotFound,
    OriginClaimConflict,
    OriginProofMismatch,
    OriginProofNotFound,
    OriginProofRejected,
    OriginProofUnavailable,
    OriginVerificationConflict,
    OriginVerificationDenied,
)
from signal_core.page_observations import (
    OriginNotVerified,
    PageAnalysis,
    PageObservation,
    PageObservationConflict,
    PageObservationFailed,
    PageObservationNotReady,
)
from signal_core.proposals import (
    ApprovalDecisionConflict,
    ApprovalExpired,
    ApprovalNotFound,
    ApprovalPermissionDenied,
    ApprovalRevisionConflict,
    LocalProposal,
    ModelAttemptLimit,
    ModelInputConflict,
    ModelOutcomeUnknown,
    ModelRunInProgress,
    ProposalManifestConflict,
    ProposalNotReady,
)
from signal_core.recipe_releases import RecipeReleaseUnavailable
from signal_core.session_issuance import InvalidIdentitySession
from signal_core.session_management import SessionContextConflict, SiteSelectionDenied
from signal_core.session_tokens import InvalidOpaqueSessionToken, validate_session_token
from signal_core.site_onboarding import (
    InvalidSiteOnboarding,
    SiteLimitReached,
    SiteOnboardingConflict,
    SiteOnboardingDenied,
)
from signal_core.standing_authorization import (
    RecipeRange,
    StandingAuthorizationConflict,
    StandingAuthorizationUnavailable,
    StandingGrantRequest,
)
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from signal_api.authentication import (
    BrowserAuthenticationUnavailable,
    BrowserBrandDocumentGateway,
    BrowserCandidateInboxGateway,
    BrowserCommandGateway,
    BrowserFindingGateway,
    BrowserGithubDeliveryGateway,
    BrowserGithubPrOperationsGateway,
    BrowserInvitationGateway,
    BrowserLoginGateway,
    BrowserOriginVerificationGateway,
    BrowserProposalGateway,
    BrowserSessionGateway,
    BrowserSiteDirectoryGateway,
    BrowserSiteOnboardingGateway,
    BrowserSiteSelectionGateway,
    BrowserStandingGrantGateway,
    BrowserTenantGateway,
    BrowserWeeklyGateway,
    TenantSelectionDenied,
)
from signal_api.brand_document_contracts import (
    BrandDocumentDeletionResponse,
    BrandDocumentResponse,
    BrandDocumentsResponse,
    BrandDocumentTextResponse,
    BrandDocumentUploadRequest,
)
from signal_api.browser_security import (
    IDENTITY_COOKIE_NAME,
    INVITATION_IDENTITY_COOKIE_NAME,
    OIDC_BINDING_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    BrowserInvitationMutationProof,
    BrowserLogoutProof,
    BrowserMutationProof,
    BrowserRequestRejected,
    BrowserSecurity,
    BrowserSecurityUnavailable,
    clear_identity_cookie,
    clear_invitation_identity_cookie,
    clear_oidc_binding_cookie,
    clear_session_cookie,
    exact_cookie,
    require_browser_identity_mutation,
    require_browser_invitation_mutation,
    require_browser_logout_mutation,
    require_browser_mutation,
    set_identity_cookie,
    set_invitation_identity_cookie,
    set_oidc_binding_cookie,
    set_session_cookie,
)
from signal_api.candidate_inbox_contracts import (
    AstroRecipeManifest,
    CandidateInboxDecisionRequest,
    CandidateInboxDecisionResponse,
    CandidateInboxItemResponse,
    CandidateInboxResponse,
    CandidateRecipeManifest,
    FrontMatterRecipeManifest,
    NextjsRecipeManifest,
)
from signal_api.chat_reports_http import ComposedChatReportsGateway, mount_chat_reports_http
from signal_api.command_contracts import (
    SnapshotCommandAcceptedResponse,
    SnapshotCommandRequest,
    SnapshotCommandStatusResponse,
)
from signal_api.config import ApiSettings
from signal_api.contracts import (
    CapabilitiesResponse,
    Capability,
    CapabilityAvailability,
    Contract,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
)
from signal_api.dataforseo_http import ComposedDataForSeoGateway, mount_dataforseo_http
from signal_api.docs_http import ComposedDocsGateway, mount_docs_http
from signal_api.email_http import ComposedEmailGateway, mount_email_http
from signal_api.finding_contracts import (
    FindingResponse,
    FindingsResponse,
    LatestPageObservationResponse,
    LocalFixtureAnalysisRequest,
    LocalFixtureAnalysisResponse,
    PageObservationResponse,
    VerifiedHomepageAnalysisRequest,
    VerifiedHomepageAnalysisResponse,
)
from signal_api.ga4_http import ComposedGa4Gateway, mount_ga4_http
from signal_api.github_delivery_contracts import (
    GitHubDeliveryObservationResponse,
    GitHubDeliveryObservationsResponse,
)
from signal_api.github_pr_contracts import GitHubPrOperationResponse, GitHubPrOperationsResponse
from signal_api.health_http import mount_health_http
from signal_api.indexnow_http import ComposedIndexNowReadGateway, mount_indexnow_http
from signal_api.owner_connector_http import OwnerConnectorGateway, mount_owner_connector_http
from signal_api.proposal_contracts import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    LocalProposalRequest,
    LocalProposalResponse,
    ProposalManifest,
    ProposalResponse,
    ProposalsResponse,
)
from signal_api.session_contracts import (
    CsrfTokenResponse,
    CurrentTenantSessionResponse,
    InvitationAcceptanceRequest,
    InvitationAcceptanceResponse,
    OrganizationsResponse,
    OrganizationSummary,
    OriginChallengeRequest,
    OriginChallengeResponse,
    OriginVerificationRequest,
    OriginVerificationResponse,
    SiteOnboardingRequest,
    SiteOnboardingResponse,
    SiteSelectionRequest,
    SiteSelectionResponse,
    SitesResponse,
    SiteSummary,
    TenantSelectionRequest,
    TenantSessionResponse,
)
from signal_api.slack_http import ComposedSlackGateway, mount_slack_http
from signal_api.standing_contracts import (
    StandingGrantRequestContract,
    StandingGrantResponse,
    StandingGrantRevokeRequest,
    StandingGrantRevokeResponse,
    StandingGrantViewResponse,
)
from signal_api.team_http import mount_team_http
from signal_api.telegram_http import ComposedTelegramGateway, mount_telegram_http
from signal_api.weekly_report_contracts import WeeklyReportResponse

logger = logging.getLogger("signal.api")
_CORRELATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
_PROPOSAL_MANIFEST = TypeAdapter(ProposalManifest)
_CANDIDATE_RECIPE_MANIFEST = TypeAdapter(
    CandidateRecipeManifest | AstroRecipeManifest | FrontMatterRecipeManifest | NextjsRecipeManifest
)
ReadinessProbe = Callable[[], Awaitable[bool]]


async def _not_configured() -> bool:
    return False


def _error(
    request: Request, *, status: int, code: str, message: str, retryable: bool
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            retryable=retryable,
            correlation_id=request.state.correlation_id,
        )
    )
    return JSONResponse(status_code=status, content=payload.model_dump())


def _snapshot_status_response(
    request: Request,
    site_id: UUID,
    status: HumanCommandStatus,
) -> SnapshotCommandStatusResponse | JSONResponse:
    try:
        return SnapshotCommandStatusResponse(
            command_id=status.id,
            site_id=site_id,
            actor_user_id=status.actor_user_id,
            kind=status.kind,
            status=status.status,
            accepted_at=_aware_datetime(status.accepted_at),
            workflow_id=status.workflow_id,
            workflow_type=status.workflow_type,
            first_run_id=status.first_run_id,
            workflow_state=status.workflow_state,
            projected_at=(
                _aware_datetime(status.projected_at) if status.projected_at is not None else None
            ),
            result_reference=(
                {
                    "schema_version": status.result_reference.schema_version,
                    "manifest_id": status.result_reference.manifest_id,
                    "manifest_sha256": status.result_reference.manifest_sha256,
                    "coverage": status.result_reference.coverage,
                    "discovered_count": status.result_reference.discovered_count,
                    "terminal_count": status.result_reference.terminal_count,
                    "scope_version": status.result_reference.scope_version,
                    "crawl_policy_version": status.result_reference.crawl_policy_version,
                }
                if status.result_reference is not None
                else None
            ),
            terminal_reason=status.terminal_reason,
            correlation_id=request.state.correlation_id,
        )
    except (AttributeError, TypeError, ValueError):
        return _error(
            request,
            status=500,
            code="COMMAND_STATUS_FAILED",
            message="The command status could not be read.",
            retryable=False,
        )


def _finding_response(finding: AuditFinding) -> FindingResponse:
    return FindingResponse(
        finding_id=finding.id,
        evidence_id=finding.evidence_id,
        command_id=finding.command_id,
        manifest_id=finding.manifest_id,
        finding_key=finding.finding_key,
        title=finding.title,
        summary=finding.summary,
        resource_locator=finding.resource_locator,
        severity=finding.severity,
        status=finding.status,
        confidence_class=finding.confidence_class,
        source_kind=finding.source_kind,
        source_identifier=finding.source_identifier,
        content_sha256=finding.content_sha256,
        evidence_observed_at=_aware_datetime(finding.evidence_observed_at),
        first_seen_at=_aware_datetime(finding.first_seen_at),
        last_seen_at=_aware_datetime(finding.last_seen_at),
        reused=finding.reused,
    )


def _page_observation_response(observation: PageObservation) -> PageObservationResponse:
    return PageObservationResponse(
        intent_id=observation.intent_id,
        evidence_id=observation.evidence_id,
        finding_id=observation.finding_id,
        command_id=observation.command_id,
        manifest_id=observation.manifest_id,
        origin=observation.origin,
        final_url=observation.final_url,
        http_status=observation.http_status,
        media_type=observation.media_type,
        title=observation.title,
        heading=observation.heading,
        meta_description=observation.meta_description,
        body_sha256=observation.body_sha256,
        observed_at=_aware_datetime(observation.observed_at),
        reused=observation.reused,
    )


def _proposal_response(proposal: LocalProposal) -> ProposalResponse:
    return ProposalResponse(
        proposal_id=proposal.proposal_id,
        revision_id=proposal.revision_id,
        revision_number=proposal.revision_number,
        revision_sha256=proposal.revision_sha256,
        manifest=_PROPOSAL_MANIFEST.validate_python(proposal.manifest),
        created_by_user_id=proposal.created_by_user_id,
        created_at=_aware_datetime(proposal.created_at),
        approval_request_id=proposal.approval_request_id,
        approval_status=proposal.approval_status,
        approval_requested_at=_aware_datetime(proposal.approval_requested_at),
        approval_expires_at=_aware_datetime(proposal.approval_expires_at),
        decision_id=proposal.decision_id,
        decision=proposal.decision,
        decided_by_user_id=proposal.decided_by_user_id,
        decision_channel=proposal.decision_channel,
        decided_at=(
            _aware_datetime(proposal.decided_at) if proposal.decided_at is not None else None
        ),
        reused=proposal.reused,
    )


def _candidate_inbox_response(item: CandidateRecipeInboxItem) -> CandidateInboxItemResponse:
    return CandidateInboxItemResponse(
        revision_id=item.revision_id,
        revision_sha256=item.revision_sha256,
        manifest=_CANDIDATE_RECIPE_MANIFEST.validate_python(item.manifest),
        sealed_at=_aware_datetime(item.sealed_at),
        recipe_release_id=item.recipe_release_id,
        release_content_hash=item.release_content_hash,
        base_sha=item.base_sha,
        patch_sha256=item.patch_sha256,
        review_status=item.review_status,
        decision_id=item.decision_id,
        decision=item.decision,
        decided_by_user_id=item.decided_by_user_id,
        decision_channel=item.decision_channel,
        decided_at=_aware_datetime(item.decided_at) if item.decided_at is not None else None,
        reused=item.reused,
    )


def create_app(
    *,
    settings: ApiSettings | None = None,
    readiness_probe: ReadinessProbe | None = None,
    browser_security: BrowserSecurity | None = None,
    browser_login: BrowserLoginGateway | None = None,
    browser_fixture_analysis: BrowserFindingGateway | None = None,
    browser_proposals: BrowserProposalGateway | None = None,
    browser_documents: BrowserBrandDocumentGateway | None = None,
    browser_brain: object | None = None,
    browser_pagespeed: object | None = None,
    browser_writer: object | None = None,
    browser_strategy: object | None = None,
    browser_assistant: object | None = None,
    browser_visibility: object | None = None,
    browser_candidate_inbox: BrowserCandidateInboxGateway | None = None,
    browser_github_pr_operations: BrowserGithubPrOperationsGateway | None = None,
    browser_github_delivery: BrowserGithubDeliveryGateway | None = None,
    browser_slack: ComposedSlackGateway | None = None,
    browser_gsc: OwnerConnectorGateway | None = None,
    browser_bing: OwnerConnectorGateway | None = None,
    browser_github: OwnerConnectorGateway | None = None,
    browser_dataforseo: ComposedDataForSeoGateway | None = None,
    browser_indexnow: ComposedIndexNowReadGateway | None = None,
    browser_ga4: ComposedGa4Gateway | None = None,
    browser_email: ComposedEmailGateway | None = None,
    browser_chat_reports: ComposedChatReportsGateway | None = None,
    browser_telegram: ComposedTelegramGateway | None = None,
    browser_wordpress: object | None = None,
    browser_webflow: object | None = None,
    browser_docs: ComposedDocsGateway | None = None,
    browser_health: object | None = None,
    health_monitor: object | None = None,
) -> FastAPI:
    configured = settings or ApiSettings.from_environment()
    probe = readiness_probe or _not_configured
    docs_url = "/docs" if configured.expose_docs else None
    openapi_url = "/openapi.json" if configured.expose_docs else None
    application = FastAPI(
        title="Signal API",
        version="0.0.0",
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=openapi_url,
    )
    application.state.browser_security = browser_security
    application.state.browser_login = browser_login
    application.state.browser_fixture_analysis = browser_fixture_analysis
    application.state.browser_proposals = browser_proposals
    application.state.browser_documents = browser_documents
    application.state.browser_brain = browser_brain
    application.state.browser_pagespeed = browser_pagespeed
    application.state.browser_writer = browser_writer
    application.state.browser_webflow = browser_webflow
    application.state.browser_strategy = browser_strategy
    application.state.browser_assistant = browser_assistant

    application.state.browser_visibility = browser_visibility or browser_login
    application.state.browser_candidate_inbox = browser_candidate_inbox
    application.state.browser_github_pr_operations = browser_github_pr_operations
    application.state.browser_github_delivery = browser_github_delivery
    mount_slack_http(application, browser_slack)
    mount_owner_connector_http(application, "gsc", browser_gsc)
    mount_owner_connector_http(application, "github", browser_github)
    mount_owner_connector_http(application, "github-pr", browser_github)
    mount_owner_connector_http(application, "bing", browser_bing)
    mount_dataforseo_http(application, browser_dataforseo)
    mount_indexnow_http(application, browser_indexnow)
    mount_ga4_http(application, browser_ga4)
    mount_email_http(application, browser_email)
    mount_team_http(application, browser_login)
    mount_chat_reports_http(application, browser_chat_reports)

    mount_health_http(application, browser_health, health_monitor)
    mount_telegram_http(application, browser_telegram)
    from signal_api.wordpress_http import mount_wordpress_http

    mount_wordpress_http(application, browser_wordpress)
    mount_docs_http(application, browser_docs)

    from signal_api.visibility_schedule_routes import register_visibility_schedule_routes

    register_visibility_schedule_routes(application)

    @application.middleware("http")
    async def request_contract(request: Request, call_next) -> Response:
        if request.url.path == "/v1/session/callback":
            request.state.sensitive_query_items = tuple(request.query_params.multi_items())
            request.scope["query_string"] = b""
        supplied = request.headers.get("x-correlation-id", "")
        request.state.correlation_id = (
            supplied if _CORRELATION_ID.fullmatch(supplied) else str(uuid4())
        )
        try:
            response = await call_next(request)
        except Exception:
            logger.error(
                "unhandled_request_failure",
                extra={"correlation_id": request.state.correlation_id, "method": request.method},
            )
            response = _error(
                request,
                status=500,
                code="INTERNAL_ERROR",
                message="The request could not be completed.",
                retryable=False,
            )
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        return _error(
            request,
            status=error.status_code,
            code=codes.get(error.status_code, "HTTP_ERROR"),
            message="The requested API operation is unavailable.",
            retryable=False,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        del error
        return _error(
            request,
            status=422,
            code="INVALID_REQUEST",
            message="The request does not match the API contract.",
            retryable=False,
        )

    @application.exception_handler(BrowserRequestRejected)
    async def browser_request_rejected(
        request: Request, error: BrowserRequestRejected
    ) -> JSONResponse:
        del error
        return _error(
            request,
            status=403,
            code="BROWSER_REQUEST_REJECTED",
            message="The browser request could not be authorized.",
            retryable=False,
        )

    @application.exception_handler(BrowserSecurityUnavailable)
    async def browser_security_unavailable(
        request: Request, error: BrowserSecurityUnavailable
    ) -> JSONResponse:
        del error
        return _error(
            request,
            status=503,
            code="BROWSER_SECURITY_NOT_READY",
            message="Browser mutation security is not ready.",
            retryable=False,
        )

    @application.exception_handler(BrowserAuthenticationUnavailable)
    async def browser_authentication_unavailable(
        request: Request, error: BrowserAuthenticationUnavailable
    ) -> JSONResponse:
        del error
        return _error(
            request,
            status=503,
            code="AUTHENTICATION_NOT_READY",
            message="Authentication dependencies are not ready.",
            retryable=True,
        )

    @application.get("/health/live", response_model=HealthResponse, tags=["health"])
    async def liveness() -> HealthResponse:
        return HealthResponse(status="ok", service="signal-api", version="0.0.0")

    @application.get(
        "/health/ready",
        response_model=HealthResponse,
        responses={503: {"model": ErrorResponse}},
        tags=["health"],
    )
    async def readiness(request: Request) -> HealthResponse | JSONResponse:
        if not await probe():
            return _error(
                request,
                status=503,
                code="DEPENDENCIES_NOT_READY",
                message="Required service dependencies are not ready.",
                retryable=True,
            )
        return HealthResponse(status="ready", service="signal-api", version="0.0.0")

    async def composed_capabilities():
        inventory = (
            ("provider.dataforseo", browser_dataforseo),
            ("provider.telegram", browser_telegram),
            ("provider.slack", browser_slack),
            ("provider.gsc", browser_gsc),
            ("provider.github", browser_github),
            ("provider.bing", browser_bing),
            ("provider.ga4", browser_ga4),
            ("provider.google_docs", browser_docs),
            ("provider.wordpress", browser_wordpress),
            ("provider.webflow", browser_webflow),
            ("provider.indexnow", browser_indexnow),
            ("provider.email", browser_email),
            ("evidence.pagespeed", browser_pagespeed),
            ("operations.health", browser_health),
            ("planning.content_writer", browser_writer),
            ("planning.business_brain", browser_brain),
            ("planning.seo_strategy", browser_strategy),
            ("planning.ai_visibility", browser_visibility or browser_login),
            ("approval.candidate_inbox", browser_candidate_inbox),
            ("delivery.github_operations", browser_github_pr_operations),
            ("delivery.github_observations", browser_github_delivery),
            ("notification.chat_reports", browser_chat_reports),
            ("evidence.brand_documents", browser_documents),
            ("database.command_acceptance", browser_login),
            ("identity.site_authorization", browser_login),
            ("identity.oidc_login_attempts", browser_login),
            ("identity.session_issuance", browser_login),
            ("identity.session_audit", browser_login),
            ("identity.pkce_secret_store", browser_login),
            ("identity.recovery_authority", browser_login),
            ("identity.oidc_login_flow", browser_login),
            ("identity.site_invitation_issuance", browser_login),
            ("identity.site_invitation_acceptance", browser_login),
            ("identity.oidc_http_login", browser_login),
            ("identity.tenant_session_http", browser_login),
            ("identity.session_revocation", browser_login),
            ("dashboard.site_context", browser_login),
            ("dashboard.active_site_selection", browser_login),
            ("dashboard.site_onboarding", browser_login),
            ("dashboard.origin_verification", browser_login),
            ("identity.invitation_identity_proof", browser_login),
            ("identity.invitation_http", browser_login),
            ("command.human_snapshot_http", browser_login),
            ("evidence.verified_origin_homepage", browser_fixture_analysis),
            ("planning.verified_homepage_proposal", browser_proposals),
            ("customer.authentication", None),
            ("provider.openai_web_search", browser_assistant),
            ("provider.perplexity_web_search", browser_assistant),
            ("provider.gemini_web_search", browser_assistant),
            ("provider.production_writes", None),
        )
        result = []
        for key, gateway in inventory:
            configured_gateway = gateway is not None
            if configured_gateway and key in {"provider.slack", "provider.telegram"}:
                try:
                    configured_gateway = bool(await gateway.availability())
                except Exception:
                    configured_gateway = False
            result.append(
                (
                    key,
                    CapabilityAvailability.INTERNAL_ONLY
                    if configured_gateway
                    else CapabilityAvailability.DISABLED,
                )
            )
        return result

    @application.get("/v1/capabilities", response_model=CapabilitiesResponse, tags=["capabilities"])
    async def capabilities() -> CapabilitiesResponse:
        return CapabilitiesResponse(
            schema_version=1,
            release_status="development",
            production_writes_enabled=False,
            capabilities=tuple(
                Capability(key=key, availability=availability)
                for key, availability in await composed_capabilities()
            ),
        )

    @application.get("/v1/session/login", tags=["identity"])
    async def start_login(request: Request) -> Response:
        gateway = _browser_login_gateway(request)
        try:
            return_path = _single_query_value(request, "return_path", default="/")
            validate_oidc_return_path(return_path)
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_LOGIN_REQUEST",
                message="The login request is invalid.",
                retryable=False,
            )
        try:
            initiated = await gateway.initiate(return_path=return_path, purpose="login")
        except LoginFlowError:
            return _error(
                request,
                status=503,
                code="LOGIN_START_FAILED",
                message="Login could not be started.",
                retryable=True,
            )
        response = RedirectResponse(initiated.authorization_url, status_code=303)
        clear_identity_cookie(response)
        clear_session_cookie(response)
        clear_invitation_identity_cookie(response)
        set_oidc_binding_cookie(
            response,
            initiated.browser_binding,
            max_age=gateway.attempt_ttl_seconds,
        )
        return response

    @application.get("/v1/invitations/verify", tags=["identity"])
    async def start_invitation_verification(request: Request) -> Response:
        gateway = _browser_login_gateway(request)
        try:
            return_path = _single_query_value(
                request,
                "return_path",
                default="/invitations/accept",
            )
            validate_oidc_return_path(return_path)
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_INVITATION_VERIFICATION_REQUEST",
                message="The invitation verification request is invalid.",
                retryable=False,
            )
        try:
            initiated = await gateway.initiate(
                return_path=return_path,
                purpose="invitation_acceptance",
            )
        except LoginFlowError:
            return _error(
                request,
                status=503,
                code="INVITATION_VERIFICATION_START_FAILED",
                message="Invitation verification could not be started.",
                retryable=True,
            )
        response = RedirectResponse(initiated.authorization_url, status_code=303)
        clear_invitation_identity_cookie(response)
        set_oidc_binding_cookie(
            response,
            initiated.browser_binding,
            max_age=gateway.attempt_ttl_seconds,
        )
        return response

    @application.get("/v1/session/callback", tags=["identity"])
    async def complete_login(request: Request) -> Response:
        gateway = _browser_login_gateway(request)
        try:
            state = validate_session_token(_single_query_value(request, "state"))
            code = validate_authorization_code(_single_query_value(request, "code"))
            browser_binding = exact_cookie(request, OIDC_BINDING_COOKIE_NAME)
        except (BrowserRequestRejected, InvalidOpaqueSessionToken, OidcProtocolError, ValueError):
            response = _error(
                request,
                status=401,
                code="LOGIN_CALLBACK_REJECTED",
                message="The login response could not be verified.",
                retryable=False,
            )
            clear_oidc_binding_cookie(response)
            return response

        try:
            completed = await gateway.complete(
                state=state,
                browser_binding=browser_binding,
                code=code,
            )
        except LoginFlowError as error:
            retryable = error.code in {
                "LOGIN_CALLBACK_CONFIGURATION_REJECTED",
                "LOGIN_RECOVERY_AUTHORITY_FAILED",
            }
            rejected = error.code == "LOGIN_CALLBACK_REJECTED"
            response = _error(
                request,
                status=401 if rejected else 503,
                code="LOGIN_CALLBACK_REJECTED" if rejected else "LOGIN_CALLBACK_FAILED",
                message=(
                    "The login response could not be verified."
                    if rejected
                    else "Login could not be completed."
                ),
                retryable=retryable,
            )
            if not retryable:
                clear_oidc_binding_cookie(response)
            return response

        try:
            return_path = validate_oidc_return_path(completed.return_path)
            if isinstance(completed, CompletedOidcLogin):
                credential = validate_session_token(completed.identity_session.token)
                lifetime = _cookie_lifetime(completed.identity_session.expires_at)
            elif isinstance(completed, CompletedInvitationIdentityVerification):
                credential = validate_session_token(completed.identity_proof.token)
                lifetime = min(_cookie_lifetime(completed.identity_proof.expires_at), 600)
            else:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            response = _error(
                request,
                status=500,
                code="LOGIN_CALLBACK_FAILED",
                message="Login could not be completed.",
                retryable=False,
            )
            clear_oidc_binding_cookie(response)
            return response

        response = RedirectResponse(return_path, status_code=303)
        clear_oidc_binding_cookie(response)
        if isinstance(completed, CompletedOidcLogin):
            clear_session_cookie(response)
            clear_invitation_identity_cookie(response)
            set_identity_cookie(response, credential, max_age=lifetime)
        else:
            set_invitation_identity_cookie(response, credential, max_age=lifetime)
        return response

    @application.get(
        "/v1/invitations/csrf",
        response_model=CsrfTokenResponse,
        tags=["identity"],
    )
    async def invitation_csrf(request: Request) -> CsrfTokenResponse | JSONResponse:
        security = _browser_security(request)
        try:
            identity_proof = exact_cookie(request, INVITATION_IDENTITY_COOKIE_NAME)
        except BrowserRequestRejected:
            return _invitation_identity_unauthorized(request)
        return CsrfTokenResponse(csrf_token=security.issue_csrf_token(identity_proof))

    @application.post(
        "/v1/invitations/accept",
        response_model=InvitationAcceptanceResponse,
        tags=["identity"],
    )
    async def accept_invitation(
        request: Request,
        proof: Annotated[
            BrowserInvitationMutationProof,
            Depends(require_browser_invitation_mutation),
        ],
    ) -> Response:
        gateway = _browser_invitation_gateway(request)
        try:
            acceptance = await _strict_json_request(
                request,
                InvitationAcceptanceRequest,
                maximum=2048,
            )
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_INVITATION_ACCEPTANCE",
                message="The invitation acceptance request is invalid.",
                retryable=False,
            )
        try:
            accepted = await gateway.accept_invitation(
                identity_proof_token=proof.identity_proof_token,
                invitation_id=acceptance.invitation_id,
                token=acceptance.token,
                display_name=acceptance.display_name,
            )
        except InvitationAcceptanceDenied:
            return _error(
                request,
                status=403,
                code="INVITATION_ACCEPTANCE_DENIED",
                message="The invitation could not be accepted.",
                retryable=False,
            )

        try:
            if accepted.invitation_id != acceptance.invitation_id:
                raise ValueError
            payload = InvitationAcceptanceResponse(
                invitation_id=accepted.invitation_id,
                tenant_id=accepted.tenant_id,
                site_id=accepted.site_id,
                user_id=accepted.user_id,
                role_key=accepted.role_key,
                accepted_at=accepted.accepted_at,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="INVITATION_ACCEPTANCE_FAILED",
                message="The invitation could not be accepted.",
                retryable=False,
            )
        response = JSONResponse(payload.model_dump(mode="json"))
        clear_invitation_identity_cookie(response)
        return response

    @application.get(
        "/v1/organizations",
        response_model=OrganizationsResponse,
        tags=["identity"],
    )
    async def organizations(request: Request) -> OrganizationsResponse | JSONResponse:
        gateway = _browser_tenant_gateway(request)
        try:
            identity_token = exact_cookie(request, IDENTITY_COOKIE_NAME)
        except BrowserRequestRejected:
            return _identity_unauthorized(request)
        try:
            memberships = await gateway.memberships(identity_session_token=identity_token)
        except InvalidIdentitySession:
            return _identity_unauthorized(request)
        return OrganizationsResponse(
            organizations=tuple(
                OrganizationSummary(
                    tenant_id=membership.tenant_id,
                    name=membership.tenant_name,
                    role_key=membership.role_key,
                )
                for membership in memberships
            )
        )

    @application.get(
        "/v1/session/csrf",
        response_model=CsrfTokenResponse,
        tags=["identity"],
    )
    async def identity_csrf(request: Request) -> CsrfTokenResponse | JSONResponse:
        security = _browser_security(request)
        try:
            identity_token = exact_cookie(request, IDENTITY_COOKIE_NAME)
        except BrowserRequestRejected:
            return _identity_unauthorized(request)
        return CsrfTokenResponse(csrf_token=security.issue_csrf_token(identity_token))

    @application.get(
        "/v1/session/logout-csrf",
        response_model=CsrfTokenResponse,
        tags=["identity"],
    )
    async def logout_csrf(request: Request) -> CsrfTokenResponse | JSONResponse:
        security = _browser_security(request)
        try:
            csrf_token = security.issue_logout_csrf_token(request)
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        return CsrfTokenResponse(csrf_token=csrf_token)

    @application.get(
        "/v1/session/tenant-csrf",
        response_model=CsrfTokenResponse,
        tags=["identity"],
    )
    async def tenant_csrf(request: Request) -> CsrfTokenResponse | JSONResponse:
        security = _browser_security(request)
        try:
            session_token = exact_cookie(request, SESSION_COOKIE_NAME)
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        return CsrfTokenResponse(csrf_token=security.issue_csrf_token(session_token))

    @application.post(
        "/v1/session/switch-tenant",
        response_model=TenantSessionResponse,
        tags=["identity"],
    )
    async def switch_tenant(
        request: Request,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_identity_mutation)],
    ) -> Response:
        gateway = _browser_tenant_gateway(request)
        security = _browser_security(request)
        try:
            selection = await _tenant_selection_request(request)
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_TENANT_SELECTION",
                message="The tenant selection request is invalid.",
                retryable=False,
            )
        try:
            session = await gateway.select_tenant(
                identity_session_token=proof.session_token,
                tenant_id=selection.tenant_id,
            )
        except TenantSelectionDenied:
            return _error(
                request,
                status=403,
                code="TENANT_SELECTION_DENIED",
                message="The requested tenant cannot be selected.",
                retryable=False,
            )
        except InvalidIdentitySession:
            return _identity_unauthorized(request)

        try:
            if session.tenant_id != selection.tenant_id:
                raise ValueError
            session_token = validate_session_token(session.token)
            lifetime = _cookie_lifetime(session.expires_at)
            csrf_token = security.issue_csrf_token(session_token)
            payload = TenantSessionResponse(
                tenant_id=session.tenant_id,
                user_id=session.user_id,
                role_key=session.role_key,
                authentication_level=session.authentication_level,
                expires_at=session.expires_at,
                csrf_token=csrf_token,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="TENANT_SESSION_FAILED",
                message="The tenant session could not be established.",
                retryable=False,
            )
        response = JSONResponse(payload.model_dump(mode="json"))
        set_session_cookie(response, session_token, max_age=lifetime)
        return response

    @application.get(
        "/v1/session",
        response_model=CurrentTenantSessionResponse,
        tags=["identity"],
    )
    async def current_session(request: Request) -> CurrentTenantSessionResponse | JSONResponse:
        gateway = _browser_session_gateway(request)
        try:
            session_token = exact_cookie(request, SESSION_COOKIE_NAME)
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        try:
            current = await gateway.current_session(session_token=session_token)
        except InvalidSession:
            return _tenant_unauthorized(request)
        try:
            _cookie_lifetime(current.expires_at)
            return CurrentTenantSessionResponse(
                tenant_id=current.tenant_id,
                user_id=current.user_id,
                role_key=current.role_key,
                authentication_level=current.authentication_level,
                expires_at=current.expires_at,
                session_version=current.session_version,
                active_site_id=current.active_site_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="SESSION_INSPECTION_FAILED",
                message="The current session could not be inspected.",
                retryable=False,
            )

    @application.put(
        "/v1/session/site",
        response_model=SiteSelectionResponse,
        tags=["identity"],
    )
    async def select_site(
        request: Request,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> SiteSelectionResponse | JSONResponse:
        gateway = _browser_site_selection_gateway(request)
        try:
            selection = await _strict_json_request(
                request,
                SiteSelectionRequest,
                maximum=1024,
            )
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_SITE_SELECTION",
                message="The site selection request is invalid.",
                retryable=False,
            )
        try:
            selected = await gateway.select_site(
                session_token=proof.session_token,
                site_id=selection.site_id,
                expected_session_version=selection.expected_session_version,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except SiteSelectionDenied:
            return _error(
                request,
                status=403,
                code="SITE_SELECTION_DENIED",
                message="The requested site cannot be selected.",
                retryable=False,
            )
        except SessionContextConflict:
            return _error(
                request,
                status=409,
                code="SITE_CONTEXT_CONFLICT",
                message="The site context changed. Refresh before selecting a site.",
                retryable=True,
            )

        try:
            if selected.site_id != selection.site_id:
                raise ValueError
            return SiteSelectionResponse(
                tenant_id=selected.tenant_id,
                user_id=selected.user_id,
                site_id=selected.site_id,
                session_version=selected.session_version,
                changed=selected.changed,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="SITE_SELECTION_FAILED",
                message="The site context could not be established.",
                retryable=False,
            )

    @application.get(
        "/v1/sites",
        response_model=SitesResponse,
        responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["sites"],
    )
    async def site_directory(request: Request) -> SitesResponse | JSONResponse:
        gateway = _browser_site_directory_gateway(request)
        try:
            session_token = exact_cookie(request, SESSION_COOKIE_NAME)
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        try:
            directory = await gateway.site_directory(session_token=session_token)
        except InvalidSession:
            return _tenant_unauthorized(request)
        try:
            return SitesResponse(
                tenant_id=directory.tenant_id,
                tenant_name=directory.tenant_name,
                sites=tuple(
                    SiteSummary(
                        id=site.id,
                        name=site.name,
                        primary_origin=site.primary_origin,
                        timezone=site.timezone,
                        reporting_currency=site.reporting_currency,
                        state=site.state,
                        ownership_status=site.ownership_status,
                    )
                    for site in directory.sites
                ),
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="SITE_DIRECTORY_FAILED",
                message="The authorized site directory could not be read.",
                retryable=False,
            )

    @application.post(
        "/v1/sites",
        response_model=SiteOnboardingResponse,
        tags=["sites"],
    )
    async def create_site(
        request: Request,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> SiteOnboardingResponse | JSONResponse:
        gateway = _browser_site_onboarding_gateway(request)
        try:
            proposed = await _strict_json_request(
                request,
                SiteOnboardingRequest,
                maximum=4096,
            )
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_SITE_ONBOARDING",
                message="The site onboarding request is invalid.",
                retryable=False,
            )
        try:
            created = await gateway.onboard_site(
                session_token=proof.session_token,
                name=proposed.name,
                primary_origin=proposed.primary_origin,
                timezone=proposed.timezone,
                reporting_currency=proposed.reporting_currency,
                expected_session_version=proposed.expected_session_version,
                idempotency_key=proposed.idempotency_key,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except InvalidSiteOnboarding:
            return _error(
                request,
                status=422,
                code="INVALID_SITE_ONBOARDING",
                message="The site onboarding request is invalid.",
                retryable=False,
            )
        except SiteOnboardingDenied:
            return _error(
                request,
                status=403,
                code="SITE_ONBOARDING_DENIED",
                message="A site cannot be created for this session.",
                retryable=False,
            )
        except SiteOnboardingConflict:
            return _error(
                request,
                status=409,
                code="SITE_ONBOARDING_CONFLICT",
                message="The site context changed. Refresh before adding a site.",
                retryable=True,
            )
        except SiteLimitReached:
            return _error(
                request,
                status=409,
                code="SITE_LIMIT_REACHED",
                message="The organization cannot add another site.",
                retryable=False,
            )

        try:
            if (
                created.name != proposed.name
                or created.primary_origin != proposed.primary_origin
                or created.timezone != proposed.timezone
                or created.reporting_currency != proposed.reporting_currency
            ):
                raise ValueError
            return SiteOnboardingResponse(
                tenant_id=created.tenant_id,
                user_id=created.user_id,
                site_id=created.site_id,
                name=created.name,
                primary_origin=created.primary_origin,
                timezone=created.timezone,
                reporting_currency=created.reporting_currency,
                session_version=created.session_version,
                replayed=created.replayed,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="SITE_ONBOARDING_FAILED",
                message="The site could not be created.",
                retryable=False,
            )

    @application.get(
        "/v1/sites/{site_id}/standing-authorization",
        response_model=StandingGrantViewResponse,
        tags=["sites"],
    )
    async def read_standing_grant(
        request: Request, site_id: UUID
    ) -> StandingGrantViewResponse | JSONResponse:
        gateway = _browser_standing_grant_gateway(request)
        try:
            token = exact_cookie(request, SESSION_COOKIE_NAME)
            view = await gateway.read_standing_authorization(session_token=token, site_id=site_id)
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        except StandingAuthorizationUnavailable as error:
            return _standing_grant_error(request, error)
        return StandingGrantViewResponse(
            site_id=site_id,
            state=view.state,
            grant_id=view.grant_id,
            recipe_release_ids=view.recipe_release_ids,
            work_types=view.work_types,
            thresholds=view.thresholds,
            weekly_volume_caps=view.weekly_volume_caps,
            weekly_total_cap=view.weekly_total_cap,
            weekly_spend_cents=view.weekly_spend_cents,
            excluded_paths=view.excluded_paths,
            starts_at=view.starts_at,
            ends_at=view.ends_at,
            recovery_window_hours=view.recovery_window_hours,
            restriction_event_id=view.restriction_event_id,
            durability=view.durability,
        )

    @application.post(
        "/v1/sites/{site_id}/standing-authorization",
        response_model=StandingGrantResponse,
        status_code=201,
        tags=["sites"],
    )
    async def grant_standing_grant(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> StandingGrantResponse | JSONResponse:
        gateway = _browser_standing_grant_gateway(request)
        try:
            proposed = await _strict_standing_grant_request(request)
            grant_request = StandingGrantRequest(
                site_id=site_id,
                recipe_ranges=tuple(
                    RecipeRange(item.key, item.minimum_inclusive, item.maximum_exclusive)
                    for item in proposed.recipe_ranges
                ),
                thresholds=proposed.thresholds,
                weekly_volume_caps=proposed.weekly_volume_caps,
                weekly_total_cap=proposed.weekly_total_cap,
                weekly_spend_cents=proposed.weekly_spend_cents,
                excluded_paths=proposed.excluded_paths,
                starts_at=proposed.starts_at,
                ends_at=proposed.ends_at,
                recovery_window_hours=proposed.recovery_window_hours,
            )
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_STANDING_GRANT",
                message="The standing authorization is invalid.",
                retryable=False,
            )
        try:
            granted = await gateway.grant_standing_authorization(
                session_token=proof.session_token, request=grant_request
            )
        except StandingAuthorizationConflict:
            return _error(
                request,
                status=409,
                code="STANDING_GRANT_EXISTS",
                message="A current site authorization already exists.",
                retryable=False,
            )
        except (StandingAuthorizationUnavailable, RecipeReleaseUnavailable) as error:
            return _standing_grant_error(request, error)
        return StandingGrantResponse(
            site_id=site_id,
            grant_id=granted.id,
            recipe_release_ids=granted.recipe_release_ids,
        )

    @application.post(
        "/v1/sites/{site_id}/standing-authorization/{grant_id}/revoke",
        response_model=StandingGrantRevokeResponse,
        status_code=202,
        tags=["sites"],
    )
    async def revoke_standing_grant(
        request: Request,
        site_id: UUID,
        grant_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> StandingGrantRevokeResponse | JSONResponse:
        gateway = _browser_standing_grant_gateway(request)
        try:
            await _strict_json_request(request, StandingGrantRevokeRequest, maximum=128)
            revoked = await gateway.revoke_standing_authorization(
                session_token=proof.session_token, site_id=site_id, grant_id=grant_id
            )
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_STANDING_REVOCATION",
                message="The revocation request is invalid.",
                retryable=False,
            )
        except StandingAuthorizationUnavailable as error:
            return _standing_grant_error(request, error)
        return StandingGrantRevokeResponse(
            site_id=site_id,
            grant_id=grant_id,
            restriction_event_id=revoked.restriction_event_id,
            durability=revoked.durability,
        )

    @application.get("/v1/sites/{site_id}/weekly-loop", tags=["sites"])
    async def read_weekly_pause(request: Request, site_id: UUID) -> JSONResponse:
        gateway = _browser_weekly_gateway(request)
        try:
            result = await gateway.read_site_pause(
                session_token=exact_cookie(request, SESSION_COOKIE_NAME), site_id=site_id
            )
        except PermissionError:
            return _tenant_unauthorized(request)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @application.post("/v1/sites/{site_id}/weekly-loop/pause", tags=["sites"])
    async def pause_weekly_site(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> JSONResponse:
        gateway = _browser_weekly_gateway(request)
        try:
            result = await gateway.set_site_paused(
                session_token=proof.session_token, site_id=site_id, paused=True
            )
        except PermissionError:
            return _tenant_unauthorized(request)
        return JSONResponse(
            {
                "state": result.state,
                "epoch": result.epoch,
                "draining_observations": result.draining_observations,
                "durability": result.durability,
            }
        )

    @application.post("/v1/sites/{site_id}/weekly-loop/resume", tags=["sites"])
    async def resume_weekly_site(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> JSONResponse:
        gateway = _browser_weekly_gateway(request)
        try:
            result = await gateway.set_site_paused(
                session_token=proof.session_token, site_id=site_id, paused=False
            )
        except PermissionError:
            return _tenant_unauthorized(request)
        return JSONResponse(
            {
                "state": result.state,
                "epoch": result.epoch,
                "draining_observations": result.draining_observations,
                "durability": result.durability,
            }
        )

    @application.get(
        "/v1/sites/{site_id}/weekly-cycles/latest",
        tags=["sites"],
        response_model=WeeklyReportResponse,
    )
    async def latest_weekly_cycle_report(request: Request, site_id: UUID) -> JSONResponse:
        return await weekly_cycle_report(request, site_id, None)

    @application.get(
        "/v1/sites/{site_id}/weekly-cycles/{week_start}",
        tags=["sites"],
        response_model=WeeklyReportResponse,
    )
    async def weekly_cycle_report(
        request: Request, site_id: UUID, week_start: date | None
    ) -> JSONResponse:
        gateway = _browser_weekly_gateway(request)
        try:
            token = exact_cookie(request, SESSION_COOKIE_NAME)
            report = await gateway.read_weekly_report(
                session_token=token, site_id=site_id, week_start=week_start
            )
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        if report is None:
            return _error(
                request,
                status=404,
                code="WEEKLY_CYCLE_UNAVAILABLE",
                message="The weekly cycle is unavailable.",
                retryable=False,
            )
        validated = WeeklyReportResponse.model_validate(report)
        if (
            validated.site_id != site_id
            or week_start is not None
            and validated.week_start != week_start
        ):
            raise ValueError("Weekly report scope differs from the request.")
        return JSONResponse(validated.model_dump(mode="json"))

    @application.get(
        "/v1/sites/{site_id}/weekly-cycles/{week_start}/observation/{command_id}",
        tags=["sites"],
    )
    async def weekly_observation_status(
        request: Request, site_id: UUID, week_start: date, command_id: UUID
    ) -> JSONResponse:
        gateway = _browser_weekly_gateway(request)
        try:
            token = exact_cookie(request, SESSION_COOKIE_NAME)
            report = await gateway.read_weekly_report(
                session_token=token, site_id=site_id, week_start=week_start
            )
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        observation = report.get("observation_command") if report is not None else None
        if not isinstance(observation, dict) or observation.get("command_id") != str(command_id):
            return _error(
                request,
                status=404,
                code="WEEKLY_OBSERVATION_UNAVAILABLE",
                message="The weekly observation is unavailable.",
                retryable=False,
            )
        return JSONResponse(observation)

    @application.post(
        "/v1/sites/{site_id}/origin-challenges",
        response_model=OriginChallengeResponse,
        status_code=201,
        tags=["sites"],
    )
    async def create_origin_challenge(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> OriginChallengeResponse | JSONResponse:
        gateway = _browser_origin_verification_gateway(request)
        try:
            proposed = await _strict_json_request(
                request,
                OriginChallengeRequest,
                maximum=3072,
            )
        except ValueError:
            return _origin_verification_error(
                request,
                InvalidOriginVerification(),
            )
        try:
            challenge = await gateway.issue_origin_challenge(
                session_token=proof.session_token,
                site_id=site_id,
                origin=proposed.origin,
                idempotency_key=proposed.idempotency_key,
            )
        except (
            InvalidSession,
            InvalidOriginVerification,
            OriginVerificationDenied,
            OriginVerificationConflict,
        ) as error:
            return _origin_verification_error(request, error)
        try:
            expected_content = f"signal-site-verification={challenge.challenge_id}\n"
            issued_at = _aware_datetime(challenge.issued_at)
            expires_at = _aware_datetime(challenge.expires_at)
            if (
                challenge.site_id != site_id
                or challenge.challenge_id.version != 4
                or challenge.origin != proposed.origin
                or challenge.proof_url
                != f"{proposed.origin}/.well-known/signal-site-verification.txt"
                or challenge.proof_content != expected_content
                or expires_at <= issued_at
            ):
                raise ValueError
            return OriginChallengeResponse(
                site_id=challenge.site_id,
                challenge_id=challenge.challenge_id,
                origin=challenge.origin,
                proof_url=challenge.proof_url,
                proof_content=challenge.proof_content,
                issued_at=issued_at,
                expires_at=expires_at,
                replayed=challenge.replayed,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="ORIGIN_CHALLENGE_FAILED",
                message="The origin challenge could not be issued.",
                retryable=False,
            )

    @application.post(
        "/v1/sites/{site_id}/verify-origin",
        response_model=OriginVerificationResponse,
        tags=["sites"],
    )
    async def verify_site_origin(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> OriginVerificationResponse | JSONResponse:
        gateway = _browser_origin_verification_gateway(request)
        try:
            proposed = await _strict_json_request(
                request,
                OriginVerificationRequest,
                maximum=3072,
            )
        except ValueError:
            return _origin_verification_error(
                request,
                InvalidOriginVerification(),
            )
        try:
            verified = await gateway.verify_origin(
                session_token=proof.session_token,
                site_id=site_id,
                challenge_id=proposed.challenge_id,
                origin=proposed.origin,
                idempotency_key=proposed.idempotency_key,
            )
        except (
            InvalidSession,
            InvalidOriginVerification,
            OriginVerificationDenied,
            OriginVerificationConflict,
            OriginChallengeNotFound,
            OriginChallengeExpired,
            OriginProofNotFound,
            OriginProofMismatch,
            OriginProofRejected,
            OriginProofUnavailable,
            OriginClaimConflict,
        ) as error:
            return _origin_verification_error(request, error)
        try:
            verified_at = _aware_datetime(verified.verified_at)
            recheck_at = _aware_datetime(verified.recheck_at)
            if (
                verified.site_id != site_id
                or verified.challenge_id != proposed.challenge_id
                or verified.origin != proposed.origin
                or verified.proof_method != "http_well_known"
                or recheck_at <= verified_at
            ):
                raise ValueError
            return OriginVerificationResponse(
                site_id=verified.site_id,
                challenge_id=verified.challenge_id,
                origin=verified.origin,
                permitted_origins=(verified.origin,),
                verified_at=verified_at,
                recheck_at=recheck_at,
                replayed=verified.replayed,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="ORIGIN_VERIFICATION_FAILED",
                message="The origin verification result was invalid.",
                retryable=False,
            )

    @application.post(
        "/v1/sites/{site_id}/commands/snapshot",
        response_model=SnapshotCommandAcceptedResponse,
        status_code=202,
        tags=["commands"],
    )
    async def request_snapshot_command(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> Response:
        gateway = _browser_command_gateway(request)
        try:
            await _strict_json_request(request, SnapshotCommandRequest, maximum=256)
            idempotency_key = _single_header_value(request, "idempotency-key")
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", idempotency_key) is None:
                raise ValueError
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_SNAPSHOT_COMMAND",
                message="The snapshot command request is invalid.",
                retryable=False,
            )
        try:
            accepted = await gateway.accept_snapshot_command(
                session_token=proof.session_token,
                site_id=site_id,
                idempotency_key=idempotency_key,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        except IdempotencyConflict:
            return _error(
                request,
                status=409,
                code="IDEMPOTENCY_CONFLICT",
                message="The idempotency key is already bound to another command.",
                retryable=False,
            )

        try:
            accepted_at = _aware_datetime(accepted.accepted_at)
            payload = SnapshotCommandAcceptedResponse(
                command_id=accepted.id,
                site_id=site_id,
                status=accepted.status,
                status_url=f"/v1/sites/{site_id}/commands/{accepted.id}",
                reused=accepted.reused,
                accepted_at=accepted_at,
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="COMMAND_ACCEPTANCE_FAILED",
                message="The command could not be accepted.",
                retryable=False,
            )
        response = JSONResponse(status_code=202, content=payload.model_dump(mode="json"))
        response.headers["Location"] = payload.status_url
        return response

    @application.get(
        "/v1/sites/{site_id}/commands/latest",
        response_model=SnapshotCommandStatusResponse,
        response_model_exclude_none=True,
        tags=["commands"],
    )
    async def latest_snapshot_command(
        request: Request,
        site_id: UUID,
    ) -> SnapshotCommandStatusResponse | JSONResponse:
        gateway = _browser_command_gateway(request)
        try:
            session_token = exact_cookie(request, SESSION_COOKIE_NAME)
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        try:
            status = await gateway.latest_snapshot_command(
                session_token=session_token,
                site_id=site_id,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        except CommandNotFound:
            return _error(
                request,
                status=404,
                code="COMMAND_NOT_FOUND",
                message="The command is unavailable.",
                retryable=False,
            )
        return _snapshot_status_response(request, site_id, status)

    @application.get(
        "/v1/sites/{site_id}/commands/{command_id}",
        response_model=SnapshotCommandStatusResponse,
        response_model_exclude_none=True,
        tags=["commands"],
    )
    async def snapshot_command_status(
        request: Request,
        site_id: UUID,
        command_id: UUID,
    ) -> SnapshotCommandStatusResponse | JSONResponse:
        gateway = _browser_command_gateway(request)
        try:
            session_token = exact_cookie(request, SESSION_COOKIE_NAME)
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        try:
            status = await gateway.snapshot_command_status(
                session_token=session_token,
                site_id=site_id,
                command_id=command_id,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        except CommandNotFound:
            return _error(
                request,
                status=404,
                code="COMMAND_NOT_FOUND",
                message="The command is unavailable.",
                retryable=False,
            )

        if status.id != command_id:
            return _error(
                request,
                status=500,
                code="COMMAND_STATUS_FAILED",
                message="The command status could not be read.",
                retryable=False,
            )
        return _snapshot_status_response(request, site_id, status)

    @application.post(
        "/v1/sites/{site_id}/analysis/local-fixture",
        response_model=LocalFixtureAnalysisResponse,
        tags=["evidence"],
    )
    async def analyze_local_fixture(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> LocalFixtureAnalysisResponse | JSONResponse:
        gateway = _browser_finding_gateway(request)
        try:
            await _strict_json_request(request, LocalFixtureAnalysisRequest, maximum=256)
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_FIXTURE_ANALYSIS",
                message="The fixture analysis request is invalid.",
                retryable=False,
            )
        try:
            finding = await gateway.analyze_local_fixture(
                session_token=proof.session_token,
                site_id=site_id,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        except AuditNotReady:
            return _error(
                request,
                status=409,
                code="AUDIT_NOT_READY",
                message="Complete a site audit before running fixture analysis.",
                retryable=False,
            )
        except EvidenceConflict:
            return _error(
                request,
                status=409,
                code="EVIDENCE_CONFLICT",
                message="The fixture evidence conflicts with the committed audit evidence.",
                retryable=False,
            )
        try:
            return LocalFixtureAnalysisResponse(
                site_id=site_id,
                finding=_finding_response(finding),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="FIXTURE_ANALYSIS_FAILED",
                message="The fixture analysis result is invalid.",
                retryable=False,
            )

    @application.post(
        "/v1/sites/{site_id}/analysis/verified-homepage",
        response_model=VerifiedHomepageAnalysisResponse,
        tags=["evidence"],
    )
    async def analyze_verified_homepage(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> VerifiedHomepageAnalysisResponse | JSONResponse:
        gateway = _browser_finding_gateway(request)
        try:
            command = await _strict_json_request(
                request, VerifiedHomepageAnalysisRequest, maximum=512
            )
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_PAGE_ANALYSIS",
                message="The page analysis request is invalid.",
                retryable=False,
            )
        try:
            analysis: PageAnalysis = await gateway.analyze_verified_homepage(
                session_token=proof.session_token,
                site_id=site_id,
                idempotency_key=command.idempotency_key,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        except OriginNotVerified:
            return _error(
                request,
                status=409,
                code="ORIGIN_NOT_VERIFIED",
                message="Verify the selected site origin before analyzing its homepage.",
                retryable=False,
            )
        except PageObservationNotReady:
            return _error(
                request,
                status=409,
                code="AUDIT_NOT_READY",
                message="Complete a site audit before analyzing the verified homepage.",
                retryable=False,
            )
        except PageObservationConflict:
            return _error(
                request,
                status=409,
                code="PAGE_OBSERVATION_CONFLICT",
                message="The page observation conflicts with committed evidence.",
                retryable=False,
            )
        except PageObservationFailed as exc:
            return _error(
                request,
                status=502,
                code="PAGE_OBSERVATION_FAILED",
                message="The verified homepage could not be observed through the bounded reader.",
                retryable=exc.outcome in {"transport_unavailable", "http_rejected"},
            )
        try:
            return VerifiedHomepageAnalysisResponse(
                site_id=site_id,
                observation=_page_observation_response(analysis.observation),
                finding=(
                    _finding_response(analysis.finding) if analysis.finding is not None else None
                ),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="PAGE_ANALYSIS_FAILED",
                message="The page analysis result is invalid.",
                retryable=False,
            )

    @application.get(
        "/v1/sites/{site_id}/observations/homepage/latest",
        response_model=LatestPageObservationResponse,
        tags=["evidence"],
    )
    async def latest_page_observation(
        request: Request,
        site_id: UUID,
    ) -> LatestPageObservationResponse | JSONResponse:
        gateway = _browser_finding_gateway(request)
        try:
            session_token = exact_cookie(request, SESSION_COOKIE_NAME)
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        try:
            observation = await gateway.latest_page_observation(
                session_token=session_token,
                site_id=site_id,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        try:
            return LatestPageObservationResponse(
                site_id=site_id,
                observation=(
                    _page_observation_response(observation) if observation is not None else None
                ),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="PAGE_OBSERVATION_READ_FAILED",
                message="The page observation projection is invalid.",
                retryable=False,
            )

    @application.get(
        "/v1/sites/{site_id}/findings",
        response_model=FindingsResponse,
        tags=["evidence"],
    )
    async def findings(
        request: Request,
        site_id: UUID,
    ) -> FindingsResponse | JSONResponse:
        gateway = _browser_finding_gateway(request)
        try:
            session_token = exact_cookie(request, SESSION_COOKIE_NAME)
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        try:
            current = await gateway.findings(session_token=session_token, site_id=site_id)
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        try:
            return FindingsResponse(
                site_id=site_id,
                findings=tuple(_finding_response(finding) for finding in current),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="FINDING_READ_FAILED",
                message="The finding projection is invalid.",
                retryable=False,
            )

    @application.post(
        "/v1/sites/{site_id}/proposals/local-fixture",
        response_model=LocalProposalResponse,
        tags=["planning"],
    )
    async def prepare_local_fixture_proposal(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> LocalProposalResponse | JSONResponse:
        gateway = _browser_proposal_gateway(request)
        try:
            await _strict_json_request(request, LocalProposalRequest, maximum=256)
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_PROPOSAL_REQUEST",
                message="The proposal request is invalid.",
                retryable=False,
            )
        try:
            proposal = await gateway.prepare_local_fixture_proposal(
                session_token=proof.session_token,
                site_id=site_id,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        except ApprovalPermissionDenied:
            return _error(
                request,
                status=403,
                code="APPROVAL_PERMISSION_DENIED",
                message="Only the current site owner can prepare this approval request.",
                retryable=False,
            )
        except ProposalNotReady:
            return _error(
                request,
                status=409,
                code="PROPOSAL_NOT_READY",
                message="Record the current fixture finding before preparing a proposal.",
                retryable=False,
            )
        except ProposalManifestConflict:
            return _error(
                request,
                status=409,
                code="PROPOSAL_EVIDENCE_CHANGED",
                message="The supporting evidence changed. Refresh before preparing a proposal.",
                retryable=True,
            )
        try:
            return LocalProposalResponse(
                site_id=site_id,
                proposal=_proposal_response(proposal),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="PROPOSAL_PREPARATION_FAILED",
                message="The proposal result is invalid.",
                retryable=False,
            )

    @application.post(
        "/v1/sites/{site_id}/proposals/model-fixture",
        response_model=LocalProposalResponse,
        tags=["planning"],
    )
    async def prepare_model_fixture_proposal(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> LocalProposalResponse | JSONResponse:
        gateway = _browser_proposal_gateway(request)
        try:
            await _strict_json_request(request, LocalProposalRequest, maximum=256)
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_PROPOSAL_REQUEST",
                message="The model proposal request is invalid.",
                retryable=False,
            )
        try:
            proposal = await gateway.prepare_model_fixture_proposal(
                session_token=proof.session_token,
                site_id=site_id,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        except ApprovalPermissionDenied:
            return _error(
                request,
                status=403,
                code="APPROVAL_PERMISSION_DENIED",
                message="Only the current site owner can prepare this approval request.",
                retryable=False,
            )
        except ProposalNotReady:
            return _error(
                request,
                status=409,
                code="PROPOSAL_NOT_READY",
                message="Record the current fixture finding before preparing a proposal.",
                retryable=False,
            )
        except ModelRunInProgress:
            return _error(
                request,
                status=409,
                code="MODEL_RUN_IN_PROGRESS",
                message="This exact evidence already has a model draft in progress.",
                retryable=True,
            )
        except ModelOutcomeUnknown:
            return _error(
                request,
                status=409,
                code="MODEL_OUTCOME_UNKNOWN",
                message="A prior model call has an unknown outcome and was not retried.",
                retryable=False,
            )
        except ModelAttemptLimit:
            return _error(
                request,
                status=409,
                code="MODEL_ATTEMPT_LIMIT",
                message="The bounded model attempt limit has been reached for this evidence.",
                retryable=False,
            )
        except (ModelInputConflict, ProposalManifestConflict):
            return _error(
                request,
                status=409,
                code="PROPOSAL_EVIDENCE_CHANGED",
                message="The supporting evidence changed. Refresh before preparing a proposal.",
                retryable=True,
            )
        except MetadataDraftError as error:
            unavailable = error.code in {
                "MODEL_NOT_CONFIGURED",
                "MODEL_CREDENTIAL_UNAVAILABLE",
                "MODEL_TRANSPORT_UNAVAILABLE",
                "MODEL_RATE_LIMITED",
                "MODEL_UNCONFIGURED",
                "MODEL_UNAVAILABLE",
                "MODEL_BUDGET_UNCONFIGURED",
                "MODEL_BUDGET_EXHAUSTED",
                "MODEL_BUDGET_DISPATCH_DENIED",
                "MODEL_LOW_QUALITY",
            }
            return _error(
                request,
                status=503 if unavailable else 502,
                code=error.code,
                message="The bounded model draft could not be completed safely.",
                retryable=error.retryable,
            )
        try:
            return LocalProposalResponse(
                site_id=site_id,
                proposal=_proposal_response(proposal),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="PROPOSAL_PREPARATION_FAILED",
                message="The proposal result is invalid.",
                retryable=False,
            )

    @application.post(
        "/v1/sites/{site_id}/proposals/verified-homepage",
        response_model=LocalProposalResponse,
        tags=["planning"],
    )
    async def prepare_verified_homepage_proposal(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> LocalProposalResponse | JSONResponse:
        gateway = _browser_proposal_gateway(request)
        try:
            await _strict_json_request(request, LocalProposalRequest, maximum=256)
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_PROPOSAL_REQUEST",
                message="The verified-homepage proposal request is invalid.",
                retryable=False,
            )
        try:
            proposal = await gateway.prepare_verified_homepage_proposal(
                session_token=proof.session_token,
                site_id=site_id,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        except ApprovalPermissionDenied:
            return _error(
                request,
                status=403,
                code="APPROVAL_PERMISSION_DENIED",
                message="Only the current site owner can prepare this approval request.",
                retryable=False,
            )
        except ProposalNotReady:
            return _error(
                request,
                status=409,
                code="PROPOSAL_NOT_READY",
                message=(
                    "Analyze the verified homepage and retain a supported title or heading "
                    "before preparing a proposal."
                ),
                retryable=False,
            )
        except ModelRunInProgress:
            return _error(
                request,
                status=409,
                code="MODEL_RUN_IN_PROGRESS",
                message="This exact evidence already has a model draft in progress.",
                retryable=True,
            )
        except ModelOutcomeUnknown:
            return _error(
                request,
                status=409,
                code="MODEL_OUTCOME_UNKNOWN",
                message="A prior model call has an unknown outcome and was not retried.",
                retryable=False,
            )
        except ModelAttemptLimit:
            return _error(
                request,
                status=409,
                code="MODEL_ATTEMPT_LIMIT",
                message="The bounded model attempt limit has been reached for this evidence.",
                retryable=False,
            )
        except (ModelInputConflict, ProposalManifestConflict):
            return _error(
                request,
                status=409,
                code="PROPOSAL_EVIDENCE_CHANGED",
                message="The supporting evidence changed. Refresh before preparing a proposal.",
                retryable=True,
            )
        except MetadataDraftError as error:
            unavailable = error.code in {
                "MODEL_NOT_CONFIGURED",
                "MODEL_CREDENTIAL_UNAVAILABLE",
                "MODEL_TRANSPORT_UNAVAILABLE",
                "MODEL_RATE_LIMITED",
                "MODEL_UNCONFIGURED",
                "MODEL_UNAVAILABLE",
                "MODEL_BUDGET_UNCONFIGURED",
                "MODEL_BUDGET_EXHAUSTED",
                "MODEL_BUDGET_DISPATCH_DENIED",
                "MODEL_LOW_QUALITY",
            }
            return _error(
                request,
                status=503 if unavailable else 502,
                code=error.code,
                message="The bounded model draft could not be completed safely.",
                retryable=error.retryable,
            )
        try:
            return LocalProposalResponse(
                site_id=site_id,
                proposal=_proposal_response(proposal),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="PROPOSAL_PREPARATION_FAILED",
                message="The proposal result is invalid.",
                retryable=False,
            )

    @application.get(
        "/v1/sites/{site_id}/proposals",
        response_model=ProposalsResponse,
        tags=["planning"],
    )
    async def proposals(
        request: Request,
        site_id: UUID,
    ) -> ProposalsResponse | JSONResponse:
        gateway = _browser_proposal_gateway(request)
        try:
            session_token = exact_cookie(request, SESSION_COOKIE_NAME)
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        try:
            current = await gateway.local_fixture_proposals(
                session_token=session_token,
                site_id=site_id,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        try:
            return ProposalsResponse(
                site_id=site_id,
                proposals=tuple(_proposal_response(proposal) for proposal in current),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="PROPOSAL_READ_FAILED",
                message="The proposal projection is invalid.",
                retryable=False,
            )

    @application.get(
        "/v1/sites/{site_id}/candidate-recipe-inbox",
        response_model=CandidateInboxResponse,
        tags=["approvals"],
    )
    async def candidate_recipe_inbox(
        request: Request, site_id: UUID
    ) -> CandidateInboxResponse | JSONResponse:
        gateway = _browser_candidate_inbox_gateway(request)
        try:
            session_token = exact_cookie(request, SESSION_COOKIE_NAME)
            revisions = await gateway.candidate_recipe_inbox(
                session_token=session_token, site_id=site_id
            )
        except (BrowserRequestRejected, InvalidSession):
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        try:
            return CandidateInboxResponse(
                site_id=site_id,
                revisions=tuple(_candidate_inbox_response(item) for item in revisions),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="CANDIDATE_INBOX_READ_FAILED",
                message="The candidate Inbox projection is invalid.",
                retryable=False,
            )

    @application.get(
        "/v1/sites/{site_id}/github-pr-operations",
        response_model=GitHubPrOperationsResponse,
        tags=["changes"],
    )
    async def github_pr_operations(
        request: Request, site_id: UUID
    ) -> GitHubPrOperationsResponse | JSONResponse:
        gateway = _browser_github_pr_operations_gateway(request)
        try:
            session_token = exact_cookie(request, SESSION_COOKIE_NAME)
            operations = await gateway.github_pr_operations(
                session_token=session_token, site_id=site_id
            )
        except (BrowserRequestRejected, InvalidSession):
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        except GitHubPrDeliveryUnavailable:
            return _error(
                request,
                status=503,
                code="PR_OPERATION_READ_UNAVAILABLE",
                message="Pull-request operation evidence is temporarily unavailable.",
                retryable=True,
            )
        try:
            return GitHubPrOperationsResponse(
                site_id=site_id,
                operations=tuple(
                    GitHubPrOperationResponse(
                        operation_id=item.id,
                        revision_id=item.revision_id,
                        revision_sha256=item.revision_sha256,
                        branch_name=item.branch_name,
                        state=item.state,
                        step=item.step,
                        base_sha=item.base_sha,
                        expected_tree_sha=item.expected_tree_sha,
                        expected_commit_sha=item.expected_commit_sha,
                        pr_number=item.pr_number,
                        pr_url=item.pr_url,
                        created_at=_aware_datetime(item.created_at),
                        updated_at=_aware_datetime(item.updated_at),
                        journal_generation=item.journal_generation,
                        journal_position=item.journal_position,
                        journal_body_hash=item.journal_body_hash,
                        authority={
                            "kind": item.authority_kind,
                            "record_id": item.authority_id,
                            "owner_user_id": item.authority_owner_user_id,
                            "decision_channel": item.authority_decision_channel,
                        }
                        if item.authority_kind is not None
                        else None,
                    )
                    for item in operations
                ),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="PR_OPERATION_READ_INVALID",
                message="Pull-request operation evidence is invalid.",
                retryable=False,
            )

    @application.get(
        "/v1/sites/{site_id}/github-delivery-observations",
        response_model=GitHubDeliveryObservationsResponse,
        tags=["changes"],
    )
    async def github_delivery_observations(request: Request, site_id: UUID):
        gateway = getattr(request.app.state, "browser_github_delivery", None)
        if not isinstance(gateway, BrowserGithubDeliveryGateway):
            raise BrowserAuthenticationUnavailable()
        try:
            session_token = exact_cookie(request, SESSION_COOKIE_NAME)
            observations = await gateway.github_delivery_observations(
                session_token=session_token, site_id=site_id
            )
        except (BrowserRequestRejected, InvalidSession):
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        except GitHubObservationUnavailable:
            return _error(
                request,
                status=503,
                code="DELIVERY_OBSERVATION_UNAVAILABLE",
                message="Delivery observation evidence is temporarily unavailable.",
                retryable=True,
            )
        try:
            import rfc8785

            return GitHubDeliveryObservationsResponse(
                site_id=site_id,
                observations=tuple(
                    GitHubDeliveryObservationResponse(
                        attempt_id=item.attempt_id,
                        operation_id=item.operation_id,
                        state=item.state,
                        canonical_receipt=rfc8785.dumps(item.receipt).decode("utf-8")
                        if item.receipt is not None
                        else None,
                        receipt_sha256=item.receipt_sha256,
                        observed_at=_aware_datetime(item.observed_at),
                        next_observe_at=_aware_datetime(item.next_observe_at),
                    )
                    for item in observations
                ),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="DELIVERY_OBSERVATION_INVALID",
                message="Delivery observation evidence is invalid.",
                retryable=False,
            )

    @application.post(
        "/v1/sites/{site_id}/candidate-recipe-revisions/{revision_id}/decision",
        response_model=CandidateInboxDecisionResponse,
        tags=["approvals"],
    )
    async def decide_candidate_recipe_revision(
        request: Request,
        site_id: UUID,
        revision_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> CandidateInboxDecisionResponse | JSONResponse:
        gateway = _browser_candidate_inbox_gateway(request)
        try:
            requested = await _strict_json_request(
                request, CandidateInboxDecisionRequest, maximum=1024
            )
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_APPROVAL_DECISION",
                message="The candidate review decision request is invalid.",
                retryable=False,
            )
        try:
            item = await gateway.decide_candidate_recipe_revision(
                session_token=proof.session_token,
                site_id=site_id,
                revision_id=revision_id,
                revision_sha256=requested.revision_sha256,
                decision_id=requested.decision_id,
                decision=requested.decision,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        except ApprovalPermissionDenied:
            return _error(
                request,
                status=403,
                code="APPROVAL_PERMISSION_DENIED",
                message="Only the current site owner can decide this revision.",
                retryable=False,
            )
        except ApprovalNotFound:
            return _error(
                request,
                status=404,
                code="APPROVAL_NOT_FOUND",
                message="The sealed revision is unavailable.",
                retryable=False,
            )
        except ApprovalRevisionConflict:
            return _error(
                request,
                status=409,
                code="APPROVAL_REVISION_CHANGED",
                message="The revision is no longer current. Refresh before deciding.",
                retryable=True,
            )
        except ApprovalDecisionConflict:
            return _error(
                request,
                status=409,
                code="APPROVAL_ALREADY_DECIDED",
                message="A different decision is already recorded.",
                retryable=False,
            )
        try:
            return CandidateInboxDecisionResponse(
                site_id=site_id,
                revision=_candidate_inbox_response(item),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="APPROVAL_DECISION_FAILED",
                message="The candidate review decision result is invalid.",
                retryable=False,
            )

    @application.post(
        "/v1/sites/{site_id}/approval-requests/{approval_request_id}/decision",
        response_model=ApprovalDecisionResponse,
        tags=["approvals"],
    )
    async def decide_local_fixture_approval(
        request: Request,
        site_id: UUID,
        approval_request_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> ApprovalDecisionResponse | JSONResponse:
        gateway = _browser_proposal_gateway(request)
        try:
            requested = await _strict_json_request(request, ApprovalDecisionRequest, maximum=1024)
        except ValueError:
            return _error(
                request,
                status=422,
                code="INVALID_APPROVAL_DECISION",
                message="The approval decision request is invalid.",
                retryable=False,
            )
        try:
            proposal = await gateway.decide_local_fixture_approval(
                session_token=proof.session_token,
                site_id=site_id,
                approval_request_id=approval_request_id,
                revision_sha256=requested.revision_sha256,
                decision_id=requested.decision_id,
                decision=requested.decision,
            )
        except InvalidSession:
            return _tenant_unauthorized(request)
        except AuthorizationDenied:
            return _site_command_denied(request)
        except ApprovalPermissionDenied:
            return _error(
                request,
                status=403,
                code="APPROVAL_PERMISSION_DENIED",
                message="Only the current site owner can decide this approval request.",
                retryable=False,
            )
        except ApprovalNotFound:
            return _error(
                request,
                status=404,
                code="APPROVAL_NOT_FOUND",
                message="The approval request is unavailable.",
                retryable=False,
            )
        except ApprovalExpired:
            return _error(
                request,
                status=409,
                code="APPROVAL_EXPIRED",
                message="The approval request expired. Prepare a current revision.",
                retryable=False,
            )
        except ApprovalRevisionConflict:
            return _error(
                request,
                status=409,
                code="APPROVAL_REVISION_CHANGED",
                message="The approval revision changed. Refresh before deciding.",
                retryable=True,
            )
        except ApprovalDecisionConflict:
            return _error(
                request,
                status=409,
                code="APPROVAL_ALREADY_DECIDED",
                message="A different decision is already recorded.",
                retryable=False,
            )
        try:
            return ApprovalDecisionResponse(
                site_id=site_id,
                proposal=_proposal_response(proposal),
                correlation_id=request.state.correlation_id,
            )
        except (AttributeError, TypeError, ValueError):
            return _error(
                request,
                status=500,
                code="APPROVAL_DECISION_FAILED",
                message="The approval decision result is invalid.",
                retryable=False,
            )

    @application.post("/v1/session/logout", status_code=202, tags=["identity"])
    async def logout(
        request: Request,
        proof: Annotated[BrowserLogoutProof, Depends(require_browser_logout_mutation)],
    ) -> Response:
        gateway = _browser_session_gateway(request)
        revoked = await gateway.logout(
            session_token=proof.session_token,
            presented_session_kind=proof.session_kind,
        )
        if not isinstance(revoked, bool):
            return _error(
                request,
                status=500,
                code="SESSION_LOGOUT_FAILED",
                message="The session could not be revoked.",
                retryable=False,
            )
        response = JSONResponse(
            status_code=202,
            content={"schema_version": 1, "status": "AUTHORITY_DURABILITY_PENDING"},
        )
        clear_oidc_binding_cookie(response)
        clear_identity_cookie(response)
        clear_session_cookie(response)
        clear_invitation_identity_cookie(response)
        return response

    @application.post(
        "/v1/sites/{site_id}/brand-documents",
        response_model=BrandDocumentResponse,
        status_code=201,
        tags=["sites"],
    )
    async def upload_document(
        request: Request,
        site_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> BrandDocumentResponse | JSONResponse:
        gateway = _browser_document_gateway(request)
        try:
            proposed = await _strict_json_request(
                request, BrandDocumentUploadRequest, maximum=2_800_000
            )
            body = base64.b64decode(proposed.content_base64, validate=True)
            if len(body) > 2 * 1024 * 1024:
                raise ValueError
            document = await gateway.upload_brand_document(
                session_token=proof.session_token,
                site_id=site_id,
                filename=proposed.filename,
                body=body,
                supersedes_id=proposed.supersedes_id,
            )
        except (ValueError, binascii.Error, BrandDocumentRejected) as error:
            reason = str(error)
            return _error(
                request,
                status=422,
                code="BRAND_DOCUMENT_REJECTED",
                message=(
                    reason
                    if re.fullmatch(r"[a-z_]{3,64}", reason)
                    else "The document failed validation."
                ),
                retryable=False,
            )
        except BrandDocumentUnavailable as error:
            return _document_unavailable(request, error)
        return BrandDocumentResponse(**document.__dict__)

    @application.get(
        "/v1/sites/{site_id}/brand-documents",
        response_model=BrandDocumentsResponse,
        tags=["sites"],
    )
    async def documents(request: Request, site_id: UUID) -> BrandDocumentsResponse | JSONResponse:
        gateway = _browser_document_gateway(request)
        try:
            token = exact_cookie(request, SESSION_COOKIE_NAME)
            records = await gateway.list_brand_documents(session_token=token, site_id=site_id)
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        except BrandDocumentUnavailable as error:
            return _document_unavailable(request, error)
        return BrandDocumentsResponse(
            documents=tuple(BrandDocumentResponse(**item.__dict__) for item in records)
        )

    @application.get(
        "/v1/sites/{site_id}/brand-documents/{document_id}",
        response_model=BrandDocumentTextResponse,
        tags=["sites"],
    )
    async def document_text(
        request: Request, site_id: UUID, document_id: UUID
    ) -> BrandDocumentTextResponse | JSONResponse:
        gateway = _browser_document_gateway(request)
        try:
            token = exact_cookie(request, SESSION_COOKIE_NAME)
            result = await gateway.read_brand_document(
                session_token=token, site_id=site_id, document_id=document_id
            )
        except BrowserRequestRejected:
            return _tenant_unauthorized(request)
        except BrandDocumentUnavailable as error:
            return _document_unavailable(request, error)
        return BrandDocumentTextResponse(
            document_id=result.document_id,
            trust_label=result.trust_label,
            text=result.text,
            text_sha256=result.text_sha256,
            injection_signal=result.injection_signal,
        )

    @application.delete(
        "/v1/sites/{site_id}/brand-documents/{document_id}",
        response_model=BrandDocumentDeletionResponse,
        tags=["sites"],
    )
    async def delete_document(
        request: Request,
        site_id: UUID,
        document_id: UUID,
        proof: Annotated[BrowserMutationProof, Depends(require_browser_mutation)],
    ) -> BrandDocumentDeletionResponse | JSONResponse:
        gateway = _browser_document_gateway(request)
        outcome = await gateway.delete_brand_document(
            session_token=proof.session_token, site_id=site_id, document_id=document_id
        )
        if outcome == "denied":
            return _document_unavailable(request, BrandDocumentUnavailable("owner_access_denied"))
        if outcome == "missing":
            return _error(
                request,
                status=404,
                code="DOCUMENT_UNAVAILABLE",
                message="The document is unavailable.",
                retryable=False,
            )
        if outcome != "deleted_retained":
            return _error(
                request,
                status=500,
                code="DOCUMENT_DELETE_FAILED",
                message="The document could not be deleted.",
                retryable=False,
            )
        return BrandDocumentDeletionResponse(outcome="deleted_retained")

    from signal_api.business_brain_routes import register_business_brain_routes

    register_business_brain_routes(application)
    from signal_api.seo_strategy_routes import register_seo_strategy_routes

    register_seo_strategy_routes(application)
    from signal_api.ask_signal_routes import register_ask_signal_routes

    register_ask_signal_routes(application)

    from signal_api.pagespeed_routes import register_pagespeed_routes

    register_pagespeed_routes(application)
    from signal_api.content_writer_routes import register_content_writer_routes

    register_content_writer_routes(application)
    from signal_api.webflow_routes import register_webflow_routes

    register_webflow_routes(application)

    from signal_api.ai_visibility_routes import register_ai_visibility_routes

    register_ai_visibility_routes(application)
    return application


def _browser_login_gateway(request: Request) -> BrowserLoginGateway:
    gateway = getattr(request.app.state, "browser_login", None)
    if not isinstance(gateway, BrowserLoginGateway):
        raise BrowserAuthenticationUnavailable()
    if isinstance(gateway.attempt_ttl_seconds, bool) or not (
        60 <= gateway.attempt_ttl_seconds <= 600
    ):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_document_gateway(request: Request) -> BrowserBrandDocumentGateway:
    gateway = getattr(request.app.state, "browser_documents", None)
    if not isinstance(gateway, BrowserBrandDocumentGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _document_unavailable(request: Request, error: BrandDocumentUnavailable) -> JSONResponse:
    if str(error) == "owner_access_denied":
        return _error(
            request,
            status=403,
            code="DOCUMENT_ACCESS_DENIED",
            message="Only the current site owner can manage documents.",
            retryable=False,
        )
    if str(error) in {"document_unavailable", "document_contains_secret"}:
        return _error(
            request,
            status=404,
            code="DOCUMENT_UNAVAILABLE",
            message="The document is unavailable.",
            retryable=False,
        )
    return _error(
        request,
        status=503,
        code="DOCUMENT_STORAGE_UNAVAILABLE",
        message="Document storage or extraction is unavailable.",
        retryable=True,
    )


def _browser_tenant_gateway(request: Request) -> BrowserTenantGateway:
    gateway = getattr(request.app.state, "browser_login", None)
    if not isinstance(gateway, BrowserTenantGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_invitation_gateway(request: Request) -> BrowserInvitationGateway:
    gateway = getattr(request.app.state, "browser_login", None)
    if not isinstance(gateway, BrowserInvitationGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_session_gateway(request: Request) -> BrowserSessionGateway:
    gateway = getattr(request.app.state, "browser_login", None)
    if not isinstance(gateway, BrowserSessionGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_site_directory_gateway(request: Request) -> BrowserSiteDirectoryGateway:
    gateway = getattr(request.app.state, "browser_login", None)
    if not isinstance(gateway, BrowserSiteDirectoryGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_site_onboarding_gateway(request: Request) -> BrowserSiteOnboardingGateway:
    gateway = getattr(request.app.state, "browser_login", None)
    if not isinstance(gateway, BrowserSiteOnboardingGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_standing_grant_gateway(request: Request) -> BrowserStandingGrantGateway:
    gateway = getattr(request.app.state, "browser_login", None)
    if not isinstance(gateway, BrowserStandingGrantGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_weekly_gateway(request: Request) -> BrowserWeeklyGateway:
    gateway = getattr(request.app.state, "browser_login", None)
    if not isinstance(gateway, BrowserWeeklyGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _standing_grant_error(
    request: Request, error: StandingAuthorizationUnavailable | RecipeReleaseUnavailable
) -> JSONResponse:
    if isinstance(error, StandingAuthorizationUnavailable) and str(error) == "invalid_session":
        return _tenant_unauthorized(request)
    if isinstance(error, StandingAuthorizationUnavailable) and str(error) in {
        "permission_denied",
        "authorization_denied",
    }:
        return _site_command_denied(request)
    return _error(
        request,
        status=409,
        code="STANDING_GRANT_UNAVAILABLE",
        message="The standing authorization is unavailable for this site.",
        retryable=False,
    )


def _browser_origin_verification_gateway(
    request: Request,
) -> BrowserOriginVerificationGateway:
    gateway = getattr(request.app.state, "browser_login", None)
    if not isinstance(gateway, BrowserOriginVerificationGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_site_selection_gateway(request: Request) -> BrowserSiteSelectionGateway:
    gateway = getattr(request.app.state, "browser_login", None)
    if not isinstance(gateway, BrowserSiteSelectionGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_command_gateway(request: Request) -> BrowserCommandGateway:
    gateway = getattr(request.app.state, "browser_login", None)
    if not isinstance(gateway, BrowserCommandGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_finding_gateway(request: Request) -> BrowserFindingGateway:
    gateway = getattr(request.app.state, "browser_fixture_analysis", None)
    if not isinstance(gateway, BrowserFindingGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_proposal_gateway(request: Request) -> BrowserProposalGateway:
    gateway = getattr(request.app.state, "browser_proposals", None)
    if not isinstance(gateway, BrowserProposalGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_candidate_inbox_gateway(request: Request) -> BrowserCandidateInboxGateway:
    gateway = getattr(request.app.state, "browser_candidate_inbox", None)
    if not isinstance(gateway, BrowserCandidateInboxGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_github_pr_operations_gateway(request: Request) -> BrowserGithubPrOperationsGateway:
    gateway = getattr(request.app.state, "browser_github_pr_operations", None)
    if not isinstance(gateway, BrowserGithubPrOperationsGateway):
        raise BrowserAuthenticationUnavailable()
    return gateway


def _browser_security(request: Request) -> BrowserSecurity:
    security = getattr(request.app.state, "browser_security", None)
    if not isinstance(security, BrowserSecurity):
        raise BrowserSecurityUnavailable()
    return security


def _single_query_value(request: Request, name: str, *, default: str | None = None) -> str:
    sensitive_items = getattr(request.state, "sensitive_query_items", None)
    values = (
        [value for key, value in sensitive_items if key == name]
        if sensitive_items is not None
        else request.query_params.getlist(name)
    )
    if not values and default is not None:
        return default
    if len(values) != 1:
        raise ValueError("Ambiguous query input.")
    return values[0]


def _single_header_value(request: Request, name: str) -> str:
    target = name.lower().encode("ascii")
    values = [
        value.decode("latin-1").strip()
        for key, value in request.scope.get("headers", ())
        if key.lower() == target
    ]
    if len(values) != 1:
        raise ValueError("Ambiguous header input.")
    return values[0]


def _identity_unauthorized(request: Request) -> JSONResponse:
    response = _error(
        request,
        status=401,
        code="IDENTITY_SESSION_REQUIRED",
        message="A valid identity session is required.",
        retryable=False,
    )
    clear_identity_cookie(response)
    clear_session_cookie(response)
    return response


def _tenant_unauthorized(request: Request) -> JSONResponse:
    response = _error(
        request,
        status=401,
        code="TENANT_SESSION_REQUIRED",
        message="A valid tenant session is required.",
        retryable=False,
    )
    clear_session_cookie(response)
    return response


def _origin_verification_error(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, InvalidSession):
        return _tenant_unauthorized(request)
    mappings: tuple[tuple[type[Exception], int, str, str, bool], ...] = (
        (
            InvalidOriginVerification,
            422,
            "INVALID_ORIGIN_VERIFICATION",
            "The origin verification request is invalid.",
            False,
        ),
        (
            OriginVerificationDenied,
            403,
            "ORIGIN_VERIFICATION_DENIED",
            "The origin cannot be verified for this session.",
            False,
        ),
        (
            OriginChallengeNotFound,
            404,
            "ORIGIN_CHALLENGE_NOT_FOUND",
            "The origin challenge is unavailable.",
            False,
        ),
        (
            OriginChallengeExpired,
            409,
            "ORIGIN_CHALLENGE_EXPIRED",
            "The origin challenge expired. Issue a new challenge.",
            True,
        ),
        (
            OriginProofNotFound,
            409,
            "ORIGIN_PROOF_NOT_FOUND",
            "The exact origin proof file was not found.",
            True,
        ),
        (
            OriginProofMismatch,
            409,
            "ORIGIN_PROOF_MISMATCH",
            "The origin proof file did not match the current challenge.",
            True,
        ),
        (
            OriginProofRejected,
            422,
            "ORIGIN_PROOF_REJECTED",
            "The origin proof response violated the verification policy.",
            False,
        ),
        (
            OriginProofUnavailable,
            503,
            "ORIGIN_PROOF_UNAVAILABLE",
            "The origin proof could not be reached.",
            True,
        ),
        (
            OriginClaimConflict,
            409,
            "ORIGIN_CLAIM_CONFLICT",
            "The origin has a conflicting protected control claim.",
            False,
        ),
        (
            OriginVerificationConflict,
            409,
            "ORIGIN_VERIFICATION_CONFLICT",
            "The origin verification state changed. Refresh and try again.",
            True,
        ),
    )
    for error_type, status, code, message, retryable in mappings:
        if isinstance(error, error_type):
            return _error(
                request,
                status=status,
                code=code,
                message=message,
                retryable=retryable,
            )
    return _error(
        request,
        status=500,
        code="ORIGIN_VERIFICATION_FAILED",
        message="The origin verification could not be completed.",
        retryable=False,
    )


def _site_command_denied(request: Request) -> JSONResponse:
    return _error(
        request,
        status=403,
        code="SITE_COMMAND_DENIED",
        message="The site command could not be authorized.",
        retryable=False,
    )


def _invitation_identity_unauthorized(request: Request) -> JSONResponse:
    response = _error(
        request,
        status=401,
        code="INVITATION_IDENTITY_REQUIRED",
        message="A valid invitation identity proof is required.",
        retryable=False,
    )
    clear_invitation_identity_cookie(response)
    return response


def _cookie_lifetime(expires_at: object) -> int:
    if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
        raise ValueError("Session expiry is invalid.")
    lifetime = math.ceil((expires_at.astimezone(UTC) - datetime.now(UTC)).total_seconds())
    if lifetime <= 0:
        raise ValueError("Session expiry is invalid.")
    return min(lifetime, 86_400)


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp is invalid.")
    return value


async def _tenant_selection_request(request: Request) -> TenantSelectionRequest:
    return await _strict_json_request(request, TenantSelectionRequest, maximum=1024)


async def _strict_json_request[RequestContract: Contract](
    request: Request,
    contract: type[RequestContract],
    *,
    maximum: int,
) -> RequestContract:
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip() != "application/json":
        raise ValueError("Request requires JSON.")
    if request.headers.getlist("content-encoding"):
        raise ValueError("Encoded request bodies are not accepted.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise ValueError("Request body is too large.")
    try:

        def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate request field.")
                result[key] = value
            return result

        parsed = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
        return contract.model_validate(parsed)
    except (TypeError, UnicodeDecodeError, ValueError):
        raise ValueError("Request body is invalid.") from None


async def _strict_standing_grant_request(request: Request) -> StandingGrantRequestContract:
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip() != "application/json":
        raise ValueError("Standing grant requires JSON.")
    if request.headers.getlist("content-encoding"):
        raise ValueError("Encoded standing grants are not accepted.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise ValueError("Standing grant body is too large.")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate standing grant field.")
            result[key] = value
        return result

    try:
        parsed = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
        return StandingGrantRequestContract.model_validate(parsed)
    except (TypeError, UnicodeDecodeError, ValueError):
        raise ValueError("Standing grant body is invalid.") from None


app = create_app()
