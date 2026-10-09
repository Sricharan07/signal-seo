import json
from dataclasses import dataclass
from uuid import uuid4

import pytest
from signal_core.decision_contracts import (
    ChoiceQuestion,
    DecisionRequest,
    NoulQuestion,
    Recommendation,
    ScoreQuestion,
)
from signal_core.jev_decisions import (
    DecisionService,
    FallbackClassification,
    FallbackUnavailable,
    JevHttpAdapter,
    JevUnavailable,
)
from signal_core.shared_egress import ProviderEgressResponse, ProviderEgressUnavailable

from tests.tooling.decision_fallback_support import OpenAIFallbackClassifier
from tests.tooling.model_test_support import Budget


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Credential:
    async def api_key(self, **kwargs) -> str:
        return "typesafe-test-key-0000000000000000"


class Egress:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def post_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.handler(kwargs)


def request(*, ceiling: Recommendation = Recommendation.SHIP) -> DecisionRequest:
    return DecisionRequest(
        decision_id=uuid4(),
        purpose="autonomy.triage",
        state={"finding": "canonical conflict", "source": "verified crawl"},
        questions={
            "recommendation": ChoiceQuestion(
                "What is the safest next step?",
                {
                    "ship": "Recommend unattended continuation.",
                    "ask_owner": "Require an owner decision.",
                    "reject": "Stop this work.",
                },
            ),
            "risk": ScoreQuestion("How risky is this work?", ["low", "medium", "high"]),
            "evidence_sufficient": NoulQuestion(
                "Is the evidence sufficient?",
                true_criterion="Evidence is current and corroborated.",
                false_criterion="Evidence is stale or incomplete.",
            ),
        },
        threshold=0.8,
        policy_ceiling=ceiling,
    )


def response(*, choice: str = "ship", confidence: float = 0.9) -> dict[str, object]:
    recommendation_probabilities = {
        "ship": 0.9 if choice == "ship" else 0.05,
        "ask_owner": 0.9 if choice == "ask_owner" else 0.05,
        "reject": 0.9 if choice == "reject" else 0.05,
    }
    if choice not in recommendation_probabilities:
        recommendation_probabilities = {"ship": 0.4, "ask_owner": 0.4, "reject": 0.2}
    return {
        "model": "jev-1.13.0",
        "answers": {
            "recommendation": {
                "type": "choice",
                "choice": choice,
                "probabilities": recommendation_probabilities,
                "confidence": confidence,
            },
            "risk": {
                "type": "score",
                "score": 1.05,
                "legend": {"0": "low", "1": "medium", "2": "high"},
                "probabilities": {"0": 0.0, "1": 0.95, "2": 0.05},
                "confidence": 0.92,
            },
            "evidence_sufficient": {"type": "noul", "noul": 0.72},
        },
        "usage": {"input_tokens": 320, "output_tokens": 48},
    }


def adapter_for(document: dict[str, object]) -> JevHttpAdapter:
    def handler(call) -> ProviderEgressResponse:
        assert call["url"] == "https://api.typesafe.ai/v1/systemone"
        assert call["authorization"].startswith("Bearer ")
        assert call["operation_id"].version == 4
        assert call["timeout_seconds"] == 20.0
        assert call["max_response_bytes"] == 128 * 1024
        submitted = json.loads(call["body"])
        assert submitted["model"] == "jev-latest"
        assert set(submitted["questions"]) == {
            "recommendation",
            "risk",
            "evidence_sufficient",
        }
        return ProviderEgressResponse(
            200,
            "application/json",
            json.dumps(document).encode(),
        )

    return JevHttpAdapter(Credential(), Egress(handler))


@pytest.mark.anyio
async def test_typed_questions_round_trip_through_fixed_jev_boundary() -> None:
    evaluation = await adapter_for(response()).evaluate(request())

    assert evaluation.model_reported == "jev-1.13.0"
    assert evaluation.input_tokens == 320
    assert evaluation.answers["recommendation"].choice == "ship"
    assert evaluation.answers["risk"].score == 1.05
    assert evaluation.answers["evidence_sufficient"].noul == 0.72


