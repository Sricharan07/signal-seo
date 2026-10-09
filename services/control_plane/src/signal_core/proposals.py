"""Evidence-bound local proposal revisions and exact human decisions."""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import rfc8785
from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.audit_findings import AuditFinding, read_authenticated_findings
from signal_core.authorization import (
    AuthorizationDenied,
    InvalidSession,
    authorize_snapshot,
    validated_human_inputs,
)
from signal_core.database import _clean_transaction
from signal_core.model_reasoning import (
    MODEL_PROMPT_SHA256,
    MODEL_RELEASE,
    OPENAI_MODEL,
    VERIFIED_MODEL_PROMPT_SHA256,
    VERIFIED_MODEL_RELEASE,
    MetadataDraft,
    MetadataDraftTask,
)
from signal_core.page_observations import PageObservation

LOCAL_PROPOSAL_KIND = "local_fixture_metadata_draft"
LOCAL_PROPOSED_DESCRIPTION = "Explore the Signal test fixture and its durable SEO evidence."
LOCAL_RECIPE_ID = "title_description_improvement"
LOCAL_RECIPE_VERSION = "local-fixture-0.1.0"
LOCAL_ROLE_RELEASE = "local-deterministic-v1"
MODEL_PROPOSAL_KIND = "model_fixture_metadata_draft"
MODEL_RECIPE_VERSION = "local-model-0.1.0"
VERIFIED_MODEL_PROPOSAL_KIND = "model_verified_homepage_metadata_draft"
VERIFIED_MODEL_RECIPE_VERSION = "verified-homepage-model-1.0.0"
VERIFIED_MODEL_AUTHORITY = "accept_verified_homepage_metadata_draft"
VERIFIED_RECOVERY_SUMMARY = "Discard the proposed draft; no external state has changed."
ApprovalDecision = Literal["approved", "rejected", "changes_requested"]
_DECISIONS = frozenset({"approved", "rejected", "changes_requested"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LEGACY_FIXTURE_PROMPT = "6c50fef0b4d7c0f096b34a2cd7f52de346e205bae3d788a7f25a7ddce73746e0"
_LEGACY_VERIFIED_PROMPT = "f963221dfbac7cc218bead459e6f11ca61d07e41a98ae8fa46d784f5d06b9347"


class ProposalNotReady(Exception):
    """No current evidence-backed finding can support a proposal."""


class ProposalManifestConflict(Exception):
    """The sealed proposal no longer matches current evidence."""


class ModelRunInProgress(Exception):
    """The exact evidence already has a model call in progress."""


class ModelOutcomeUnknown(Exception):
    """A prior model call has an unknown outcome and cannot be retried blindly."""


class ModelAttemptLimit(Exception):
    """The bounded retry allowance for this evidence has been exhausted."""


class ModelInputConflict(Exception):
    """The durable run input does not match the current exact task."""


class ApprovalPermissionDenied(Exception):
    """The current actor is not eligible to make the local decision."""


class ApprovalNotFound(Exception):
    """The scoped approval request does not exist."""


class ApprovalExpired(Exception):
    """The exact approval request expired before a decision was committed."""


class ApprovalRevisionConflict(Exception):
    """The supplied revision hash is not the approval request's exact revision."""


class ApprovalDecisionConflict(Exception):
    """A different decision is already committed for this approval request."""


@dataclass(frozen=True)
class LocalProposal:
    proposal_id: UUID
    revision_id: UUID
    revision_number: int
    revision_sha256: str
    manifest: dict[str, object]
    created_by_user_id: UUID
    created_at: datetime
    approval_request_id: UUID
    approval_status: str
    approval_requested_at: datetime
    approval_expires_at: datetime
    decision_id: UUID | None
    decision: ApprovalDecision | None
    decided_by_user_id: UUID | None
    decision_channel: str | None
    decided_at: datetime | None
    reused: bool = False


@dataclass(frozen=True)
class FixtureModelRun:
    agent_run_id: UUID
    model_call_id: UUID
    attempt_number: int
    finding_id: UUID
    evidence_id: UUID
    command_id: UUID
    manifest_id: UUID
    resource_locator: str
    confidence_class: str
    evidence_observed_at: datetime
    input_sha256: str
    status: str
    reused: bool
    outcome: Literal["started", "completed"]


def build_local_fixture_manifest(*, site_id: UUID, finding: AuditFinding) -> dict[str, object]:
    """Build the one exact fixture-only proposal supported by current evidence."""
    if not isinstance(site_id, UUID) or not isinstance(finding, AuditFinding):
        raise ValueError("A validated site and finding are required.")
    observed_at = _utc_timestamp(finding.evidence_observed_at)
    return {
        "schema_version": 1,
        "proposal_kind": LOCAL_PROPOSAL_KIND,
        "site_id": str(site_id),
        "finding": {
            "id": str(finding.id),
            "evidence_id": str(finding.evidence_id),
            "command_id": str(finding.command_id),
            "resource_locator": finding.resource_locator,
            "confidence_class": finding.confidence_class,
            "observed_at": observed_at,
        },
        "target": {
            "resource_locator": "/fixture/missing-meta-description",
            "field": "meta_description",
            "before": None,
            "after": LOCAL_PROPOSED_DESCRIPTION,
        },
        "recipe": {
            "id": LOCAL_RECIPE_ID,
            "version": LOCAL_RECIPE_VERSION,
            "qualification": "fixture_only",
        },
        "assessment": {
            "impact": "One synthetic fixture metadata field",
            "risk": "low",
            "confidence_basis": "Deterministic HTML metadata parser",
            "customer_origin_read": False,
        },
        "tests": [
            "finding_evidence_bound",
            "target_field_scoped",
            "customer_origin_not_read",
            "external_write_disabled",
        ],
        "role_contributions": [
            {
                "role": "technical_seo",
                "release": LOCAL_ROLE_RELEASE,
                "result": "finding_supported",
            },
            {
                "role": "content_strategy",
                "release": LOCAL_ROLE_RELEASE,
                "result": "metadata_draft_prepared",
            },
            {
                "role": "independent_reviewer",
                "release": LOCAL_ROLE_RELEASE,
                "result": "scope_checks_passed",
            },
            {
                "role": "coordinator",
                "release": LOCAL_ROLE_RELEASE,
                "result": "approval_requested",
            },
        ],
        "authority": {
            "approval_class": "A1",
            "requested": "accept_local_fixture_draft",
            "external_write": False,
        },
        "cost": {"currency": "USD", "maximum_minor_units": 0},
        "recovery": {
            "mode": "discard_local_draft",
            "external_state_changed": False,
            "summary": "Discard the draft; no external state has changed.",
        },
    }


def build_fixture_metadata_task(*, site_id: UUID, finding: AuditFinding) -> MetadataDraftTask:
    """Build the only model input allowed by the fixture proposal release."""
    if not isinstance(site_id, UUID) or not isinstance(finding, AuditFinding):
        raise ValueError("A validated site and finding are required.")
    if (
        finding.finding_key != "metadata.meta_description.missing"
        or finding.resource_locator != "/fixture/missing-meta-description"
        or finding.confidence_class != "deterministic"
    ):
        raise ProposalNotReady()
    return MetadataDraftTask(
        site_id=str(site_id),
        finding_id=str(finding.id),
        evidence_id=str(finding.evidence_id),
        command_id=str(finding.command_id),
        resource_locator=finding.resource_locator,
        observed_at=_utc_timestamp(finding.evidence_observed_at),
    )


def build_verified_homepage_metadata_task(
    *, site_id: UUID, finding: AuditFinding, observation: PageObservation
) -> MetadataDraftTask:
    """Build a bounded model packet from one exact verified-homepage finding."""
    if (
        not isinstance(site_id, UUID)
        or not isinstance(finding, AuditFinding)
        or not isinstance(observation, PageObservation)
        or finding.finding_key != "metadata.meta_description.missing"
        or finding.source_kind != "verified_origin"
        or finding.confidence_class != "deterministic"
        or observation.evidence_id != finding.evidence_id
        or observation.finding_id != finding.id
        or observation.command_id != finding.command_id
        or observation.manifest_id != finding.manifest_id
        or observation.final_url != finding.resource_locator
        or observation.meta_description is not None
        or (observation.title is None and observation.heading is None)
        or not _valid_https_resource(observation.final_url)
    ):
        raise ProposalNotReady()
    return MetadataDraftTask(
        site_id=str(site_id),
        finding_id=str(finding.id),
        evidence_id=str(finding.evidence_id),
        command_id=str(finding.command_id),
        resource_locator=finding.resource_locator,
        observed_at=_utc_timestamp(finding.evidence_observed_at),
        page_title=observation.title or "",
        page_heading=observation.heading or "",
    )


def build_model_fixture_manifest(
    *, site_id: UUID, run: FixtureModelRun, draft: MetadataDraft
) -> dict[str, object]:
    """Seal one model draft with its evidence, release, checks, and authority."""
    if (
        not isinstance(site_id, UUID)
        or not isinstance(run, FixtureModelRun)
        or not isinstance(draft, MetadataDraft)
        or run.outcome != "started"
    ):
        raise ValueError("A started model run and validated draft are required.")
    return {
        "schema_version": 1,
        "proposal_kind": MODEL_PROPOSAL_KIND,
        "site_id": str(site_id),
        "finding": {
            "id": str(run.finding_id),
            "evidence_id": str(run.evidence_id),
            "command_id": str(run.command_id),
            "resource_locator": run.resource_locator,
            "confidence_class": run.confidence_class,
            "observed_at": _utc_timestamp(run.evidence_observed_at),
        },
        "target": {
            "resource_locator": "/fixture/missing-meta-description",
            "field": "meta_description",
            "before": None,
            "after": draft.meta_description,
        },
        "recipe": {
            "id": LOCAL_RECIPE_ID,
            "version": MODEL_RECIPE_VERSION,
            "qualification": "fixture_only",
        },
        "assessment": {
            "impact": "One synthetic fixture metadata field",
            "risk": "low",
            "confidence_basis": "Model draft constrained by deterministic fixture evidence",
            "rationale": draft.rationale,
            "customer_origin_read": False,
        },
        "tests": [
            "finding_evidence_bound",
            "model_output_schema_valid",
            "target_field_scoped",
            "external_write_disabled",
        ],
        "role_contributions": [
            {
                "role": "technical_seo",
                "release": LOCAL_ROLE_RELEASE,
                "result": "finding_supported",
            },
            {
                "role": "content_strategy",
                "release": MODEL_RELEASE,
                "result": "metadata_draft_prepared",
            },
            {
                "role": "independent_reviewer",
                "release": LOCAL_ROLE_RELEASE,
                "result": "scope_checks_passed",
            },
            {
                "role": "coordinator",
                "release": LOCAL_ROLE_RELEASE,
                "result": "approval_requested",
            },
        ],
        "model": {
            "call_id": str(run.model_call_id),
            "release": MODEL_RELEASE,
            "model_requested": draft.model_requested,
            "model_reported": draft.model_reported,
            "provider_response_id": draft.provider_response_id,
            "prompt_sha256": MODEL_PROMPT_SHA256,
            "input_sha256": run.input_sha256,
            "output_sha256": draft.output_sha256,
            "store": False,
            "usage": {
                "input_tokens": draft.usage.input_tokens,
                "output_tokens": draft.usage.output_tokens,
                "cached_input_tokens": draft.usage.cached_input_tokens,
                "total_tokens": draft.usage.total_tokens,
            },
        },
        "authority": {
            "approval_class": "A1",
            "requested": "accept_model_fixture_draft",
            "external_write": False,
        },
        "cost": {"currency": "USD", "maximum_minor_units": 1},
        "recovery": {
            "mode": "discard_local_draft",
            "external_state_changed": False,
            "summary": "Discard the draft; no external state has changed.",
        },
    }


def build_verified_homepage_model_manifest(
    *, site_id: UUID, run: FixtureModelRun, draft: MetadataDraft
) -> dict[str, object]:
    """Seal one model draft against owner-verified homepage evidence."""
    if (
        not isinstance(site_id, UUID)
        or not isinstance(run, FixtureModelRun)
        or not isinstance(draft, MetadataDraft)
        or run.outcome != "started"
        or not _valid_https_resource(run.resource_locator)
    ):
        raise ValueError("A started verified-homepage run and validated draft are required.")
    return {
        "schema_version": 1,
        "proposal_kind": VERIFIED_MODEL_PROPOSAL_KIND,
        "site_id": str(site_id),
        "finding": {
            "id": str(run.finding_id),
            "evidence_id": str(run.evidence_id),
            "command_id": str(run.command_id),
            "resource_locator": run.resource_locator,
            "confidence_class": run.confidence_class,
            "observed_at": _utc_timestamp(run.evidence_observed_at),
        },
        "target": {
            "resource_locator": run.resource_locator,
            "field": "meta_description",
            "before": None,
            "after": draft.meta_description,
        },
        "recipe": {
            "id": LOCAL_RECIPE_ID,
            "version": VERIFIED_MODEL_RECIPE_VERSION,
            "qualification": "verified_homepage_proposal_only",
        },
        "assessment": {
            "impact": "One owner-verified homepage metadata field",
            "risk": "low",
            "confidence_basis": "Model draft constrained by verified homepage metadata",
            "rationale": draft.rationale,
            "customer_origin_read": True,
        },
        "tests": [
            "finding_evidence_bound",
            "model_output_schema_valid",
            "verified_homepage_target_scoped",
            "external_write_disabled",
        ],
        "role_contributions": [
            {
                "role": "technical_seo",
                "release": LOCAL_ROLE_RELEASE,
                "result": "finding_supported",
            },
            {
                "role": "content_strategy",
                "release": VERIFIED_MODEL_RELEASE,
                "result": "metadata_draft_prepared",
            },
            {
                "role": "independent_reviewer",
                "release": LOCAL_ROLE_RELEASE,
                "result": "scope_checks_passed",
            },
            {
                "role": "coordinator",
                "release": LOCAL_ROLE_RELEASE,
                "result": "approval_requested",
            },
        ],
        "model": {
            "call_id": str(run.model_call_id),
            "release": VERIFIED_MODEL_RELEASE,
            "model_requested": draft.model_requested,
            "model_reported": draft.model_reported,
            "provider_response_id": draft.provider_response_id,
            "prompt_sha256": VERIFIED_MODEL_PROMPT_SHA256,
            "input_sha256": run.input_sha256,
            "output_sha256": draft.output_sha256,
            "store": False,
            "usage": {
                "input_tokens": draft.usage.input_tokens,
                "output_tokens": draft.usage.output_tokens,
                "cached_input_tokens": draft.usage.cached_input_tokens,
                "total_tokens": draft.usage.total_tokens,
            },
        },
        "authority": {
            "approval_class": "A1",
            "requested": VERIFIED_MODEL_AUTHORITY,
            "external_write": False,
        },
        "cost": {"currency": "USD", "maximum_minor_units": 1},
        "recovery": {
            "mode": "discard_local_draft",
            "external_state_changed": False,
            "summary": VERIFIED_RECOVERY_SUMMARY,
        },
    }


def canonicalize_proposal_manifest(manifest: dict[str, object]) -> bytes:
    """Return RFC 8785 bytes for exact revision identity."""
    try:
        canonical = rfc8785.dumps(manifest)
    except (rfc8785.CanonicalizationError, TypeError):
        raise ValueError("The proposal manifest cannot be canonicalized.") from None
    if not canonical or len(canonical) > 32768:
        raise ValueError("The proposal manifest is outside the supported size.")
    return canonical


def prepare_authenticated_local_fixture_proposal(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
) -> LocalProposal:
    """Seal one deterministic local proposal and request exact human approval."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    authority = authorize_snapshot(
        connection,
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if authority.role_key != "owner":
        raise ApprovalPermissionDenied()
    findings = read_authenticated_findings(
        connection,
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if not findings:
        raise ProposalNotReady()
    manifest = build_local_fixture_manifest(site_id=requested_site_id, finding=findings[0])
    canonical = canonicalize_proposal_manifest(manifest)
    digest = hashlib.sha256(canonical).digest()
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.prepare_authenticated_local_fixture_proposal("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                uuid4(),
                uuid4(),
                uuid4(),
                Jsonb(manifest),
                canonical,
                digest,
            ),
        ).fetchone()
        return _proposal_result(row, expected="prepared")


def begin_authenticated_fixture_model_run(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
    finding: AuditFinding,
    task: MetadataDraftTask,
    model_requested: str = OPENAI_MODEL,
) -> FixtureModelRun:
    """Commit model-call intent before any provider request is sent."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if (
        not isinstance(finding, AuditFinding)
        or not isinstance(task, MetadataDraftTask)
        or task.site_id != str(requested_site_id)
        or task.finding_id != str(finding.id)
        or task.evidence_id != str(finding.evidence_id)
        or task.command_id != str(finding.command_id)
        or task.resource_locator != finding.resource_locator
        or task.observed_at != _utc_timestamp(finding.evidence_observed_at)
    ):
        raise ModelInputConflict()
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.begin_authenticated_fixture_model_run("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                uuid4(),
                uuid4(),
                finding.id,
                finding.evidence_id,
                finding.command_id,
                bytes.fromhex(MODEL_PROMPT_SHA256),
                bytes.fromhex(task.sha256),
                model_requested,
            ),
        ).fetchone()
        return _model_run_result(row, input_sha256=task.sha256)


