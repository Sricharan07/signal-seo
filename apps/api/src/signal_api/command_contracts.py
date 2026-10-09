"""Versioned HTTP contracts for harmless durable human commands."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from signal_api.contracts import Contract


class SnapshotCommandRequest(Contract):
    schema_version: Literal[1]


class SnapshotCommandAcceptedResponse(Contract):
    schema_version: Literal[1] = 1
    command_id: UUID
    site_id: UUID
    status: Literal["accepted"]
    status_url: str = Field(min_length=1, max_length=256, pattern=r"^/v1/sites/")
    reused: bool
    accepted_at: datetime
    correlation_id: str = Field(min_length=1, max_length=64)


class CrawlManifestReferenceResponse(Contract):
    schema_version: Literal[1]
    manifest_id: UUID
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage: Literal["complete", "partial"]
    discovered_count: int = Field(ge=0, le=1000000)
    terminal_count: int = Field(ge=0, le=1000000)
    scope_version: int = Field(ge=1, le=2147483647)
    crawl_policy_version: int = Field(ge=1, le=2147483647)

    @model_validator(mode="after")
    def terminal_count_is_bounded(self) -> "CrawlManifestReferenceResponse":
        if self.terminal_count > self.discovered_count:
            raise ValueError("Terminal URL count cannot exceed discovered URLs.")
        return self


class SnapshotCommandStatusResponse(Contract):
    schema_version: Literal[1] = 1
    command_id: UUID
    site_id: UUID
    actor_user_id: UUID
    kind: Literal["site.snapshot"]
    status: Literal[
        "accepted", "workflow_admitted", "processing", "succeeded", "failed", "cancelled"
    ]
    accepted_at: datetime
    workflow_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=(
            r"^signal:CrawlSite:"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        ),
    )
    workflow_type: Literal["CrawlSite"] | None = None
    first_run_id: str | None = Field(
        default=None,
        min_length=36,
        max_length=36,
        pattern=(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
    )
    workflow_state: Literal["admitted", "running", "succeeded", "failed", "cancelled"] | None = None
    projected_at: datetime | None = None
    result_reference: CrawlManifestReferenceResponse | None = None
    terminal_reason: Literal["crawl_activity_failed", "crawl_cancelled"] | None = None
    correlation_id: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def progress_is_coherent(self) -> "SnapshotCommandStatusResponse":
        progress = (
            self.workflow_id,
            self.workflow_type,
            self.first_run_id,
            self.workflow_state,
            self.projected_at,
        )
        if self.status == "accepted" and progress != (None, None, None, None, None):
            raise ValueError("Accepted commands cannot contain workflow progress.")
        if self.status in {"accepted", "workflow_admitted", "processing"} and (
            self.result_reference is not None or self.terminal_reason is not None
        ):
            raise ValueError("Nonterminal commands cannot contain terminal evidence.")
        if self.status == "workflow_admitted":
            if self.first_run_id is not None or any(
                value is None
                for value in (
                    self.workflow_id,
                    self.workflow_type,
                    self.workflow_state,
                    self.projected_at,
                )
            ):
                raise ValueError("Workflow admission requires complete progress evidence.")
            if self.workflow_state != "admitted":
                raise ValueError("Workflow admission requires an admitted workflow.")
            if not self.workflow_id.endswith(f":{self.command_id}"):
                raise ValueError("Workflow admission does not match the command.")
        if self.status == "processing":
            if any(value is None for value in progress):
                raise ValueError("Processing requires complete workflow start evidence.")
            if self.workflow_state != "running":
                raise ValueError("Processing requires a running workflow projection.")
            if not self.workflow_id.endswith(f":{self.command_id}"):
                raise ValueError("Workflow start does not match the command.")
        if self.status in {"succeeded", "failed", "cancelled"}:
            if any(value is None for value in progress):
                raise ValueError("Terminal commands require complete workflow evidence.")
            if self.workflow_state != self.status:
                raise ValueError("Terminal workflow state must match command status.")
            if not self.workflow_id.endswith(f":{self.command_id}"):
                raise ValueError("Terminal workflow does not match the command.")
            if self.status == "succeeded" and (
                self.result_reference is None or self.terminal_reason is not None
            ):
                raise ValueError("Successful crawl requires one result reference.")
            if self.status == "failed" and (
                self.result_reference is not None or self.terminal_reason != "crawl_activity_failed"
            ):
                raise ValueError("Failed crawl requires its closed reason.")
            if self.status == "cancelled" and (
                self.result_reference is not None or self.terminal_reason != "crawl_cancelled"
            ):
                raise ValueError("Cancelled crawl requires its closed reason.")
        return self
