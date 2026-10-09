"""Legacy qualification ports, not composed product capabilities."""

import asyncio
import hashlib
import json
import ssl
from dataclasses import dataclass, field

import httpx2
from signal_core.decision_contracts import DecisionRequest, canonical_json
from signal_core.egress_profiles import EgressProfile
from signal_core.jev_decisions import (
    _MAX_RESPONSE_BYTES,
    FALLBACK_ENDPOINT,
    FALLBACK_MODEL,
    DecisionCredential,
    DecisionEgress,
    FallbackClassification,
    FallbackUnavailable,
    _derived_operation_id,
    _fallback_payload,
    _parse_fallback_response,
)
from signal_core.model_budget import ModelBudgetUnavailable
from signal_core.model_credentials import JevCredentialError, ModelCredentialError
from signal_core.model_roles import ModelRoles
from signal_core.shared_egress import ProviderEgressUnavailable


@dataclass(frozen=True, repr=False)
class OpenAIFallbackClassifier:
    credential: DecisionCredential | None = field(repr=False)
    egress: DecisionEgress | None = field(default=None, repr=False)
    endpoint: str = FALLBACK_ENDPOINT
    model: str = FALLBACK_MODEL
    timeout_seconds: float = 20.0
    credential_transport: httpx2.AsyncBaseTransport | None = field(default=None, repr=False)
    credential_verify: ssl.SSLContext | bool = field(default=True, repr=False)
    budget: object | None = field(default=None, repr=False)
    roles: ModelRoles = field(default_factory=ModelRoles.from_environment)

    def __post_init__(self) -> None:
        if self.credential is not None and not callable(getattr(self.credential, "api_key", None)):
            raise ValueError("A model credential capability is required.")
        if self.egress is not None and not callable(getattr(self.egress, "post_json", None)):
            raise ValueError("A shared egress capability is required.")
        if self.endpoint != FALLBACK_ENDPOINT or self.model != FALLBACK_MODEL:
            raise ValueError("The fallback is fixed to the bounded OpenAI release.")
        if not 0.1 <= float(self.timeout_seconds) <= 60:
            raise ValueError("Fallback timeout is outside the bounded profile.")
        if self.credential_verify is not True and not isinstance(
            self.credential_verify, ssl.SSLContext
        ):
            raise ValueError("Fallback credential TLS verification cannot be disabled.")

    async def classify(
        self,
        request: DecisionRequest,
        *,
        primary_failure: str,
    ) -> FallbackClassification:
        if self.credential is None:
            raise FallbackUnavailable("FALLBACK_UNCONFIGURED")
        if self.egress is None:
            raise FallbackUnavailable("FALLBACK_EGRESS_UNCONFIGURED")
        if self.budget is None:
            raise FallbackUnavailable("FALLBACK_BUDGET_UNCONFIGURED")
        config = self.roles.for_role("claim_check")
        payload = _fallback_payload(request, primary_failure=primary_failure)
        payload.update(
            model=config.model, reasoning={"effort": config.effort}, max_output_tokens=8192
        )
        body = canonical_json(payload)
        operation_id = _derived_operation_id(request.decision_id, "openai-fallback")
        try:
            if callable(getattr(self.budget, "validate_scope", None)):
                self.budget.validate_scope(self.egress)
            self.budget.reserve(operation_id, body, "claim_check", config)
        except ModelBudgetUnavailable:
            raise FallbackUnavailable("FALLBACK_BUDGET_UNAVAILABLE") from None
        try:
            key = await self.credential.api_key(
                transport=self.credential_transport,
                verify=self.credential_verify,
            )
        except (ModelCredentialError, JevCredentialError):
            raise FallbackUnavailable("FALLBACK_CREDENTIAL_UNAVAILABLE") from None
        try:
            self.budget.dispatch(operation_id, body)
            response = await asyncio.to_thread(
                self.egress.post_json,
                url=self.endpoint,
                profile=EgressProfile.OPENAI_MODEL,
                authorization=f"Bearer {key}",
                body=body,
                operation_id=operation_id,
                timeout_seconds=float(self.timeout_seconds),
                max_response_bytes=_MAX_RESPONSE_BYTES,
            )
        except ModelBudgetUnavailable:
            raise FallbackUnavailable("FALLBACK_BUDGET_UNAVAILABLE") from None
        except ProviderEgressUnavailable as error:
            code = (
                "FALLBACK_TRANSPORT_UNAVAILABLE" if error.retryable else "FALLBACK_RESPONSE_INVALID"
            )
            raise FallbackUnavailable(code) from None
        body = response.body
        status_code = response.status_code
        media_type = response.media_type
        if status_code != 200 or media_type.strip().lower() != "application/json":
            raise FallbackUnavailable("FALLBACK_PROVIDER_UNAVAILABLE")
        classified = _parse_fallback_response(body, model_requested=config.model)
        from signal_core.model_reasoning import BrainModelResult, MetadataDraftError, _parse_usage

        try:
            document = json.loads(body)
            result = BrainModelResult(
                {
                    "recommendation": classified.recommendation.value,
                    "confidence": classified.confidence,
                    "reason_code": classified.reason_code,
                },
                document["id"],
                classified.model_reported,
                _parse_usage(document.get("usage")),
            )
            self.budget.finish(
                operation_id,
                model=config,
                result=result,
                response_sha256=hashlib.sha256(body).hexdigest(),
            )
        except (MetadataDraftError, ModelBudgetUnavailable):
            raise FallbackUnavailable("FALLBACK_USAGE_UNKNOWN") from None
        return classified
