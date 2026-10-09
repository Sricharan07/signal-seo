import json
from dataclasses import dataclass

import httpx2
import pytest
from signal_core.model_reasoning import (
    MODEL_INSTRUCTIONS,
    MODEL_PROMPT_SHA256,
    MODEL_RELEASE,
    OPENAI_MODEL,
    VERIFIED_MODEL_INSTRUCTIONS,
    VERIFIED_MODEL_PROMPT_SHA256,
    VERIFIED_MODEL_RELEASE,
    CrawlTextTask,
    MetadataDraftError,
    MetadataDraftTask,
)

from tests.tooling.model_test_support import metadata_adapter as OpenAIResponsesAdapter


@pytest.mark.anyio
async def test_crawl_text_draft_is_bounded_and_uses_only_observed_facts() -> None:
    observed = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        observed["payload"] = json.loads(request.content)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json=_response({"text": "Signal Guide"}),
        )

    result = await OpenAIResponsesAdapter(
        Credential(), response_transport=httpx2.MockTransport(handler)
    ).draft_crawl_text(CrawlTextTask("title", "https://example.test/", "", ("Signal Guide",)))
    assert result.text == "Signal Guide"
    assert observed["payload"]["store"] is False
    assert observed["payload"]["tools"] == []
    assert observed["payload"]["text"]["format"]["strict"] is True


@pytest.mark.anyio
async def test_crawl_text_draft_rejects_bad_output_and_provider_loss() -> None:
    task = CrawlTextTask("title", "https://example.test/", "", ("Signal Guide",))
    invalid = OpenAIResponsesAdapter(
        Credential(),
        response_transport=httpx2.MockTransport(
            lambda request: httpx2.Response(
                200,
                headers={"content-type": "application/json"},
                json=_response({"text": "X" * 90}),
            )
        ),
    )
    with pytest.raises(MetadataDraftError, match="MODEL_OUTPUT_INVALID"):
        await invalid.draft_crawl_text(task)
    unavailable = OpenAIResponsesAdapter(
        Credential(),
        response_transport=httpx2.MockTransport(
            lambda request: httpx2.Response(503, json={"error": "unavailable"})
        ),
    )
    with pytest.raises(MetadataDraftError, match="MODEL_PROVIDER_REJECTED"):
        await unavailable.draft_crawl_text(task)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass(frozen=True)
class Credential:
    value: str = "sk-test-local-reasoning-credential"

    async def api_key(self, *, transport=None, verify=True) -> str:
        assert transport is None
        assert verify is True
        return self.value


def _task() -> MetadataDraftTask:
    return MetadataDraftTask(
        site_id="33333333-3333-4333-8333-333333333333",
        finding_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        evidence_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        command_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        resource_locator="/fixture/missing-meta-description",
        observed_at="2026-09-13T12:00:00.000000Z",
    )


def _response(
    output: dict[str, str] | None = None,
    *,
    status: str = "completed",
    model: str = OPENAI_MODEL,
) -> dict[str, object]:
    drafted = output or {
        "meta_description": (
            "Explore Signal's fixture product and see how durable SEO evidence supports "
            "clear, reviewable metadata recommendations."
        ),
        "rationale": "The description reflects only the supplied title and evidence context.",
    }
    return {
        "id": "resp_signal_local_1",
        "model": model,
        "status": status,
        "output": [
            {
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(drafted)}],
            }
        ],
        "usage": {
            "input_tokens": 120,
            "output_tokens": 40,
            "total_tokens": 160,
            "input_tokens_details": {"cached_tokens": 20},
        },
    }


