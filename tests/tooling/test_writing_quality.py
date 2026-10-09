import json
from dataclasses import replace
from uuid import uuid4

import pytest
from signal_core.article_pipeline import draft_with_quality
from signal_core.content_writer import (
    RUBRIC,
    ContentWriterRejected,
    article_sentences,
    entailment_question,
    grounding_report,
    validate_claims,
)
from signal_core.content_writer_service import _EditorialFallback, _entailment
from signal_core.decision_contracts import ChoiceAnswer, DecisionEvaluation, NoulAnswer
from signal_core.jev_decisions import JevHttpAdapter
from signal_core.model_reasoning import (
    BusinessBrainModelAdapter,
    ContentWriterModelAdapter,
    MetadataDraftError,
    ModelUsage,
)
from signal_core.model_roles import ROLE_EFFORTS, ModelRoles, RoleModel
from signal_core.shared_egress import ProviderEgressResponse, ProviderEgressUnavailable
from signal_core.writing_evaluation import CASES, FACT_ID, evaluation_packet
from signal_core.writing_quality import BANNED_PHRASES, THRESHOLDS, quality_report, tokens
from signal_core.writing_style import STYLE_PREFIX, voice_packet

from tests.tooling.model_test_support import Budget


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Credential:
    def __init__(self, events):
        self.events = events

    async def api_key(self):
        self.events.append("key")
        return "synthetic-writing-model-key"


def article(text, fact_ids=None):
    tagged = {"text": text, "fact_ids": [FACT_ID] if fact_ids is None else fact_ids}
    return {
        "title": tagged,
        "meta_description": tagged,
        "sections": [{"heading": tagged, "sentences": [tagged]}],
        "internal_links": [],
    }


class SyntheticModel:
    def __init__(self, budget, *, slop_attempts=0, failure=False):
        self.calls, self.slop_attempts, self.failure = [], slop_attempts, failure
        self.budget = budget
        self.revisions = 0

    def post_json(self, **kwargs):
        self.budget.events.append("egress")
        packet = json.loads(kwargs["body"])
        self.calls.append(packet)
        if self.failure:
            raise ProviderEgressUnavailable("EGRESS_OUTCOME_UNKNOWN", retryable=False)
        role = packet["text"]["format"]["name"]
        data = json.loads(packet["input"])
        fact = data.get("approved_facts", [{"statement": "Our audience is startup founders."}])[0]
        if role == "signal_article_outline":
            output = {
                "sections": [
                    {"heading": "Audience", "point": fact["statement"], "fact_ids": [FACT_ID]}
                ]
            }
        elif role == "signal_article_critique":
            output = {key: {"score": 4, "issues": []} for key in RUBRIC}
        elif (
            role == "signal_claim_check"
            and "sentences" in packet["text"]["format"]["schema"]["properties"]
        ):
            output = {
                "sentences": [
                    {"path": path, "claims": [{"text": item["text"], "fact_ids": item["fact_ids"]}]}
                    for path, item in article_sentences(data["article"])
                ]
            }
        elif role == "signal_claim_check":
            output = {"claim_absent": False}
        else:
            text = fact["statement"]
            if role == "signal_article_revise":
                self.revisions += 1
                if self.revisions <= self.slop_attempts:
                    text = "Unlock seamless, robust growth."
            output = article(text)
        return ProviderEgressResponse(
            200,
            "application/json",
            json.dumps(
                {
                    "id": "resp_synthetic_0136",
                    "model": packet["model"],
                    "status": "completed",
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 40,
                        "total_tokens": 160,
                        "input_tokens_details": {"cached_tokens": 20},
                    },
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": json.dumps(output)}],
                        }
                    ],
                }
            ).encode(),
        )


def model_and_budget(**kwargs):
    budget = Budget()
    egress = SyntheticModel(budget, **kwargs)
    reasoner = BusinessBrainModelAdapter(Credential(budget.events), egress, budget=budget)
    return ContentWriterModelAdapter(reasoner), budget, egress


@pytest.mark.parametrize("role,effort", ROLE_EFFORTS.items())
def test_operator_routing_defaults_and_override(role, effort):
    assert ModelRoles().for_role(role) == RoleModel(effort=effort)
    env = {"SIGNAL_MODEL_" + role.upper() + "_EFFORT": "high"}
    assert ModelRoles.from_environment(env).for_role(role).effort == "high"
    model = RoleModel("operator-model", "medium", 200000, 20000, 1000000, 250000)
    config = ModelRoles(((role, model),))
    assert config.for_role(role) == model
    assert model.release != RoleModel().release


