"""Strict HTTP contracts for sealed technical-recipe Inbox revisions."""

import re
from datetime import datetime
from html import escape
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from signal_core.structured_data_recipe import site_url, static_page_url
from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

from signal_api.contracts import Contract


class CandidatePatch(Contract):
    offset: int = Field(ge=0, le=262144)
    before: str = Field(max_length=4096)
    after: str = Field(max_length=4096)


class CandidateEvidence(Contract):
    manifest_id: UUID
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_id: UUID
    page_url: str = Field(min_length=1, max_length=2048)
    finding: dict[str, object]
    site_origin: str | None = Field(default=None, max_length=2048)


class IndexNowCandidateEvidence(Contract):
    key_id: UUID
    key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    site_origin: str = Field(min_length=1, max_length=2048)
    page_url: str = Field(min_length=1, max_length=2048)
    finding: dict[str, object]


class CandidateArtifact(Contract):
    path: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=50_000_000)


class CandidateBuildReceipt(Contract):
    toolchain: str = Field(min_length=1, max_length=512)
    command: str = Field(min_length=1, max_length=512)
    exit_class: Literal["passed"]
    logs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: tuple[CandidateArtifact, ...] = Field(min_length=1, max_length=1000)


class StructuredDataPacket(Contract):
    recipe_key: Literal["structured_data_grounded"]
    json_ld: dict[str, object]
    fact_refs: dict[str, UUID]
    owner_required: bool
    autonomy_eligible: Literal[False]
    output_path: str = Field(max_length=2048)
    baseline_build_id: UUID


class InternalLinkPacket(Contract):
    recipe_key: Literal["technical_internal_link_add"]
    page_cap: int = Field(ge=1, le=3)
    autonomy_eligible: Literal[False]
    baseline_build_id: UUID
    output_path: str = Field(max_length=2048)
    target_id: UUID
    target_url: str = Field(max_length=2048)
    graph: dict[str, object]
    coverage: Literal["complete", "partial"]


class CandidateRecipeManifest(Contract):
    schema_version: Literal[1]
    site_id: UUID
    extension_id: UUID
    build_id: UUID
    audit_report_id: UUID | None
    finding_id: UUID
    recipe_release_id: UUID
    release_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str = Field(min_length=1, max_length=2048)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch: CandidatePatch
    evidence: CandidateEvidence | IndexNowCandidateEvidence
    build_receipt: CandidateBuildReceipt
    expected_impact: str = Field(min_length=1, max_length=500)
    recovery_plan: str = Field(min_length=1, max_length=500)
    approval_class: Literal["owner_review"]
    claim_review_required: bool
    model_draft: dict[str, object] | None
    structured_data: StructuredDataPacket | None = None
    internal_link: InternalLinkPacket | None = None

    @model_validator(mode="after")
    def evidence_kind(self):
        if isinstance(self.evidence, IndexNowCandidateEvidence):
            if (
                self.audit_report_id is not None
                or self.finding_id != self.evidence.key_id
                or self.evidence.finding.get("key") != "indexnow.key.required"
                or self.patch.before != ""
                or self.patch.offset != 0
                or self.source_path != self.patch.after + ".txt"
                or self.result_sha256 != self.evidence.key_sha256
                or self.claim_review_required
                or self.model_draft is not None
            ):
                raise ValueError("Invalid IndexNow key revision.")
        elif self.internal_link is not None:
            try:
                site_url(self.internal_link.target_url, self.evidence.site_origin)
                if (
                    static_page_url(self.source_path, self.evidence.site_origin)
                    != self.evidence.page_url
                ):
                    raise ValueError("Invalid source URL.")
            except (TechnicalRecipeUnavailable, TypeError):
                raise ValueError("Invalid contextual link URL.") from None
            if (
                self.audit_report_id is not None
                or self.evidence.finding.get("key") != "links.internal.add"
                or self.claim_review_required
                or self.model_draft is not None
                or self.internal_link.output_path != "_site/" + self.source_path
                or self.structured_data is not None
                or not re.fullmatch(r"[A-Za-z0-9-]+(?: [A-Za-z0-9-]+){1,2}", self.patch.before)
                or self.patch.after
                != '<a href="'
                + escape(self.internal_link.target_url, quote=True)
                + '">'
                + self.patch.before
                + "</a>"
            ):
                raise ValueError("Invalid contextual link revision.")
        elif self.audit_report_id is None:
            raise ValueError("Crawl recipes require an audit report.")
        return self


class AstroSample(Contract):
    path: str = Field(min_length=1, max_length=2048)
    before: str = Field(max_length=4096)
    after: str = Field(max_length=4096)


class AstroBuiltImpact(Contract):
    baseline_build_id: UUID
    lockfile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1, le=128)
    pages: tuple[str, ...] = Field(min_length=1, max_length=128)
    samples: tuple[AstroSample, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def exact_scope(self):
        if len(self.pages) != self.page_count or tuple(sorted(set(self.pages))) != self.pages:
            raise ValueError("Impact count must equal the exact unique sorted page set.")
        if any(sample.path not in self.pages for sample in self.samples):
            raise ValueError("Samples must belong to the sealed scope.")
        return self


class AstroEvidence(CandidateEvidence):
    site_origin: str = Field(min_length=1, max_length=2048)


class AstroRecipeManifest(CandidateRecipeManifest):
    framework: Literal["astro"]
    recipe_key: Literal["astro_title", "astro_description", "astro_alt", "astro_json_ld"]
    approval_class: Literal["A2", "A4"]
    autonomy_eligible: Literal[False]
    built_impact: AstroBuiltImpact
    evidence: AstroEvidence


class FrontMatterRecipeManifest(AstroRecipeManifest):
    content_adapter: Literal["front_matter"]
    framework: Literal["astro", "eleventy", "nextjs"]
    recipe_key: Literal["front_matter_title", "front_matter_description"]
    claim_review_required: Literal[True]
    model_draft: None


class NextjsRecipeManifest(AstroRecipeManifest):
    content_adapter: Literal["nextjs_metadata"]
    framework: Literal["nextjs"]
    recipe_key: Literal["nextjs_title", "nextjs_description"]
    claim_review_required: Literal[True]
    model_draft: None


class CandidateInboxItemResponse(Contract):
    schema_version: Literal[1] = 1
    revision_id: UUID
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest: (
        CandidateRecipeManifest
        | AstroRecipeManifest
        | FrontMatterRecipeManifest
        | NextjsRecipeManifest
    )
    sealed_at: datetime
    recipe_release_id: UUID
    release_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_status: Literal[
        "pending", "approved", "rejected", "changes_requested", "superseded", "stale_base"
    ]
    decision_id: UUID | None
    decision: Literal["approved", "rejected", "changes_requested"] | None
    decided_by_user_id: UUID | None
    decision_channel: Literal["dashboard", "slack", "telegram"] | None
    decided_at: datetime | None
    reused: bool


class CandidateInboxResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    revisions: tuple[CandidateInboxItemResponse, ...] = Field(max_length=50)
    correlation_id: str = Field(min_length=1, max_length=64)


class CandidateInboxDecisionRequest(Contract):
    schema_version: Literal[1]
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_id: UUID
    decision: Literal["approved", "rejected", "changes_requested"]


class CandidateInboxDecisionResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    revision: CandidateInboxItemResponse
    correlation_id: str = Field(min_length=1, max_length=64)
