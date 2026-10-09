"""Function-only PostgreSQL persistence for immutable decision recommendations."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.database import Scope, scoped_transaction
from signal_core.decision_contracts import DecisionRecommendation, DecisionRequest


class DecisionRecordConflict(Exception):
    """The decision id is already bound to different immutable facts."""


class DecisionScopeUnavailable(Exception):
    """The trusted tenant/site scope is no longer active."""


@dataclass(frozen=True)
class RecordedDecision:
    decision_id: UUID
    created_at: datetime
    duplicate: bool


@dataclass(frozen=True, repr=False)
class PostgresDecisionRecorder:
    connection: Connection = field(repr=False)
    scope: Scope

    def __post_init__(self) -> None:
        if not isinstance(self.scope, Scope):
            raise ValueError("Decision recording requires a trusted database scope.")

    def record(
        self,
        request: DecisionRequest,
        recommendation: DecisionRecommendation,
    ) -> RecordedDecision:
        if not isinstance(request, DecisionRequest) or not isinstance(
            recommendation, DecisionRecommendation
        ):
            raise ValueError("Decision recording requires validated contracts.")
        if request.decision_id != recommendation.decision_id:
            raise ValueError("Decision recommendation identity does not match its request.")
        with scoped_transaction(self.connection, self.scope):
            row = self.connection.execute(
                "SELECT decision_id, created_at, duplicate, record_outcome "
                "FROM control.record_decision_recommendation(" + ", ".join(["%s"] * 17) + ")",
                (
                    self.scope.tenant_id,
                    self.scope.site_id,
                    request.decision_id,
                    request.purpose,
                    recommendation.provider,
                    recommendation.model_requested,
                    recommendation.model_reported,
                    request.input_sha256,
                    request.question_schema_sha256,
                    Jsonb(dict(recommendation.answers)),
                    Jsonb(dict(recommendation.probabilities)),
                    Decimal(str(recommendation.confidence)),
                    Decimal(str(recommendation.threshold)),
                    recommendation.policy_ceiling.value,
                    recommendation.recommendation.value,
                    recommendation.fallback,
                    recommendation.fallback_reason,
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("Decision recording returned no outcome.")
        if row[3] == "record_conflict":
            raise DecisionRecordConflict()
        if row[3] == "scope_unavailable":
            raise DecisionScopeUnavailable()
        if row[3] != "recorded":
            raise RuntimeError("Decision recording returned an invalid outcome.")
        return RecordedDecision(row[0], row[1], row[2])