def begin_authenticated_verified_homepage_model_run(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
    finding: AuditFinding,
    task: MetadataDraftTask,
    model_requested: str = OPENAI_MODEL,
) -> FixtureModelRun:
    """Commit a verified-homepage model-call intent before provider I/O."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if (
        not isinstance(finding, AuditFinding)
        or finding.source_kind != "verified_origin"
        or not isinstance(task, MetadataDraftTask)
        or task.site_id != str(requested_site_id)
        or task.finding_id != str(finding.id)
        or task.evidence_id != str(finding.evidence_id)
        or task.command_id != str(finding.command_id)
        or task.resource_locator != finding.resource_locator
        or task.observed_at != _utc_timestamp(finding.evidence_observed_at)
        or not _valid_https_resource(task.resource_locator)
    ):
        raise ModelInputConflict()
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.begin_authenticated_verified_homepage_model_run("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                uuid4(),
                uuid4(),
                finding.id,
                finding.evidence_id,
                finding.command_id,
                bytes.fromhex(VERIFIED_MODEL_PROMPT_SHA256),
                bytes.fromhex(task.sha256),
                model_requested,
            ),
        ).fetchone()
        return _model_run_result(row, input_sha256=task.sha256, verified_homepage=True)


def fail_authenticated_fixture_model_run(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
    run: FixtureModelRun,
    error_code: str,
    outcome_unknown: bool,
) -> None:
    """Close a requested model call with a sanitized known or unknown failure."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if (
        not isinstance(run, FixtureModelRun)
        or run.outcome != "started"
        or not isinstance(error_code, str)
        or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", error_code) is None
        or not isinstance(outcome_unknown, bool)
    ):
        raise ValueError("A valid requested model failure is required.")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT control.fail_authenticated_fixture_model_run(%s, %s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                run.agent_run_id,
                run.model_call_id,
                error_code,
                outcome_unknown,
            ),
        ).fetchone()
        outcome = row[0] if row is not None else None
        _raise_outcome(outcome)
        if outcome not in {"failed", "unknown"}:
            raise RuntimeError("Model failure recording failed.")


