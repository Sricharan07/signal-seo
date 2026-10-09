"""Exact immutable observation bytes, not a publishing authority."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

import rfc8785
from pydantic import Field, model_validator

from signal_api.contracts import Contract


class GitHubDeliveryObservationResponse(Contract):
    attempt_id: UUID
    operation_id: UUID
    state: Literal["dispatching", "completed", "outcome_unknown"]
    canonical_receipt: str | None = Field(max_length=65536)
    receipt_sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    next_observe_at: datetime

    @model_validator(mode="after")
    def exact_receipt(self):
        if self.state != "completed":
            if self.canonical_receipt is not None or self.receipt_sha256 is not None:
                raise ValueError("Unfinished observations have no receipt.")
            return self
        if self.canonical_receipt is None:
            raise ValueError("Completed observations require exact evidence.")
        body = self.canonical_receipt.encode("utf-8")
        document = json.loads(body)
        if (
            not isinstance(document, dict)
            or set(document)
            != {
                "schema_version",
                "site_id",
                "attempt_id",
                "operation_id",
                "revision_sha256",
                "environment",
                "deployment_actor_id",
                "provider",
                "provider_evidence",
                "live",
                "live_egress_operation_id",
                "outcome",
                "reason",
                "recovery_plan",
                "delivery_certified",
            }
            or document.get("schema_version") != 1
            or document.get("outcome")
            not in ("verified", "not_yet_deployed", "inconclusive", "regressed")
            or not isinstance(document.get("provider"), dict)
            or document["provider"].get("stage")
            not in ("pr_opened", "checks", "merged", "deployed")
        ):
            raise ValueError("Receipt schema mismatch.")
        if (
            len(body) > 65536
            or rfc8785.dumps(document) != body
            or hashlib.sha256(body).hexdigest() != self.receipt_sha256
        ):
            raise ValueError("Receipt identity mismatch.")
        if (
            document.get("attempt_id") != str(self.attempt_id)
            or document.get("operation_id") != str(self.operation_id)
            or document.get("delivery_certified") is not False
        ):
            raise ValueError("Receipt scope mismatch.")
        return self


class GitHubDeliveryObservationsResponse(Contract):
    schema_version: Literal[1] = 1
    site_id: UUID
    observations: tuple[GitHubDeliveryObservationResponse, ...] = Field(max_length=50)
    correlation_id: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def site_scope(self):
        operations = set()
        for observation in self.observations:
            if observation.operation_id in operations:
                raise ValueError("Duplicate operation observation.")
            operations.add(observation.operation_id)
            if observation.canonical_receipt is not None:
                if json.loads(observation.canonical_receipt)["site_id"] != str(self.site_id):
                    raise ValueError("Receipt site mismatch.")
        return self
