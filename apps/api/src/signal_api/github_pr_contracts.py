"""Strict read-only delivery evidence contract for the Changes view."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from signal_api.contracts import Contract


class GitHubPrAuthorityResponse(Contract):
    kind: Literal["owner_inbox", "standing_grant", "owner_editorial"]
    record_id: UUID
    owner_user_id: UUID
    decision_channel: Literal["dashboard", "slack", "telegram"] | None

    @model_validator(mode="after")
    def channel_matches_authority(self):
        if (self.kind != "standing_grant") != (self.decision_channel is not None):
            raise ValueError("The decision channel must match the authorization kind.")
        if self.kind == "owner_editorial" and self.decision_channel != "dashboard":
            raise ValueError("Editorial authority requires dashboard approval.")
        return self


class GitHubPrOperationResponse(Contract):
    schema_version: Literal[2] = 2
    authority: GitHubPrAuthorityResponse | None = None
    operation_id: UUID
    revision_id: UUID
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch_name: str = Field(pattern=r"^signal/[0-9a-f]{32}$")
    state: Literal["planned", "dispatching", "outcome_unknown", "ready", "opened", "blocked"]
    step: Literal["tree", "commit", "branch", "pr", "done"]
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    expected_tree_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    expected_commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    pr_number: int | None = Field(default=None, gt=0)
    pr_url: str | None = Field(default=None, pattern=r"^https://github[.]com/.+/pull/[1-9][0-9]*$")
    created_at: datetime
    updated_at: datetime
    journal_generation: UUID | None
    journal_position: int | None = Field(default=None, gt=0)
    journal_body_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class GitHubPrOperationsResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    operations: tuple[GitHubPrOperationResponse, ...] = Field(max_length=50)
    correlation_id: str = Field(min_length=1, max_length=64)