@pytest.mark.parametrize(
    "question",
    [
        lambda: ChoiceQuestion("Choose.", {str(index): None for index in range(256)}),
        lambda: ScoreQuestion("Score.", ["only one"]),
        lambda: ScoreQuestion("Score.", [str(index) for index in range(11)]),
        lambda: NoulQuestion("Question.", true_criterion="yes"),
        lambda: NoulQuestion("Question.", true_criterion=object()),
    ],
)
def test_question_contract_rejects_documented_limit_violations(question) -> None:
    with pytest.raises(ValueError):
        question()


def test_request_rejects_non_json_and_over_limit_shared_input() -> None:
    with pytest.raises(ValueError, match="text, a JSON object"):
        DecisionRequest(
            decision_id=uuid4(),
            purpose="bad.input",
            state=b"not-json",
            questions=request().questions,
            threshold=0.8,
            policy_ceiling=Recommendation.SHIP,
        )
    with pytest.raises(ValueError, match="64 KiB"):
        DecisionRequest(
            decision_id=uuid4(),
            purpose="large.input",
            state="x" * (64 * 1024),
            questions=request().questions,
            threshold=0.8,
            policy_ceiling=Recommendation.SHIP,
        )


def _mutated_response(mutator) -> dict[str, object]:
    document = response()
    mutator(document)
    return document


@pytest.mark.anyio
@pytest.mark.parametrize(
    "document",
    [
        response(choice="unknown"),
        _mutated_response(
            lambda value: value["answers"]["recommendation"].update(
                probabilities={"ship": 0.8, "ask_owner": 0.1, "reject": 0.0}
            )
        ),
        _mutated_response(
            lambda value: value["answers"]["recommendation"].update(
                probabilities={"ship": 1.1, "ask_owner": -0.1, "reject": 0.0}
            )
        ),
        _mutated_response(lambda value: value["answers"]["recommendation"].update(confidence=1.1)),
        _mutated_response(
            lambda value: value["answers"]["recommendation"].update(
                probabilities={"ship": 0.2, "ask_owner": 0.7, "reject": 0.1}
            )
        ),
        _mutated_response(lambda value: value.update(model="jev-latest")),
    ],
)
async def test_ec125_invalid_answers_are_unavailable(document: dict[str, object]) -> None:
    with pytest.raises(JevUnavailable) as captured:
        await adapter_for(document).evaluate(request())

    assert captured.value.code == "JEV_RESPONSE_INVALID"
    assert captured.value.retryable is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, "JEV_RATE_LIMITED", True),
        (500, "JEV_PROVIDER_UNAVAILABLE", True),
        (529, "JEV_PROVIDER_UNAVAILABLE", True),
        (401, "JEV_PROVIDER_REJECTED", False),
    ],
)
async def test_provider_failures_are_sanitized_and_classified(
    status: int,
    code: str,
    retryable: bool,
) -> None:
    adapter = JevHttpAdapter(
        Credential(),
        Egress(
            lambda call: ProviderEgressResponse(
                status,
                "application/json",
                b'{"secret":"provider detail"}',
            )
        ),
    )

    with pytest.raises(JevUnavailable) as captured:
        await adapter.evaluate(request())

    assert captured.value.code == code
    assert captured.value.retryable is retryable
    assert "provider detail" not in str(captured.value)


@pytest.mark.anyio
async def test_timeout_and_unconfigured_jev_are_unavailable() -> None:
    def timeout(call):
        raise ProviderEgressUnavailable("EGRESS_TRANSPORT_ERROR", retryable=True)

    with pytest.raises(JevUnavailable, match="JEV_TRANSPORT_UNAVAILABLE"):
        await JevHttpAdapter(
            Credential(),
            Egress(timeout),
        ).evaluate(request())
    with pytest.raises(JevUnavailable, match="JEV_UNCONFIGURED"):
        await JevHttpAdapter(None).evaluate(request())
    with pytest.raises(JevUnavailable, match="JEV_EGRESS_UNCONFIGURED"):
        await JevHttpAdapter(Credential()).evaluate(request())