@pytest.mark.parametrize(
    "config",
    [(("unknown", RoleModel()),), (("page_type", RoleModel()), ("page_type", RoleModel()))],
)
def test_invalid_operator_roles_fail_closed(config):
    with pytest.raises(ValueError):
        ModelRoles(config)
    with pytest.raises(ValueError):
        ModelRoles.from_environment({"SIGNAL_MODEL_PAGE_TYPE_MODEL": "operator-model"})


@pytest.mark.parametrize("phrase", BANNED_PHRASES)
def test_banned_phrase_thresholds(phrase):
    report = quality_report([phrase + " belongs nowhere in this draft."])
    assert "banned_hits" in report["reasons"]
    assert report["metrics"]["banned_hits"] > THRESHOLDS["banned_hits"]
    assert quality_report(["The workshop starts with your existing notes."])["state"] == "passed"


def test_quality_metrics_and_language_limits():
    report = quality_report(["Perhaps it might possibly be useful in order to start."])
    assert {"hedge_density", "filler_density"} <= set(report["reasons"])
    repeated = quality_report(["This draft was crafted by an unknown person."] * 12)
    assert {"repeated_ngram_ratio", "sentence_length_variance", "passive_ratio"} <= set(
        repeated["reasons"]
    )
    assert quality_report(["word " * 90])["metrics"]["paragraph_words"] == 90
    assert quality_report(["word " * 90])["state"] == "low_quality"
    te = quality_report([CASES[3]["statement"]])
    assert te["language"] == "te" and te["metrics"]["readability"] is None
    assert te["language_limitations"]


def test_telugu_combining_marks_and_wrong_language_are_not_silently_qualified():
    assert len(tokens(CASES[3]["statement"])) == 3
    wrong = quality_report(["Our audience is startup founders."], language="te")
    assert wrong["state"] == "low_quality"
    assert wrong["reasons"] == ["target_language_mismatch"]
    assert quality_report([CASES[3]["statement"]], language="te")["state"] == "passed"


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
async def test_fixed_evaluation_suite_uses_four_passes_and_data_only_prompts(case):
    model, budget, egress = model_and_budget()
    result, quality, receipts = await draft_with_quality(model, evaluation_packet(case), uuid4())
    assert result.output["title"]["text"] == case["statement"]
    assert quality["state"] == "passed"
    assert [r["role"] for r in receipts] == [
        "article_outline",
        "article_draft",
        "article_critique",
        "article_revise",
    ]
    assert len(budget.calls) == len(budget.receipts) == 4
    assert budget.used == 4 * RoleModel().cost(ModelUsage(120, 40, 160, 20))
    assert budget.events[:5] == ["reserve", "key", "dispatch", "egress", "finish"]
    for call in egress.calls:
        assert call["model"] == "gpt-6-luna" and call["reasoning"]["effort"] == "high"
        assert call["instructions"].startswith(STYLE_PREFIX)
        assert case["topic"] not in call["instructions"]
        assert call["tools"] == [] and call["store"] is False and call["text"]["format"]["strict"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_attempts,expected,passes", [(0, "passed", 4), (1, "passed", 8), (2, "low_quality", 8)]
)
async def test_regenerate_once_and_low_quality_flag(bad_attempts, expected, passes):
    model, budget, _ = model_and_budget(slop_attempts=bad_attempts)
    _, quality, receipts = await draft_with_quality(model, evaluation_packet(CASES[0]), uuid4())
    assert quality["state"] == expected and len(receipts) == len(budget.calls) == passes
    assert quality["regenerations"] == min(bad_attempts, 1)
    if bad_attempts == 2:
        assert quality["reasons"] == ["banned_hits"]


@pytest.mark.anyio
async def test_budget_precedes_credentials_and_unknown_outcome_retains_hold():
    model, budget, egress = model_and_budget()
    budget.cap = 0
    with pytest.raises(MetadataDraftError, match="MODEL_BUDGET_EXHAUSTED"):
        await model.outline(evaluation_packet(CASES[0]), uuid4())
    assert not egress.calls and not budget.events
    model, budget, egress = model_and_budget(failure=True)
    operation_id = uuid4()
    with pytest.raises(MetadataDraftError, match="MODEL_UNAVAILABLE"):
        await model.outline(evaluation_packet(CASES[0]), operation_id)
    hold = budget.used
    assert hold > 0 and not budget.receipts
    with pytest.raises(MetadataDraftError, match="MODEL_OUTCOME_UNKNOWN"):
        await model.outline(evaluation_packet(CASES[0]), operation_id)
    assert len(egress.calls) == 1 and budget.used == hold