def fail_authenticated_verified_homepage_model_run(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
    run: FixtureModelRun,
    error_code: str,
    outcome_unknown: bool,
) -> None:
    """Close a verified-homepage model call with a sanitized outcome."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if (
        not isinstance(run, FixtureModelRun)
        or run.outcome != "started"
        or not _valid_https_resource(run.resource_locator)
        or not isinstance(error_code, str)
        or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", error_code) is None
        or not isinstance(outcome_unknown, bool)
    ):
        raise ValueError("A valid requested model failure is required.")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT control.fail_authenticated_verified_homepage_model_run("
            "%s, %s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                run.agent_run_id,
                run.model_call_id,
                error_code,
                outcome_unknown,
            ),
        ).fetchone()
        outcome = row[0] if row is not None else None
        _raise_outcome(outcome)
        if outcome not in {"failed", "unknown"}:
            raise RuntimeError("Model failure recording failed.")


def complete_authenticated_fixture_model_proposal(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
    run: FixtureModelRun,
    draft: MetadataDraft,
) -> LocalProposal:
    """Atomically seal validated model output and request exact local approval."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if not isinstance(run, FixtureModelRun) or not isinstance(draft, MetadataDraft):
        raise ValueError("A validated model run and draft are required.")
    output_canonical = draft.output_canonical
    if hashlib.sha256(output_canonical).hexdigest() != draft.output_sha256:
        raise ValueError("The model output identity is invalid.")
    manifest = build_model_fixture_manifest(site_id=requested_site_id, run=run, draft=draft)
    manifest_canonical = canonicalize_proposal_manifest(manifest)
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.complete_authenticated_fixture_model_proposal("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                run.agent_run_id,
                run.model_call_id,
                draft.provider_response_id,
                draft.model_reported,
                Jsonb(draft.output),
                output_canonical,
                bytes.fromhex(draft.output_sha256),
                draft.usage.input_tokens,
                draft.usage.output_tokens,
                draft.usage.cached_input_tokens,
                draft.usage.total_tokens,
                uuid4(),
                uuid4(),
                uuid4(),
                Jsonb(manifest),
                manifest_canonical,
                hashlib.sha256(manifest_canonical).digest(),
            ),
        ).fetchone()
        return _proposal_result(row, expected="prepared")


