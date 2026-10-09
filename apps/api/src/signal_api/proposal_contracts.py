"""Versioned HTTP contracts for fixture-only supervised proposals."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from signal_api.contracts import Contract


class LocalProposalRequest(Contract):
    schema_version: Literal[1]


class ProposalFindingReference(Contract):
    id: UUID
    evidence_id: UUID
    command_id: UUID
    resource_locator: Literal["/fixture/missing-meta-description"]
    confidence_class: Literal["deterministic"]
    observed_at: datetime


class VerifiedProposalFindingReference(Contract):
    id: UUID
    evidence_id: UUID
    command_id: UUID
    resource_locator: str = Field(min_length=9, max_length=2048, pattern=r"^https://[^#\s]+$")
    confidence_class: Literal["deterministic"]
    observed_at: datetime


class DeterministicProposalTarget(Contract):
    resource_locator: Literal["/fixture/missing-meta-description"]
    field: Literal["meta_description"]
    before: None
    after: Literal["Explore the Signal test fixture and its durable SEO evidence."]


class DeterministicProposalRecipe(Contract):
    id: Literal["title_description_improvement"]
    version: Literal["local-fixture-0.1.0"]
    qualification: Literal["fixture_only"]


class DeterministicProposalAssessment(Contract):
    impact: Literal["One synthetic fixture metadata field"]
    risk: Literal["low"]
    confidence_basis: Literal["Deterministic HTML metadata parser"]
    customer_origin_read: Literal[False]


class DeterministicProposalRoleContribution(Contract):
    role: Literal["technical_seo", "content_strategy", "independent_reviewer", "coordinator"]
    release: Literal["local-deterministic-v1"]
    result: Literal[
        "finding_supported",
        "metadata_draft_prepared",
        "scope_checks_passed",
        "approval_requested",
    ]


class DeterministicProposalAuthority(Contract):
    approval_class: Literal["A1"]
    requested: Literal["accept_local_fixture_draft"]
    external_write: Literal[False]


class DeterministicProposalCost(Contract):
    currency: Literal["USD"]
    maximum_minor_units: Literal[0]


class ProposalRecovery(Contract):
    mode: Literal["discard_local_draft"]
    external_state_changed: Literal[False]
    summary: Literal["Discard the draft; no external state has changed."]


class DeterministicProposalManifest(Contract):
    schema_version: Literal[1]
    proposal_kind: Literal["local_fixture_metadata_draft"]
    site_id: UUID
    finding: ProposalFindingReference
    target: DeterministicProposalTarget
    recipe: DeterministicProposalRecipe
    assessment: DeterministicProposalAssessment
    tests: tuple[
        Literal["finding_evidence_bound"],
        Literal["target_field_scoped"],
        Literal["customer_origin_not_read"],
        Literal["external_write_disabled"],
    ]
    role_contributions: tuple[DeterministicProposalRoleContribution, ...] = Field(
        min_length=4, max_length=4
    )
    authority: DeterministicProposalAuthority
    cost: DeterministicProposalCost
    recovery: ProposalRecovery


class ModelProposalTarget(Contract):
    resource_locator: Literal["/fixture/missing-meta-description"]
    field: Literal["meta_description"]
    before: None
    after: str = Field(min_length=70, max_length=160)


class ModelProposalRecipe(Contract):
    id: Literal["title_description_improvement"]
    version: Literal["local-model-0.1.0"]
    qualification: Literal["fixture_only"]


class ModelProposalAssessment(Contract):
    impact: Literal["One synthetic fixture metadata field"]
    risk: Literal["low"]
    confidence_basis: Literal["Model draft constrained by deterministic fixture evidence"]
    rationale: str = Field(min_length=1, max_length=300)
    customer_origin_read: Literal[False]


class ModelProposalRoleContribution(Contract):
    role: Literal["technical_seo", "content_strategy", "independent_reviewer", "coordinator"]
    release: Literal[
        "local-deterministic-v1",
        "local-gpt-6-luna-metadata-v2",
        "verified-gpt-6-luna-metadata-v2",
        "local-gpt-5.6-luna-metadata-v1",
        "verified-gpt-5.6-luna-metadata-v1",
    ]
    result: Literal[
        "finding_supported",
        "metadata_draft_prepared",
        "scope_checks_passed",
        "approval_requested",
    ]


class ModelProposalUsage(Contract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class ModelProposalRecord(Contract):
    call_id: UUID
    release: Literal["local-gpt-6-luna-metadata-v2", "local-gpt-5.6-luna-metadata-v1"]
    model_requested: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    model_reported: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    provider_response_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    store: Literal[False]
    usage: ModelProposalUsage


class ModelProposalAuthority(Contract):
    approval_class: Literal["A1"]
    requested: Literal["accept_model_fixture_draft"]
    external_write: Literal[False]


class ModelProposalCost(Contract):
    currency: Literal["USD"]
    maximum_minor_units: Literal[1]


class ModelProposalManifest(Contract):
    schema_version: Literal[1]
    proposal_kind: Literal["model_fixture_metadata_draft"]
    site_id: UUID
    finding: ProposalFindingReference
    target: ModelProposalTarget
    recipe: ModelProposalRecipe
    assessment: ModelProposalAssessment
    tests: tuple[
        Literal["finding_evidence_bound"],
        Literal["model_output_schema_valid"],
        Literal["target_field_scoped"],
        Literal["external_write_disabled"],
    ]
    role_contributions: tuple[ModelProposalRoleContribution, ...] = Field(
        min_length=4, max_length=4
    )
    model: ModelProposalRecord
    authority: ModelProposalAuthority
    cost: ModelProposalCost
    recovery: ProposalRecovery


class VerifiedModelProposalTarget(Contract):
    resource_locator: str = Field(min_length=9, max_length=2048, pattern=r"^https://[^#\s]+$")
    field: Literal["meta_description"]
    before: None
    after: str = Field(min_length=70, max_length=160)


class VerifiedModelProposalRecipe(Contract):
    id: Literal["title_description_improvement"]
    version: Literal["verified-homepage-model-1.0.0"]
    qualification: Literal["verified_homepage_proposal_only"]


class VerifiedModelProposalAssessment(Contract):
    impact: Literal["One owner-verified homepage metadata field"]
    risk: Literal["low"]
    confidence_basis: Literal["Model draft constrained by verified homepage metadata"]
    rationale: str = Field(min_length=1, max_length=300)
    customer_origin_read: Literal[True]


class VerifiedModelProposalRecord(ModelProposalRecord):
    release: Literal["verified-gpt-6-luna-metadata-v2", "verified-gpt-5.6-luna-metadata-v1"]
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class VerifiedModelProposalAuthority(Contract):
    approval_class: Literal["A1"]
    requested: Literal["accept_verified_homepage_metadata_draft"]
    external_write: Literal[False]


class VerifiedProposalRecovery(Contract):
    mode: Literal["discard_local_draft"]
    external_state_changed: Literal[False]
    summary: Literal["Discard the proposed draft; no external state has changed."]


class VerifiedModelProposalManifest(Contract):
    schema_version: Literal[1]
    proposal_kind: Literal["model_verified_homepage_metadata_draft"]
    site_id: UUID
    finding: VerifiedProposalFindingReference
    target: VerifiedModelProposalTarget
    recipe: VerifiedModelProposalRecipe
    assessment: VerifiedModelProposalAssessment
    tests: tuple[
        Literal["finding_evidence_bound"],
        Literal["model_output_schema_valid"],
        Literal["verified_homepage_target_scoped"],
        Literal["external_write_disabled"],
    ]
    role_contributions: tuple[ModelProposalRoleContribution, ...] = Field(
        min_length=4, max_length=4
    )
    model: VerifiedModelProposalRecord
    authority: VerifiedModelProposalAuthority
    cost: ModelProposalCost
    recovery: VerifiedProposalRecovery


ProposalManifest = Annotated[
    DeterministicProposalManifest | ModelProposalManifest | VerifiedModelProposalManifest,
    Field(discriminator="proposal_kind"),
]


class ProposalResponse(Contract):
    schema_version: Literal[1] = 1
    proposal_id: UUID
    revision_id: UUID
    revision_number: int = Field(ge=1, le=10000)
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest: ProposalManifest
    created_by_user_id: UUID
    created_at: datetime
    approval_request_id: UUID
    approval_status: Literal["pending", "expired", "approved", "rejected", "changes_requested"]
    approval_requested_at: datetime
    approval_expires_at: datetime
    decision_id: UUID | None
    decision: Literal["approved", "rejected", "changes_requested"] | None
    decided_by_user_id: UUID | None
    decision_channel: Literal["dashboard"] | None
    decided_at: datetime | None
    reused: bool


class LocalProposalResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    proposal: ProposalResponse
    correlation_id: str = Field(min_length=1, max_length=64)


class ProposalsResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    proposals: tuple[ProposalResponse, ...] = Field(max_length=50)
    correlation_id: str = Field(min_length=1, max_length=64)


class ApprovalDecisionRequest(Contract):
    schema_version: Literal[1]
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_id: UUID
    decision: Literal["approved", "rejected", "changes_requested"]


class ApprovalDecisionResponse(LocalProposalResponse):
    pass