@pytest.mark.parametrize(
    "status,coverage,category,expected",
    [
        ("supported", "supported", "audience", "grounded"),
        ("unsupported", "supported", "audience", "owner_required"),
        ("uncertain", "supported", "audience", "owner_required"),
        ("supported", "uncertain", "audience", "owner_required"),
        *[
            ("supported", "supported", cat, "owner_required")
            for cat in ("pricing", "legal", "medical", "financial", "product_claim", "product")
        ],
    ],
)
def test_claim_by_claim_semantic_support_and_sensitive_exact_matching(
    status, coverage, category, expected
):
    a = article("We write for founders.")
    facts = [
        {"fact_id": FACT_ID, "category": category, "statement": "Our audience is startup founders."}
    ]
    reviews = {
        path: {
            "sentence": item["text"],
            "coverage": coverage,
            "claims": [{"text": item["text"], "fact_ids": [FACT_ID], "status": status}],
        }
        for path, item in article_sentences(a)
    }
    result = grounding_report(a, facts, selected_ids={FACT_ID}, claim_reviews=reviews)
    assert result["state"] == expected
    assert all(s["claims"][0]["fact_ids"] == [FACT_ID] for s in result["sentences"])
    assert (
        grounding_report(
            a, facts, selected_ids={FACT_ID}, claim_reviews=reviews, extra_flags=["title"]
        )["state"]
        == "owner_required"
    )
    assert (
        grounding_report(a, [], selected_ids={FACT_ID}, claim_reviews=reviews)["state"]
        == "owner_required"
    )
    if category != "audience":
        assert "UNSUPPORTED_SENTENCE" in result["sentences"][0]["reasons"]


def test_nonclaim_requires_verified_coverage_and_never_clears_existing_tags():
    a = article("Getting started", [])
    reviews = {
        path: {"sentence": item["text"], "coverage": "supported", "claims": []}
        for path, item in article_sentences(a)
    }
    assert grounding_report(a, [], selected_ids=set(), claim_reviews=reviews)["state"] == "grounded"
    assert grounding_report(a, [], selected_ids=set())["state"] == "owner_required"
    a["title"]["fact_ids"] = [FACT_ID]
    assert (
        grounding_report(a, [], selected_ids=set(), claim_reviews=reviews)["state"]
        == "owner_required"
    )


def test_claim_extraction_coverage_and_injection_are_closed():
    a = article("Our audience is founders.")
    output = {"sentences": [{"path": path, "claims": []} for path, _ in article_sentences(a)]}
    assert len(validate_claims(output, a)) == 4
    for changed in (
        dict(output, clear_flags=True),
        {"sentences": output["sentences"][:-1]},
        {"sentences": [output["sentences"][0]] * 4},
    ):
        with pytest.raises(ContentWriterRejected):
            validate_claims(changed, a)


def test_voice_priority_bounds_and_never_promotes_excerpts_to_facts():
    sources = [{"source_id": str(i), "text": "synthetic excerpt " * 100} for i in range(5)]
    voice = voice_packet(sources, chosen_ids=["0"], ranked_ids=["4", "3", "2", "1"])
    assert voice["origin"] == "gsc_clicks"
    assert [s["source_id"] for s in voice["untrusted_excerpts"]] == ["4", "3", "2"]
    assert all(len(s["text"]) <= 1000 for s in voice["untrusted_excerpts"])
    assert "approved_facts" not in voice
    assert voice_packet([], chosen_ids=[], profile={"tone": "plain"})["origin"] == "business_brain"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "noul,expected", [(0.0, "supported"), (1.0, "unsupported"), (0.5, "uncertain")]
)
async def test_jev_claim_entailment_and_fallback_cannot_clear_primary_flags(noul, expected):
    class Primary:
        async def evaluate(self, request):
            return DecisionEvaluation(
                "jev-1.13.0",
                {
                    "recommendation": ChoiceAnswer(
                        "ask_owner", {"ship": 0.0, "ask_owner": 1.0, "reject": 0.0}, 1.0
                    ),
                    "claim_absent": NoulAnswer(noul),
                },
                20,
                10,
            )

    class Recorder:
        def __init__(self):
            self.records = []

        def record(self, request, decision):
            self.records.append((request, decision))

    model, budget, egress = model_and_budget()
    fallback, recorder = _EditorialFallback(model), Recorder()
    claim = {"text": "We write for founders.", "fact_ids": [FACT_ID]}
    question = entailment_question(
        claim["text"], [claim], evaluation_packet(CASES[0])["approved_facts"], claim=claim
    )
    status, evidence = await _entailment(Primary(), recorder, fallback, question)
    assert status == expected
    assert len(recorder.records) == (2 if noul == 0.5 else 1)
    assert len(budget.calls) == (1 if noul == 0.5 else 0)
    if noul == 0.5:
        assert evidence["fallback"] is True
        assert egress.calls[0]["reasoning"] == {"effort": "high"}
    status, evidence = await _entailment(
        JevHttpAdapter(None), recorder, fallback, replace(question, decision_id=uuid4())
    )
    assert status == "supported" and evidence["fallback"] is True