def complete_authenticated_verified_homepage_model_proposal(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
    run: FixtureModelRun,
    draft: MetadataDraft,
) -> LocalProposal:
    """Seal verified-homepage model output and request exact local approval."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if (
        not isinstance(run, FixtureModelRun)
        or not isinstance(draft, MetadataDraft)
        or not _valid_https_resource(run.resource_locator)
    ):
        raise ValueError("A validated verified-homepage run and draft are required.")
    output_canonical = draft.output_canonical
    if hashlib.sha256(output_canonical).hexdigest() != draft.output_sha256:
        raise ValueError("The model output identity is invalid.")
    manifest = build_verified_homepage_model_manifest(
        site_id=requested_site_id, run=run, draft=draft
    )
    manifest_canonical = canonicalize_proposal_manifest(manifest)
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.complete_authenticated_verified_homepage_model_proposal("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                run.agent_run_id,
                run.model_call_id,
                draft.provider_response_id,
                draft.model_reported,
                Jsonb(draft.output),
                output_canonical,
                bytes.fromhex(draft.output_sha256),
                draft.usage.input_tokens,
                draft.usage.output_tokens,
                draft.usage.cached_input_tokens,
                draft.usage.total_tokens,
                uuid4(),
                uuid4(),
                uuid4(),
                Jsonb(manifest),
                manifest_canonical,
                hashlib.sha256(manifest_canonical).digest(),
            ),
        ).fetchone()
        return _proposal_result(row, expected="prepared")


def read_authenticated_local_fixture_proposals(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
) -> tuple[LocalProposal, ...]:
    """Read at most fifty current-site local proposals after live reauthorization."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    with _clean_transaction(connection):
        rows = connection.execute(
            "SELECT * FROM control.read_authenticated_local_fixture_proposals(%s, %s, %s)",
            (token_hash, requested_site_id, current_recovery_generation),
        ).fetchall()
        if not rows:
            raise RuntimeError("Proposal read failed.")
        outcome = rows[0][17]
        _raise_outcome(outcome)
        if outcome == "not_found":
            return ()
        if any(row[17] != "found" for row in rows) or len(rows) > 50:
            raise RuntimeError("Proposal read failed.")
        return tuple(_proposal_from_row(row) for row in rows)


