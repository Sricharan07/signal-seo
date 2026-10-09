"""Strict owner-document browser contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from signal_api.contracts import Contract


class BrandDocumentUploadRequest(Contract):
    schema_version: Literal[1]
    filename: str = Field(min_length=1, max_length=120)
    content_base64: str = Field(min_length=4, max_length=2_796_204)
    supersedes_id: UUID | None


class BrandDocumentResponse(Contract):
    schema_version: Literal[1] = 1
    document_id: UUID
    display_name: str
    media_type: str
    created_at: datetime
    supersedes_id: UUID | None
    injection_signal: bool
    secret_signal: bool
    deleted: bool
    retained_for_evidence: bool


class BrandDocumentsResponse(Contract):
    schema_version: Literal[1] = 1
    documents: tuple[BrandDocumentResponse, ...]


class BrandDocumentTextResponse(Contract):
    schema_version: Literal[1] = 1
    document_id: UUID
    trust_label: Literal["owner_upload_untrusted_data"]
    text: str = Field(min_length=1, max_length=524_288)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    injection_signal: bool


class BrandDocumentDeletionResponse(Contract):
    schema_version: Literal[1] = 1
    outcome: Literal["deleted_retained"]
    retained_for_evidence: Literal[True] = True
