"""Closed Business Brain browser contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from signal_api.contracts import Contract

Category = Literal[
    "product",
    "pricing",
    "audience",
    "positioning",
    "proof_point",
    "competitor",
    "claim",
    "legal",
    "medical",
    "financial",
    "product_claim",
]
FactStatus = Literal["proposed", "approved", "superseded", "removed"]


class VoiceProfile(Contract):
    tone: str = Field(max_length=4000)
    audience: str = Field(max_length=4000)
    guidelines: str = Field(max_length=4000)


class VoiceUpdate(Contract):
    schema_version: Literal[1]
    profile: VoiceProfile
    supersedes_id: UUID | None


class VoiceResponse(Contract):
    profile_id: UUID
    profile: VoiceProfile
    supersedes_id: UUID | None
    created_at: datetime


class OwnerFactRequest(Contract):
    schema_version: Literal[1]
    category: Category
    statement: str = Field(min_length=1, max_length=4000)


class FactCorrection(Contract):
    schema_version: Literal[1]
    statement: str = Field(min_length=1, max_length=4000)


class FactMutation(Contract):
    schema_version: Literal[1]


class ExtractedRange(Contract):
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)


class FactResponse(Contract):
    fact_id: UUID
    category: Category
    statement: str = Field(min_length=1, max_length=4000)
    status: FactStatus
    source_kind: Literal["page_evidence", "brand_document", "owner_statement"]
    page_evidence_id: UUID | None
    document_id: UUID | None
    extracted_range: ExtractedRange | None
    owner_membership_id: UUID | None
    sensitive: bool
    supersedes_id: UUID | None
    created_at: datetime
    decision_id: UUID | None
    extraction_id: UUID | None
    provenance_url: str
    source_review_required: bool = False


class FactsResponse(Contract):
    schema_version: Literal[1] = 1
    facts: tuple[FactResponse, ...]


class BrainVoiceResponse(Contract):
    schema_version: Literal[1] = 1
    voice: VoiceResponse | None


class MutationResponse(Contract):
    schema_version: Literal[1] = 1
    outcome: Literal["approved", "corrected", "removed", "recorded", "proposed"]


class ExtractionReceipt(Contract):
    extraction_id: UUID
    source_kind: Literal["page_evidence", "brand_document"]
    source_id: UUID
    decision_id: UUID
    state: Literal["completed", "failed", "outcome_unknown"]
    page_type: Literal["product", "pricing", "blog", "docs", "legal", "other"] | None
    fallback: bool | None
    provider: Literal["typesafe", "openai_fallback", "deterministic_fallback"] | None
    reason: str | None


class ExtractionStatus(Contract):
    schema_version: Literal[1] = 1
    state: Literal["available", "unavailable"]
    reason: Literal["MODEL_UNCONFIGURED"] | None
    extractions: tuple[ExtractionReceipt, ...]


class ExtractionRequest(Contract):
    schema_version: Literal[1]
    source_kind: Literal["page_evidence", "brand_document"]
    source_id: UUID
    extracted_range: ExtractedRange | None


class ExtractionResult(Contract):
    schema_version: Literal[1] = 1
    state: Literal["unavailable", "completed", "failed", "outcome_unknown"]
    extraction_id: UUID | None
    decision_id: UUID | None
    reason: str | None