@pytest.mark.anyio
async def test_provider_reported_shared_token_limit_is_enforced() -> None:
    document = response()
    document["usage"]["input_tokens"] = 65_537

    with pytest.raises(JevUnavailable, match="JEV_RESPONSE_INVALID"):
        await adapter_for(document).evaluate(request())


@dataclass
class Recorder:
    calls: list[tuple[DecisionRequest, object]]

    def record(self, decision_request, recommendation) -> None:
        self.calls.append((decision_request, recommendation))


class Classifier:
    async def classify(self, decision_request, *, primary_failure):
        return FallbackClassification(
            Recommendation.ASK_OWNER,
            0.61,
            "gpt-6-luna-2026-09-01",
            "AMBIGUOUS_EVIDENCE",
        )


@pytest.mark.anyio
@pytest.mark.parametrize("ceiling", list(Recommendation))
@pytest.mark.parametrize("provider_choice", list(Recommendation))
async def test_inv026_never_widens_and_exposes_no_execution_capability(
    ceiling: Recommendation,
    provider_choice: Recommendation,
) -> None:
    recorder = Recorder([])
    service = DecisionService(adapter_for(response(choice=provider_choice.value)), recorder)

    result = await service.recommend(request(ceiling=ceiling))

    assert result.recommendation.safety_rank <= ceiling.safety_rank
    assert recorder.calls == [(recorder.calls[0][0], result)]
    assert not any(
        callable(getattr(service, name, None))
        for name in ("authorize", "execute", "dispatch", "merge", "deploy")
    )


@pytest.mark.anyio
async def test_low_confidence_ship_is_reduced_to_owner_review() -> None:
    recorder = Recorder([])
    result = await DecisionService(
        adapter_for(response(confidence=0.79)),
        recorder,
    ).recommend(request())

    assert result.recommendation is Recommendation.ASK_OWNER
    assert result.fallback is False


@pytest.mark.anyio
async def test_unavailable_jev_uses_labelled_frontier_fallback() -> None:
    recorder = Recorder([])
    result = await DecisionService(JevHttpAdapter(None), recorder, Classifier()).recommend(
        request()
    )

    assert result.recommendation is Recommendation.ASK_OWNER
    assert result.provider == "openai_fallback"
    assert result.fallback is True
    assert result.fallback_reason == "JEV_UNCONFIGURED"
    assert recorder.calls[0][1] == result


class BrokenClassifier:
    async def classify(self, decision_request, *, primary_failure):
        raise FallbackUnavailable("FALLBACK_UNAVAILABLE")


@pytest.mark.anyio
async def test_unavailable_fallback_reduces_to_deterministic_owner_review() -> None:
    recorder = Recorder([])
    result = await DecisionService(
        JevHttpAdapter(None),
        recorder,
        BrokenClassifier(),
    ).recommend(request())

    assert result.provider == "deterministic_fallback"
    assert result.recommendation is Recommendation.ASK_OWNER
    assert result.confidence == 0
    assert result.fallback_reason == "JEV_UNCONFIGURED"


@pytest.mark.anyio
async def test_frontier_fallback_is_structured_toolless_and_cannot_ship() -> None:
    def handler(call) -> ProviderEgressResponse:
        body = json.loads(call["body"])
        assert body["store"] is False
        assert body["tools"] == []
        assert body["text"]["format"]["schema"]["properties"]["recommendation"]["enum"] == [
            "ask_owner",
            "reject",
        ]
        return ProviderEgressResponse(
            200,
            "application/json",
            json.dumps(
                {
                    "id": "resp_fallback_1",
                    "model": "gpt-6-luna-2026-09-01",
                    "status": "completed",
                    "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
                    "output": [
                        {
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(
                                        {
                                            "recommendation": "reject",
                                            "confidence": 0.84,
                                            "reason_code": "POLICY_AMBIGUOUS",
                                        }
                                    ),
                                }
                            ],
                        }
                    ],
                }
            ).encode(),
        )

    classification = await OpenAIFallbackClassifier(
        Credential(),
        Egress(handler),
        budget=Budget(),
    ).classify(request(), primary_failure="JEV_RATE_LIMITED")

    assert classification.recommendation is Recommendation.REJECT
    assert classification.confidence == 0.84
