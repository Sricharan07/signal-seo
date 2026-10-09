"""Owner-only evidence projection; a reported PR is not delivered content."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, FiniteFloat, StringConstraints, model_validator

from signal_api.contracts import Contract


class WeeklyStageResponse(Contract):
    stage: Literal[
        "observe", "analyze", "plan", "prepare", "gate", "handoff", "verify", "measure", "report"
    ]
    outcome: Literal["completed", "unavailable", "deferred", "waiting_owner", "stopped", "failed"]
    detail_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    evidence_refs: tuple[str, ...] = Field(max_length=64)
    recorded_at: datetime


class WeeklySkillResponse(Contract):
    stage: Literal[
        "import_gsc",
        "import_bing",
        "import_ga4",
        "pagespeed_refresh",
        "visibility_reobserve",
        "brain_refresh",
        "strategy_rebuild",
        "internal_link_proposals",
        "brief_proposals",
        "report_delivery",
        "chat_report_delivery",
    ]
    outcome: Literal["completed", "unavailable", "failed"]
    detail_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    work_type: Literal["research_audit", "draft_patch"]
    budget_source: Literal["standing_authorization"]
    cap_source: Literal[
        "standing_authorization",
        "standing_authorization_and_content_writer",
        "standing_authorization_and_email",
        "standing_authorization_and_pagespeed",
        "standing_authorization_and_assistants",
        "standing_authorization_and_chat",
    ]
    reserved_cents: int = Field(ge=0, le=100000000)
    units: int = Field(ge=0, le=64)
    spend_status: Literal["upper_bound_reserved", "no_paid_io"]
    evidence_refs: tuple[
        Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9:/._#-]{1,512}$")], ...
    ] = Field(max_length=64)
    recorded_at: datetime | None

    @model_validator(mode="after")
    def exact_stage_authority(self):
        expected = (
            "draft_patch"
            if self.stage in {"brief_proposals", "internal_link_proposals"}
            else "research_audit"
        )
        cap = (
            "standing_authorization_and_content_writer"
            if self.stage == "brief_proposals"
            else "standing_authorization_and_email"
            if self.stage == "report_delivery"
            else "standing_authorization_and_pagespeed"
            if self.stage == "pagespeed_refresh"
            else "standing_authorization_and_assistants"
            if self.stage == "visibility_reobserve"
            else "standing_authorization_and_chat"
            if self.stage == "chat_report_delivery"
            else "standing_authorization"
        )
        if self.cap_source != cap:
            raise ValueError("Skill cap source differs.")
        if self.work_type != expected or (self.reserved_cents > 0) != (
            self.spend_status == "upper_bound_reserved"
        ):
            raise ValueError("Skill authority or budget evidence differs.")
        if self.reserved_cents and not self.units:
            raise ValueError("Budget has no admitted work units.")
        return self


class WeeklyGateResponse(Contract):
    decision_id: UUID
    outcome: Literal["ship", "ask_owner", "reject"]
    reason: str
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decided_at: datetime


class WeeklyHandoffResponse(Contract):
    decision_id: UUID
    operation_id: UUID
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: datetime


class WeeklyDeferralResponse(Contract):
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    next_week: date
    reason: str
    resolved_week: date | None


class WeeklyObservationResponse(Contract):
    command_id: UUID
    status: str
    result_reference: dict | None
    status_url: str


class WeeklyDeliveryResponse(Contract):
    workload_id: UUID
    finding_id: UUID
    recipe_release_id: UUID
    revision_id: UUID
    revision_sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: UUID | None
    operation_state: (
        Literal["planned", "dispatching", "outcome_unknown", "ready", "opened", "blocked"] | None
    )
    pr_url: str | None = Field(
        pattern=r"^https://github[.]com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*$"
    )
    authority_kind: Literal["owner_inbox", "standing_grant"] | None
    authority_id: UUID | None
    authorization_owner_id: UUID | None
    decision_channel: Literal["dashboard", "slack", "telegram"] | None
    review_status: Literal["approved", "rejected", "changes_requested"] | None
    observation_id: UUID | None
    observation_sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    delivery_outcome: Literal["verified", "not_yet_deployed", "inconclusive", "regressed"] | None
    delivery_reason: str | None
    delivery_stage: Literal["pr_opened", "checks", "merged", "deployed"] | None

    @model_validator(mode="after")
    def exact_evidence(self):
        if (
            (self.operation_id is None) != (self.authority_id is None)
            or ((self.operation_id is None) != (self.authority_kind is None))
            or (self.operation_id is None) != (self.authorization_owner_id is None)
        ):
            raise ValueError("Operation authority evidence is incomplete.")
        if (self.authority_kind == "owner_inbox") != (self.decision_channel is not None):
            raise ValueError("The exact owner decision channel is required.")
        if self.pr_url and self.operation_state != "opened":
            raise ValueError("An unopened operation cannot claim a PR.")
        if self.delivery_outcome == "verified" and (
            self.delivery_stage != "deployed" or self.observation_sha256 is None
        ):
            raise ValueError("Live verification requires deployed receipt evidence.")
        return self


class MeasurementMetrics(Contract):
    clicks: FiniteFloat | None
    impressions: FiniteFloat | None
    ctr: FiniteFloat | None
    position: FiniteFloat | None


class MeasurementSource(Contract):
    state: Literal["awaiting_data", "measured_as_reported", "unavailable", "failed"]
    reason: str
    generation_id: UUID | None
    coverage: dict | None
    metrics: MeasurementMetrics | None

    @model_validator(mode="after")
    def evidence_required(self):
        if self.state == "measured_as_reported" and (
            self.generation_id is None
            or self.coverage is None
            or self.metrics is None
            or self.coverage.get("complete") is not False
        ):
            raise ValueError("Provider-reported metrics require incomplete source evidence.")
        if self.state != "measured_as_reported" and self.metrics is not None:
            raise ValueError("Pending or unavailable sources cannot claim metrics.")
        return self


class MeasurementSources(Contract):
    gsc_page: MeasurementSource
    bing_site_context: MeasurementSource
    bing_page: MeasurementSource

    @model_validator(mode="after")
    def source_dimensions(self):
        if self.bing_page.metrics is not None and (
            self.bing_page.metrics.ctr is not None
            or self.bing_page.metrics.position is not None
            or self.bing_page.coverage.get("coverage") != "provider_returned_top_pages"
            or self.bing_page.coverage.get("date_granularity") != "unknown"
        ):
            raise ValueError("Bing page evidence must retain reported source semantics.")
        if self.bing_site_context.metrics is not None and (
            self.bing_site_context.metrics.ctr is not None
            or self.bing_site_context.metrics.position is not None
        ):
            raise ValueError("Bing site context provides clicks and impressions only.")
        return self


class MeasurementChanges(Contract):
    gsc_page: MeasurementMetrics
    bing_site_context: MeasurementMetrics
    bing_page: MeasurementMetrics | None = None


class NewPageMeasurementSource(MeasurementSource):
    state: Literal["unavailable"]
    reason: Literal["NO_PRE_CHANGE_WINDOW"]
    generation_id: None
    coverage: None
    metrics: None


class NewPageMeasurementBaseline(Contract):
    state: Literal["new_page"]
    reason: Literal["NO_PRE_CHANGE_WINDOW"]
    gsc_page: NewPageMeasurementSource
    bing_site_context: NewPageMeasurementSource
    bing_page: NewPageMeasurementSource


class MeasurementConfounder(Contract):
    operation_id: UUID
    verified_live_at: datetime
    page_url: str


class MeasurementObservation(Contract):
    state: Literal["not_yet_due", "awaiting_data", "measured_as_reported", "unavailable", "failed"]
    reason: str
    post: MeasurementSources | None
    observed_change: MeasurementChanges | None
    confounders: tuple[MeasurementConfounder, ...]


class ChangeMeasurementResponse(Contract):
    operation_id: UUID
    horizon: Literal[7, 28, 90]
    page_url: str
    verified_live_at: datetime
    due_at: datetime
    baseline_start: date | None
    baseline_end: date | None
    baseline: MeasurementSources | NewPageMeasurementBaseline
    post_start: date
    post_end: date
    evidence_url: str
    verification_attempt_id: UUID
    observation: MeasurementObservation
    recorded_at: datetime | None

    @model_validator(mode="after")
    def evidence_scope(self):
        if isinstance(self.baseline, NewPageMeasurementBaseline):
            if self.baseline_start is not None or self.baseline_end is not None:
                raise ValueError("A new page has no pre-change window.")
            if self.observation.observed_change is not None and any(
                value is not None
                for metrics in (
                    self.observation.observed_change.gsc_page,
                    self.observation.observed_change.bing_site_context,
                    *(
                        [self.observation.observed_change.bing_page]
                        if self.observation.observed_change.bing_page is not None
                        else []
                    ),
                )
                for value in metrics.model_dump().values()
            ):
                raise ValueError("A new page has no pre-change metrics for an observed change.")
        elif self.baseline_start is None or self.baseline_end is None:
            raise ValueError("An existing page requires a pre-change window.")
        if self.evidence_url != f"/changes#operation-{self.operation_id}":
            raise ValueError("Measurement evidence scope differs.")
        if self.observation.state == "measured_as_reported" and (
            self.observation.post is None
            or self.observation.post.gsc_page.state != "measured_as_reported"
        ):
            raise ValueError("Page observation lacks provider evidence.")
        return self


class NextWorkResponse(Contract):
    state: Literal["available", "unavailable"]
    source: Literal["weekly_backlog"]
    reason: str | None
    items: tuple[WeeklyDeferralResponse, ...]


class DecisionItemResponse(Contract):
    revision_id: UUID
    kind: Literal["technical", "editorial"]
    url: str

    @model_validator(mode="after")
    def exact_destination(self):
        expected = (
            f"/approvals?revision={self.revision_id}" if self.kind == "technical" else "/approvals"
        )
        if self.url != expected:
            raise ValueError("Decision destination differs.")
        return self


class NeedsDecisionResponse(Contract):
    count: int = Field(ge=0)
    items: tuple[DecisionItemResponse, ...]
    url: Literal["/approvals"]

    @model_validator(mode="after")
    def count_matches(self):
        if self.count != len(self.items):
            raise ValueError("Inbox count differs from evidence.")
        return self


class WeeklyReportResponse(Contract):
    schema_version: Literal[1, 2]
    cycle_id: UUID
    site_id: UUID
    week_start: date
    status: Literal["running", "completed", "stopped", "failed"]
    stop_reason: str | None
    started_at: datetime
    closed_at: datetime | None
    observation_command: WeeklyObservationResponse | None
    stages: tuple[WeeklyStageResponse, ...] = Field(max_length=9)
    skill_stages: tuple[WeeklySkillResponse, ...] = Field(default=(), max_length=11)
    gate_decisions: tuple[WeeklyGateResponse, ...] = Field(max_length=32)
    waiting_for_owner: tuple[UUID, ...] = Field(max_length=32)
    handoffs: tuple[WeeklyHandoffResponse, ...] = Field(max_length=32)
    deferred: tuple[WeeklyDeferralResponse, ...] = Field(max_length=32)
    delivery: tuple[WeeklyDeliveryResponse, ...] = Field(default=(), max_length=64)
    measurements: tuple[ChangeMeasurementResponse, ...] = ()
    what_is_next: NextWorkResponse | None = None
    needs_decision: NeedsDecisionResponse | None = None

    @model_validator(mode="after")
    def report_sections(self):
        if len({s.stage for s in self.skill_stages}) != len(self.skill_stages):
            raise ValueError("Duplicate skill stage evidence.")
        if self.schema_version == 2 and (self.what_is_next is None or self.needs_decision is None):
            raise ValueError("Weekly report sections are unavailable.")
        return self