def decide_authenticated_local_fixture_approval(
    connection: Connection,
    *,
    session_token: object,
    requested_site_id: object,
    current_recovery_generation: object,
    approval_request_id: object,
    expected_revision_sha256: object,
    decision: object,
    decision_id: object,
) -> LocalProposal:
    """Commit one exact local decision without dispatching any external operation."""
    token_hash = validated_human_inputs(
        session_token=session_token,
        requested_site_id=requested_site_id,
        current_recovery_generation=current_recovery_generation,
    )
    if not isinstance(approval_request_id, UUID) or not isinstance(decision_id, UUID):
        raise ApprovalNotFound()
    if (
        not isinstance(expected_revision_sha256, str)
        or _SHA256.fullmatch(expected_revision_sha256) is None
    ):
        raise ApprovalRevisionConflict()
    if not isinstance(decision, str) or decision not in _DECISIONS:
        raise ValueError("The approval decision is invalid.")
    with _clean_transaction(connection):
        row = connection.execute(
            "SELECT * FROM control.decide_authenticated_local_fixture_approval("
            "%s, %s, %s, %s, %s, %s, %s)",
            (
                token_hash,
                requested_site_id,
                current_recovery_generation,
                approval_request_id,
                bytes.fromhex(expected_revision_sha256),
                decision_id,
                decision,
            ),
        ).fetchone()
        return _proposal_result(row, expected="decided")


def _proposal_result(row: object, *, expected: str) -> LocalProposal:
    if row is None:
        raise RuntimeError("Proposal operation failed.")
    try:
        outcome = row[17]
    except (IndexError, TypeError):
        raise RuntimeError("Proposal operation failed.") from None
    _raise_outcome(outcome)
    if outcome != expected:
        raise RuntimeError("Proposal operation failed.")
    return _proposal_from_row(row)


