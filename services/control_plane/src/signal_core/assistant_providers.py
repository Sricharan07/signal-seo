"""Bounded official assistant API requests over the shared model egress gateway."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.assistant_credentials import (
    PROVIDERS,
    AssistantCredentialUnavailable,
    AssistantProvider,
    OpenBaoAssistantCredentials,
)
from signal_core.database import _clean_transaction
from signal_core.egress_profiles import EgressProfile
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider

_MODELS = {
    "openai": "gpt-4.1-mini",
    "perplexity": "fast",
    "gemini": "gemini-2.5-flash",
}
_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/responses",
    "perplexity": "https://api.perplexity.ai/v1/agent",
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    ),
}
_EGRESS_PROFILES = {
    "openai": EgressProfile.OPENAI_ASSISTANT,
    "perplexity": EgressProfile.PERPLEXITY_ASSISTANT,
    "gemini": EgressProfile.GEMINI_ASSISTANT,
}
_IDENTIFIER = re.compile(r"[A-Za-z0-9_./:-]{1,128}")
MAX_QUESTION_BYTES = 2048
MAX_RESPONSE_BYTES = 128 * 1024


class AssistantProviderError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class AssistantProviderResult:
    provider: AssistantProvider
    model_requested: str
    model_reported: str
    response_id: str
    request_sha256: bytes
    response_sha256: bytes
    response: dict = field(repr=False)
    egress_operation_id: UUID


@dataclass(frozen=True)
class AssistantEvidenceRecorded:
    evidence_id: UUID
    provider: AssistantProvider
    model_reported: str
    response_id: str


async def request_assistant_search(
    credentials: OpenBaoAssistantCredentials,
    egress: SharedEgressProvider,
    *,
    provider: AssistantProvider,
    question: str,
    operation_id: UUID,
    before_egress=None,
) -> AssistantProviderResult:
    if provider not in PROVIDERS:
        raise AssistantProviderError("ASSISTANT_PROVIDER_REJECTED")
    if not isinstance(egress, SharedEgressProvider) or egress.purpose != "model":
        raise AssistantProviderError("ASSISTANT_EGRESS_REQUIRED")
    if (
        not isinstance(question, str)
        or not 8 <= len(question) <= 512
        or any(ord(char) < 32 or ord(char) == 127 for char in question)
        or len(question.encode("utf-8")) > MAX_QUESTION_BYTES
    ):
        raise AssistantProviderError("ASSISTANT_QUESTION_REJECTED")
    try:
        api_key = await credentials.api_key(provider)
    except AssistantCredentialUnavailable:
        raise AssistantProviderError("ASSISTANT_PROVIDER_UNAVAILABLE") from None
    body = _request_body(provider, question)
    if len(body) > 4096:
        raise AssistantProviderError("ASSISTANT_REQUEST_REJECTED")
    authorization = None if provider == "gemini" else f"Bearer {api_key}"
    if before_egress is not None:
        await before_egress()
    try:
        response = egress.request_json(
            method="POST",
            url=_ENDPOINTS[provider],
            profile=_EGRESS_PROFILES[provider],
            authorization=authorization,
            google_api_key=api_key if provider == "gemini" else None,
            body=body,
            operation_id=operation_id,
            timeout_seconds=30,
            max_response_bytes=MAX_RESPONSE_BYTES,
        )
    except ProviderEgressUnavailable:
        raise AssistantProviderError("ASSISTANT_PROVIDER_UNAVAILABLE") from None
    if response.status_code in {401, 403}:
        raise AssistantProviderError("ASSISTANT_CREDENTIAL_REJECTED")
    if response.status_code == 429 or response.status_code >= 500:
        raise AssistantProviderError("ASSISTANT_PROVIDER_UNAVAILABLE")
    if response.status_code != 200 or len(response.body) > MAX_RESPONSE_BYTES:
        raise AssistantProviderError("ASSISTANT_RESPONSE_REJECTED")
    if api_key.encode() in response.body:
        raise AssistantProviderError("ASSISTANT_RESPONSE_REJECTED")
    document = _response_document(response.body)
    model, response_id = _identity(provider, document)
    return AssistantProviderResult(
        provider,
        _MODELS[provider],
        model,
        response_id,
        hashlib.sha256(body).digest(),
        hashlib.sha256(response.body).digest(),
        document,
        operation_id,
    )


def record_assistant_evidence(
    connection: Connection,
    *,
    tenant_id: UUID,
    site_id: UUID,
    result: AssistantProviderResult,
) -> AssistantEvidenceRecorded:
    if not isinstance(result, AssistantProviderResult):
        raise AssistantProviderError("ASSISTANT_EVIDENCE_REJECTED")
    evidence_id = uuid4()
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control.record_assistant_provider_evidence("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                tenant_id,
                site_id,
                evidence_id,
                result.provider,
                result.model_requested,
                result.model_reported,
                result.response_id,
                result.request_sha256,
                result.response_sha256,
                Jsonb(result.response),
                result.egress_operation_id,
            ),
        ).fetchone()[0]
    if outcome != "recorded":
        raise AssistantProviderError("ASSISTANT_EVIDENCE_UNAVAILABLE")
    return AssistantEvidenceRecorded(
        evidence_id, result.provider, result.model_reported, result.response_id
    )


def _request_body(provider: AssistantProvider, question: str) -> bytes:
    if provider == "openai":
        document = {
            "model": _MODELS[provider],
            "input": question,
            "tools": [{"type": "web_search"}],
            "tool_choice": "required",
            "max_output_tokens": 512,
            "max_tool_calls": 2,
            "store": False,
        }
    elif provider == "perplexity":
        document = {
            "preset": _MODELS[provider],
            "input": question,
            "max_output_tokens": 512,
            "max_tool_calls": 2,
            "store": False,
        }
    else:
        document = {
            "contents": [{"parts": [{"text": question}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"maxOutputTokens": 512},
        }
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _response_document(body: bytes) -> dict:
    try:
        document = json.loads(body, parse_constant=lambda _: _reject_constant())
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise AssistantProviderError("ASSISTANT_RESPONSE_REJECTED") from None
    if not isinstance(document, dict):
        raise AssistantProviderError("ASSISTANT_RESPONSE_REJECTED")
    return document


def _reject_constant() -> None:
    raise ValueError("Non-finite provider JSON is not accepted.")


def _identity(provider: AssistantProvider, document: dict) -> tuple[str, str]:
    if provider == "gemini":
        model = document.get("modelVersion")
        response_id = document.get("responseId")
        if not isinstance(document.get("candidates"), list):
            raise AssistantProviderError("ASSISTANT_RESPONSE_REJECTED")
    else:
        model = document.get("model")
        response_id = document.get("id")
        if document.get("status") != "completed" or not isinstance(document.get("output"), list):
            raise AssistantProviderError("ASSISTANT_RESPONSE_REJECTED")
    if (
        not isinstance(model, str)
        or _IDENTIFIER.fullmatch(model) is None
        or not isinstance(response_id, str)
        or _IDENTIFIER.fullmatch(response_id) is None
    ):
        raise AssistantProviderError("ASSISTANT_RESPONSE_REJECTED")
    return model, response_id