@pytest.mark.anyio
async def test_luna_metadata_call_is_bounded_schema_driven_and_not_provider_stored() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        observed["authorization"] = request.headers["authorization"]
        observed["payload"] = json.loads(request.content)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json=_response(),
        )

    result = await OpenAIResponsesAdapter(
        Credential(),
        response_transport=httpx2.MockTransport(handler),
    ).draft_metadata(_task())

    payload = observed["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == OPENAI_MODEL
    assert payload["instructions"] == MODEL_INSTRUCTIONS
    assert payload["store"] is False
    assert payload["parallel_tool_calls"] is False
    assert payload["tools"] == []
    assert payload["reasoning"] == {"effort": "medium"}
    assert payload["max_output_tokens"] == 8192
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert observed["authorization"] == "Bearer sk-test-local-reasoning-credential"
    assert result.model_reported == OPENAI_MODEL
    assert result.usage.cached_input_tokens == 20
    assert 70 <= len(result.meta_description) <= 160
    assert len(result.output_sha256) == 64
    assert MODEL_RELEASE == "local-gpt-6-luna-metadata-v2"
    assert len(MODEL_PROMPT_SHA256) == 64


@pytest.mark.anyio
async def test_verified_homepage_call_uses_real_page_packet_and_distinct_release() -> None:
    observed: dict[str, object] = {}
    task = MetadataDraftTask(
        site_id="33333333-3333-4333-8333-333333333333",
        finding_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        evidence_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        command_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        resource_locator="https://example.test/",
        observed_at="2026-09-20T12:00:00.000000Z",
        page_title="Acme analytics",
        page_heading="Understand search performance",
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        observed["payload"] = json.loads(request.content)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json=_response(
                {
                    "meta_description": (
                        "Understand search performance with Acme analytics using the title "
                        "and heading observed on its verified homepage."
                    ),
                    "rationale": "The draft uses only the verified title and heading.",
                }
            ),
        )

    await OpenAIResponsesAdapter(
        Credential(), response_transport=httpx2.MockTransport(handler)
    ).draft_verified_metadata(task)

    payload = observed["payload"]
    assert isinstance(payload, dict)
    assert payload["instructions"] == VERIFIED_MODEL_INSTRUCTIONS
    packet = json.loads(payload["input"])
    assert packet["resource_locator"] == "https://example.test/"
    assert packet["page_title"] == "Acme analytics"
    assert packet["page_heading"] == "Understand search performance"
    assert VERIFIED_MODEL_RELEASE == "verified-gpt-6-luna-metadata-v2"
    assert len(VERIFIED_MODEL_PROMPT_SHA256) == 64


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [(429, "MODEL_PROVIDER_REJECTED", False), (500, "MODEL_PROVIDER_REJECTED", False)],
)
async def test_model_http_failures_are_sanitized(status: int, code: str, retryable: bool) -> None:
    transport = httpx2.MockTransport(
        lambda request: httpx2.Response(
            status,
            headers={"content-type": "application/json"},
            json={"error": {"message": "sensitive provider detail"}},
        )
    )

    with pytest.raises(MetadataDraftError) as captured:
        await OpenAIResponsesAdapter(Credential(), response_transport=transport).draft_metadata(
            _task()
        )

    assert captured.value.code == code
    assert captured.value.retryable is retryable
    assert "sensitive" not in str(captured.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("document", "code"),
    [
        (_response(status="incomplete"), "MODEL_RESPONSE_INCOMPLETE"),
        (_response(model="gpt-unapproved"), "MODEL_RESPONSE_INVALID"),
        (
            _response(
                {
                    "meta_description": "Too short",
                    "rationale": "Unsupported output must fail.",
                }
            ),
            "MODEL_OUTPUT_INVALID",
        ),
    ],
)
async def test_model_response_and_output_fail_closed(
    document: dict[str, object], code: str
) -> None:
    transport = httpx2.MockTransport(
        lambda request: httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json=document,
        )
    )

    with pytest.raises(MetadataDraftError) as captured:
        await OpenAIResponsesAdapter(Credential(), response_transport=transport).draft_metadata(
            _task()
        )

    assert captured.value.code == code


def test_metadata_task_has_stable_bounded_identity() -> None:
    task = _task()
    reversed_packet = json.loads(task.canonical_bytes(), object_pairs_hook=dict)

    assert len(task.canonical_bytes()) < 2048
    assert task.sha256 == __import__("hashlib").sha256(task.canonical_bytes()).hexdigest()
    assert reversed_packet["current_meta_description"] is None
    assert "instruction" not in reversed_packet