def _model_run_result(
    row: object, *, input_sha256: str, verified_homepage: bool = False
) -> FixtureModelRun:
    if row is None:
        raise RuntimeError("Model run admission failed.")
    try:
        outcome = row[12]
    except (IndexError, TypeError):
        raise RuntimeError("Model run admission failed.") from None
    _raise_outcome(outcome)
    if outcome not in {"started", "completed"}:
        raise RuntimeError("Model run admission failed.")
    try:
        result = FixtureModelRun(
            agent_run_id=row[0],
            model_call_id=row[1],
            attempt_number=row[2],
            finding_id=row[3],
            evidence_id=row[4],
            command_id=row[5],
            manifest_id=row[6],
            resource_locator=row[7],
            confidence_class=row[8],
            evidence_observed_at=row[9],
            input_sha256=input_sha256,
            status=row[10],
            reused=row[11],
            outcome=outcome,
        )
    except (IndexError, TypeError):
        raise RuntimeError("Model run projection is invalid.") from None
    if (
        not all(
            isinstance(identifier, UUID)
            for identifier in (
                result.agent_run_id,
                result.model_call_id,
                result.finding_id,
                result.evidence_id,
                result.command_id,
                result.manifest_id,
            )
        )
        or not isinstance(result.attempt_number, int)
        or isinstance(result.attempt_number, bool)
        or not 1 <= result.attempt_number <= 3
        or (
            not _valid_https_resource(result.resource_locator)
            if verified_homepage
            else result.resource_locator != "/fixture/missing-meta-description"
        )
        or result.confidence_class != "deterministic"
        or not _aware(result.evidence_observed_at)
        or _SHA256.fullmatch(result.input_sha256) is None
        or result.status not in {"requested", "completed"}
        or not isinstance(result.reused, bool)
        or (result.outcome == "started" and (result.status != "requested" or result.reused))
        or (result.outcome == "completed" and (result.status != "completed" or not result.reused))
    ):
        raise RuntimeError("Model run projection is invalid.")
    return result


def _raise_outcome(outcome: object) -> None:
    if outcome == "invalid_session":
        raise InvalidSession()
    if outcome == "authorization_denied":
        raise AuthorizationDenied()
    if outcome == "approval_permission_denied":
        raise ApprovalPermissionDenied()
    if outcome == "finding_not_ready":
        raise ProposalNotReady()
    if outcome == "manifest_conflict":
        raise ProposalManifestConflict()
    if outcome == "in_progress":
        raise ModelRunInProgress()
    if outcome == "outcome_unknown":
        raise ModelOutcomeUnknown()
    if outcome == "attempt_limit":
        raise ModelAttemptLimit()
    if outcome == "input_conflict":
        raise ModelInputConflict()
    if outcome == "run_conflict":
        raise ModelInputConflict()
    if outcome == "approval_not_found":
        raise ApprovalNotFound()
    if outcome == "approval_expired":
        raise ApprovalExpired()
    if outcome == "stale_revision":
        raise ApprovalRevisionConflict()
    if outcome == "decision_conflict":
        raise ApprovalDecisionConflict()


def _proposal_from_row(row: object) -> LocalProposal:
    try:
        value = LocalProposal(
            proposal_id=row[0],
            revision_id=row[1],
            revision_number=row[2],
            revision_sha256=row[3],
            manifest=row[4],
            created_by_user_id=row[5],
            created_at=row[6],
            approval_request_id=row[7],
            approval_status=row[8],
            approval_requested_at=row[9],
            approval_expires_at=row[10],
            decision_id=row[11],
            decision=row[12],
            decided_by_user_id=row[13],
            decision_channel=row[14],
            decided_at=row[15],
            reused=row[16],
        )
    except (IndexError, TypeError):
        raise RuntimeError("Proposal projection is invalid.") from None
    _validate_proposal(value)
    return value


