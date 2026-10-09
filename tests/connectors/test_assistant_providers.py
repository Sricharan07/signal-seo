import hashlib
import json
from uuid import uuid4

import pytest
from signal_core.assistant_credentials import AssistantCredentialUnavailable
from signal_core.assistant_providers import AssistantProviderError, request_assistant_search
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import EgressProfile
from signal_core.shared_egress import (
    ProviderEgressResponse,
    SharedEgressProvider,
    SharedEgressRequest,
)


class FakeCredentials:
    async def api_key(self, provider):
        if provider == "perplexity" and self.unavailable:
            raise AssistantCredentialUnavailable()
        return "synthetic-provider-key-123456"

    unavailable = False


class FakeEgress(SharedEgressProvider):
    def __init__(self, document, status=200):
        object.__setattr__(self, "purpose", "model")
        self.response = ProviderEgressResponse(
            status, "application/json", json.dumps(document).encode()
        )
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_openai_fixed_web_search_is_bounded_and_evidenced():
    egress = FakeEgress(
        {"id": "resp_123", "model": "gpt-4.1-mini", "status": "completed", "output": []}
    )
    result = await request_assistant_search(
        FakeCredentials(),
        egress,
        provider="openai",
        question="Which pages discuss our product?",
        operation_id=uuid4(),
    )
    request = egress.calls[0]
    body = json.loads(request["body"])
    assert request["url"] == "https://api.openai.com/v1/responses"
    assert request["profile"] == EgressProfile.OPENAI_ASSISTANT
    assert request["authorization"] == "Bearer synthetic-provider-key-123456"
    assert body == {
        "model": "gpt-4.1-mini",
        "input": "Which pages discuss our product?",
        "tools": [{"type": "web_search"}],
        "tool_choice": "required",
        "max_output_tokens": 512,
        "max_tool_calls": 2,
        "store": False,
    }
    assert result.response_id == "resp_123"
    assert result.request_sha256 == hashlib.sha256(request["body"]).digest()
    assert result.response_sha256 == hashlib.sha256(egress.response.body).digest()
    assert "synthetic-provider-key" not in repr(result)


@pytest.mark.anyio
async def test_perplexity_preset_and_gemini_key_header_are_separate():
    egress = FakeEgress(
        {"id": "resp_456", "model": "perplexity/fast", "status": "completed", "output": []}
    )
    result = await request_assistant_search(
        FakeCredentials(),
        egress,
        provider="perplexity",
        question="Who cites our homepage?",
        operation_id=uuid4(),
    )
    assert result.model_reported == "perplexity/fast"
    assert egress.calls[0]["url"] == "https://api.perplexity.ai/v1/agent"
    assert egress.calls[0]["profile"] == EgressProfile.PERPLEXITY_ASSISTANT
    assert json.loads(egress.calls[0]["body"])["preset"] == "fast"
    egress = FakeEgress(
        {"modelVersion": "gemini-2.5-flash", "responseId": "gem_789", "candidates": []}
    )
    result = await request_assistant_search(
        FakeCredentials(),
        egress,
        provider="gemini",
        question="Who cites our homepage?",
        operation_id=uuid4(),
    )
    assert result.model_reported == "gemini-2.5-flash"
    assert egress.calls[0]["authorization"] is None
    assert egress.calls[0]["google_api_key"] == "synthetic-provider-key-123456"
    assert egress.calls[0]["profile"] == EgressProfile.GEMINI_ASSISTANT
    assert json.loads(egress.calls[0]["body"])["tools"] == [{"google_search": {}}]


def test_google_api_key_cannot_leave_fixed_model_origin():
    for purpose, url in (
        (
            "connector",
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        ),
        ("model", "https://other.example/v1/assistant"),
    ):
        with pytest.raises(ValueError, match="Egress profile"):
            SharedEgressRequest(
                purpose,
                EgressHttpRequest(
                    "POST",
                    url,
                    headers=(
                        ("accept", "application/json"),
                        ("content-type", "application/json"),
                        ("x-goog-api-key", "synthetic-google-key-00000000"),
                    ),
                    body=b"{}",
                ),
                profile=EgressProfile.GEMINI_ASSISTANT,
            )
    allowed = SharedEgressRequest(
        "model",
        EgressHttpRequest(
            "POST",
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            headers=(
                ("accept", "application/json"),
                ("content-type", "application/json"),
                ("x-goog-api-key", "synthetic-google-key-00000000"),
            ),
            body=b"{}",
        ),
        profile=EgressProfile.GEMINI_ASSISTANT,
    )
    assert allowed.credentialed
    assert "synthetic-google-key" not in repr(allowed)


@pytest.mark.anyio
async def test_unavailable_malformed_and_provider_failure_are_explicit():
    credentials = FakeCredentials()
    credentials.unavailable = True
    egress = FakeEgress({})
    with pytest.raises(AssistantProviderError, match="ASSISTANT_PROVIDER_UNAVAILABLE"):
        await request_assistant_search(
            credentials,
            egress,
            provider="perplexity",
            question="Who cites this site?",
            operation_id=uuid4(),
        )
    assert egress.calls == []
    with pytest.raises(AssistantProviderError, match="ASSISTANT_QUESTION_REJECTED"):
        await request_assistant_search(
            FakeCredentials(), egress, provider="openai", question="x\nsecret", operation_id=uuid4()
        )
    egress.response = ProviderEgressResponse(503, "application/json", b"{}")
    with pytest.raises(AssistantProviderError, match="ASSISTANT_PROVIDER_UNAVAILABLE"):
        await request_assistant_search(
            FakeCredentials(),
            egress,
            provider="openai",
            question="Who cites this site?",
            operation_id=uuid4(),
        )
    egress.response = ProviderEgressResponse(
        200,
        "application/json",
        b'{"id":"bad","model":"gpt-4.1-mini","status":"incomplete","output":[]}',
    )
    with pytest.raises(AssistantProviderError, match="ASSISTANT_RESPONSE_REJECTED"):
        await request_assistant_search(
            FakeCredentials(),
            egress,
            provider="openai",
            question="Who cites this site?",
            operation_id=uuid4(),
        )


def test_gemini_key_is_credentialed_redacted_and_origin_limited():
    secret = "synthetic-gemini-secret-123456"
    request = SharedEgressRequest(
        "model",
        EgressHttpRequest(
            "POST",
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            headers=(
                ("accept", "application/json"),
                ("content-type", "application/json"),
                ("x-goog-api-key", secret),
            ),
            body=b'{"contents":[]}',
        ),
        profile=EgressProfile.GEMINI_ASSISTANT,
    )
    assert request.credentialed is True
    assert len(request.request_sha256) == 32
    assert secret not in repr(request)
    with pytest.raises(ValueError, match="Egress profile"):
        SharedEgressRequest(
            "model",
            EgressHttpRequest(
                "POST",
                "https://other.example/",
                headers=(
                    ("accept", "application/json"),
                    ("content-type", "application/json"),
                    ("x-goog-api-key", secret),
                ),
                body=b"{}",
            ),
            profile=EgressProfile.GEMINI_ASSISTANT,
        )