def _validate_proposal(value: LocalProposal) -> None:
    manifest = value.manifest
    try:
        canonical = canonicalize_proposal_manifest(manifest)
        finding = manifest["finding"]
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Proposal projection is invalid.") from None
    valid = (
        all(
            isinstance(identifier, UUID)
            for identifier in (
                value.proposal_id,
                value.revision_id,
                value.created_by_user_id,
                value.approval_request_id,
            )
        )
        and isinstance(value.revision_number, int)
        and not isinstance(value.revision_number, bool)
        and 1 <= value.revision_number <= 10000
        and isinstance(value.revision_sha256, str)
        and _SHA256.fullmatch(value.revision_sha256) is not None
        and hashlib.sha256(canonical).hexdigest() == value.revision_sha256
        and _valid_manifest(manifest)
        and value.approval_status
        in {"pending", "expired", "approved", "rejected", "changes_requested"}
        and _aware(value.created_at)
        and _aware(value.approval_requested_at)
        and _aware(value.approval_expires_at)
        and value.approval_expires_at > value.approval_requested_at
        and isinstance(value.reused, bool)
    )
    decision_valid = (
        value.decision_id is None
        and value.decision is None
        and value.decided_by_user_id is None
        and value.decision_channel is None
        and value.decided_at is None
        and value.approval_status in {"pending", "expired"}
    ) or (
        isinstance(value.decision_id, UUID)
        and value.decision in _DECISIONS
        and value.approval_status == value.decision
        and isinstance(value.decided_by_user_id, UUID)
        and value.decision_channel == "dashboard"
        and _aware(value.decided_at)
    )
    if not valid or not decision_valid:
        raise RuntimeError("Proposal projection is invalid.")
    try:
        for key in ("id", "evidence_id", "command_id"):
            UUID(str(finding[key]))
        UUID(str(manifest["site_id"]))
        datetime.fromisoformat(str(finding["observed_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Proposal projection is invalid.") from None


def _valid_manifest(manifest: dict[str, object]) -> bool:
    common_keys = {
        "schema_version",
        "proposal_kind",
        "site_id",
        "finding",
        "target",
        "recipe",
        "assessment",
        "tests",
        "role_contributions",
        "authority",
        "cost",
        "recovery",
    }
    kind = manifest.get("proposal_kind")
    model_kinds = {MODEL_PROPOSAL_KIND, VERIFIED_MODEL_PROPOSAL_KIND}
    expected_keys = common_keys | ({"model"} if kind in model_kinds else set())
    try:
        finding = manifest["finding"]
        target = manifest["target"]
        common = (
            set(manifest) == expected_keys
            and manifest["schema_version"] == 1
            and kind in {LOCAL_PROPOSAL_KIND, *model_kinds}
            and isinstance(finding, dict)
            and set(finding)
            == {
                "id",
                "evidence_id",
                "command_id",
                "resource_locator",
                "confidence_class",
                "observed_at",
            }
            and (
                _valid_https_resource(finding["resource_locator"])
                if kind == VERIFIED_MODEL_PROPOSAL_KIND
                else finding["resource_locator"] == "/fixture/missing-meta-description"
            )
            and finding["confidence_class"] == "deterministic"
            and isinstance(target, dict)
            and set(target) == {"resource_locator", "field", "before", "after"}
            and target["resource_locator"] == finding["resource_locator"]
            and target["field"] == "meta_description"
            and target["before"] is None
            and manifest["recovery"]
            == {
                "mode": "discard_local_draft",
                "external_state_changed": False,
                "summary": (
                    VERIFIED_RECOVERY_SUMMARY
                    if kind == VERIFIED_MODEL_PROPOSAL_KIND
                    else "Discard the draft; no external state has changed."
                ),
            }
        )
    except (KeyError, TypeError):
        return False
    if not common:
        return False
    if kind == LOCAL_PROPOSAL_KIND:
        return _valid_deterministic_manifest(manifest)
    if kind == MODEL_PROPOSAL_KIND:
        return _valid_model_manifest(manifest)
    return _valid_verified_model_manifest(manifest)


def _valid_deterministic_manifest(manifest: dict[str, object]) -> bool:
    return (
        manifest["target"]
        == {
            "resource_locator": "/fixture/missing-meta-description",
            "field": "meta_description",
            "before": None,
            "after": LOCAL_PROPOSED_DESCRIPTION,
        }
        and manifest["recipe"]
        == {
            "id": LOCAL_RECIPE_ID,
            "version": LOCAL_RECIPE_VERSION,
            "qualification": "fixture_only",
        }
        and manifest["assessment"]
        == {
            "impact": "One synthetic fixture metadata field",
            "risk": "low",
            "confidence_basis": "Deterministic HTML metadata parser",
            "customer_origin_read": False,
        }
        and manifest["tests"]
        == [
            "finding_evidence_bound",
            "target_field_scoped",
            "customer_origin_not_read",
            "external_write_disabled",
        ]
        and manifest["role_contributions"] == _expected_roles(LOCAL_ROLE_RELEASE)
        and manifest["authority"]
        == {
            "approval_class": "A1",
            "requested": "accept_local_fixture_draft",
            "external_write": False,
        }
        and manifest["cost"] == {"currency": "USD", "maximum_minor_units": 0}
    )


def _valid_model_manifest(manifest: dict[str, object]) -> bool:
    target = manifest["target"]
    assessment = manifest["assessment"]
    model = manifest["model"]
    if (
        not isinstance(target, dict)
        or not isinstance(assessment, dict)
        or not isinstance(model, dict)
    ):
        return False
    description = target.get("after")
    rationale = assessment.get("rationale")
    model_reported = model.get("model_reported")
    release = model.get("release")
    prompt = (
        {
            MODEL_RELEASE: MODEL_PROMPT_SHA256,
            "local-gpt-5.6-luna-metadata-v1": _LEGACY_FIXTURE_PROMPT,
        }.get(release)
        if isinstance(release, str)
        else None
    )
    usage = model.get("usage")
    try:
        UUID(str(model.get("call_id")))
    except (TypeError, ValueError):
        return False
    return (
        isinstance(description, str)
        and 70 <= len(description) <= 160
        and description == description.strip()
        and "\x00" not in description
        and isinstance(rationale, str)
        and 1 <= len(rationale) <= 300
        and rationale == rationale.strip()
        and "\x00" not in rationale
        and manifest["recipe"]
        == {
            "id": LOCAL_RECIPE_ID,
            "version": MODEL_RECIPE_VERSION,
            "qualification": "fixture_only",
        }
        and set(assessment)
        == {"impact", "risk", "confidence_basis", "rationale", "customer_origin_read"}
        and assessment["impact"] == "One synthetic fixture metadata field"
        and assessment["risk"] == "low"
        and assessment["confidence_basis"]
        == "Model draft constrained by deterministic fixture evidence"
        and assessment["customer_origin_read"] is False
        and manifest["tests"]
        == [
            "finding_evidence_bound",
            "model_output_schema_valid",
            "target_field_scoped",
            "external_write_disabled",
        ]
        and prompt is not None
        and manifest["role_contributions"] == _expected_roles(release)
        and set(model)
        == {
            "call_id",
            "release",
            "model_requested",
            "model_reported",
            "provider_response_id",
            "prompt_sha256",
            "input_sha256",
            "output_sha256",
            "store",
            "usage",
        }
        and isinstance(model["model_requested"], str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model["model_requested"])
        and isinstance(model_reported, str)
        and (
            model_reported == model["model_requested"]
            or model_reported.startswith(model["model_requested"] + "-")
        )
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model_reported) is not None
        and isinstance(model["provider_response_id"], str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}", model["provider_response_id"])
        is not None
        and model["prompt_sha256"] == prompt
        and (release == MODEL_RELEASE or model["model_requested"] == "gpt-5.6-luna")
        and all(
            isinstance(model.get(key), str) and _SHA256.fullmatch(model[key]) is not None
            for key in ("input_sha256", "output_sha256")
        )
        and model["store"] is False
        and isinstance(usage, dict)
        and set(usage) == {"input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"}
        and all(
            isinstance(usage.get(key), int) and not isinstance(usage[key], bool) and usage[key] >= 0
            for key in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens")
        )
        and usage["cached_input_tokens"] <= usage["input_tokens"]
        and usage["total_tokens"] >= usage["input_tokens"] + usage["output_tokens"]
        and manifest["authority"]
        == {
            "approval_class": "A1",
            "requested": "accept_model_fixture_draft",
            "external_write": False,
        }
        and manifest["cost"] == {"currency": "USD", "maximum_minor_units": 1}
    )


def _valid_verified_model_manifest(manifest: dict[str, object]) -> bool:
    target = manifest["target"]
    assessment = manifest["assessment"]
    model = manifest["model"]
    if (
        not isinstance(target, dict)
        or not isinstance(assessment, dict)
        or not isinstance(model, dict)
    ):
        return False
    description = target.get("after")
    rationale = assessment.get("rationale")
    model_reported = model.get("model_reported")
    release = model.get("release")
    prompt = (
        {
            VERIFIED_MODEL_RELEASE: VERIFIED_MODEL_PROMPT_SHA256,
            "verified-gpt-5.6-luna-metadata-v1": _LEGACY_VERIFIED_PROMPT,
        }.get(release)
        if isinstance(release, str)
        else None
    )
    usage = model.get("usage")
    try:
        UUID(str(model.get("call_id")))
    except (TypeError, ValueError):
        return False
    return (
        isinstance(description, str)
        and 70 <= len(description) <= 160
        and description == description.strip()
        and "\x00" not in description
        and isinstance(rationale, str)
        and 1 <= len(rationale) <= 300
        and rationale == rationale.strip()
        and "\x00" not in rationale
        and manifest["recipe"]
        == {
            "id": LOCAL_RECIPE_ID,
            "version": VERIFIED_MODEL_RECIPE_VERSION,
            "qualification": "verified_homepage_proposal_only",
        }
        and set(assessment)
        == {"impact", "risk", "confidence_basis", "rationale", "customer_origin_read"}
        and assessment["impact"] == "One owner-verified homepage metadata field"
        and assessment["risk"] == "low"
        and assessment["confidence_basis"]
        == "Model draft constrained by verified homepage metadata"
        and assessment["customer_origin_read"] is True
        and manifest["tests"]
        == [
            "finding_evidence_bound",
            "model_output_schema_valid",
            "verified_homepage_target_scoped",
            "external_write_disabled",
        ]
        and prompt is not None
        and manifest["role_contributions"] == _expected_roles(release)
        and set(model)
        == {
            "call_id",
            "release",
            "model_requested",
            "model_reported",
            "provider_response_id",
            "prompt_sha256",
            "input_sha256",
            "output_sha256",
            "store",
            "usage",
        }
        and isinstance(model["model_requested"], str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model["model_requested"])
        and isinstance(model_reported, str)
        and (
            model_reported == model["model_requested"]
            or model_reported.startswith(model["model_requested"] + "-")
        )
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model_reported) is not None
        and isinstance(model["provider_response_id"], str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}", model["provider_response_id"])
        is not None
        and model["prompt_sha256"] == prompt
        and (release == VERIFIED_MODEL_RELEASE or model["model_requested"] == "gpt-5.6-luna")
        and all(
            isinstance(model.get(key), str) and _SHA256.fullmatch(model[key]) is not None
            for key in ("input_sha256", "output_sha256")
        )
        and model["store"] is False
        and isinstance(usage, dict)
        and set(usage) == {"input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"}
        and all(
            isinstance(usage.get(key), int) and not isinstance(usage[key], bool) and usage[key] >= 0
            for key in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens")
        )
        and usage["cached_input_tokens"] <= usage["input_tokens"]
        and usage["total_tokens"] >= usage["input_tokens"] + usage["output_tokens"]
        and manifest["authority"]
        == {
            "approval_class": "A1",
            "requested": VERIFIED_MODEL_AUTHORITY,
            "external_write": False,
        }
        and manifest["cost"] == {"currency": "USD", "maximum_minor_units": 1}
    )


def _expected_roles(content_release: str) -> list[dict[str, str]]:
    return [
        {
            "role": "technical_seo",
            "release": LOCAL_ROLE_RELEASE,
            "result": "finding_supported",
        },
        {
            "role": "content_strategy",
            "release": content_release,
            "result": "metadata_draft_prepared",
        },
        {
            "role": "independent_reviewer",
            "release": LOCAL_ROLE_RELEASE,
            "result": "scope_checks_passed",
        },
        {
            "role": "coordinator",
            "release": LOCAL_ROLE_RELEASE,
            "result": "approval_requested",
        },
    ]


def _utc_timestamp(value: datetime) -> str:
    if not _aware(value):
        raise ValueError("An aware evidence timestamp is required.")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _valid_https_resource(value: object) -> bool:
    if not isinstance(value, str) or not 9 <= len(value) <= 2048 or "\x00" in value:
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and parsed.fragment == ""
        )
    except ValueError:
        return False
