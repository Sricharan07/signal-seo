from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from signal_core.ai_visibility_agent import reobservation_state
from signal_core.ai_visibility_recipe import (
    grounded_inputs,
    recipe_availability,
    seal_visibility_recipe,
)
from signal_core.content_writer import ContentWriterRejected
from signal_core.owner_ai_questions import edited_questions


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_question_edits_preserve_only_exact_crawl_provenance():
    page = str(uuid4())
    derived = [{"question": "What is Signal?", "source_evidence_id": page}]
    result = edited_questions(derived, ["What is Signal?", "What does Signal offer?"])
    assert result[0].source_kind == "crawl" and result[0].source_evidence_id == UUID(page)
    assert result[1].source_kind == "owner" and result[1].source_evidence_id is None
    assert edited_questions(derived, ["What does Signal offer?"])[0].source_kind == "owner"
    assert edited_questions(derived, ["what is Signal?"])[0].source_kind == "owner"


@pytest.mark.parametrize(
    "questions",
    [
        [],
        ["short"],
        ["x" * 513],
        ["What is Signal?", "what is Signal?"],
        [f"What is owner question {i}?" for i in range(11)],
        [f"What is question {i}?" for i in range(26)],
    ],
)
def test_invalid_questions_refuse_before_recording(questions):
    with pytest.raises(ValueError):
        edited_questions([], questions)


@pytest.mark.parametrize(
    "enabled,current,cap,runtime,state",
    [
        (False, False, False, False, "paused"),
        (True, False, False, True, "paused"),
        (True, True, True, True, "cap_reached"),
        (True, True, False, False, "worker_unavailable"),
        (True, True, False, True, "enabled"),
    ],
)
def test_schedule_readiness_is_real_not_a_slice_placeholder(enabled, current, cap, runtime, state):
    assert (
        reobservation_state(
            {"enabled": enabled, "authority_current": current, "cap_reached": cap},
            runtime_configured=runtime,
        )["state"]
        == state
    )
    assert "slice" not in reobservation_state(None)["reason"]


@pytest.mark.parametrize(
    "kind,fields",
    [
        ("Product", ["name", "description"]),
        ("Organization", ["name"]),
        ("Article", ["headline"]),
        ("FAQPage", ["question", "answer"]),
    ],
)
def test_structured_fields_are_only_exact_approved_claim_references(kind, fields):
    refs = {field: str(uuid4()) for field in fields}
    payload = {
        "schema_type": kind,
        "fact_ids": list(refs.values()),
        "claims": [{"text": field, "fact_ids": [ref]} for field, ref in refs.items()],
    }
    assert grounded_inputs(payload, refs)["@type"] == kind
    with pytest.raises(ContentWriterRejected):
        grounded_inputs(payload, {**refs, fields[0]: str(uuid4())})
    with pytest.raises(ValueError):
        grounded_inputs(payload, {**refs, "publish": str(uuid4())})


@pytest.mark.parametrize("state", ["proposal_unavailable", "facts_unavailable", "step_up_required"])
def test_missing_authority_or_facts_cannot_become_recipe_readiness(state):
    assert recipe_availability({"state": state}, configured=True)["state"] == state


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [False, True])
async def test_visibility_producer_passes_grounded_inputs_to_sealer_and_propagates_failure(
    monkeypatch, failure
):
    from signal_core import ai_visibility_recipe as service
    from signal_core.technical_seo_recipes import TechnicalRecipeUnavailable

    ref = str(uuid4())
    context = {
        "state": "available",
        "sealed": None,
        "input": {"report_id": str(uuid4()), "finding_id": str(uuid4())},
        "extension_id": str(uuid4()),
        "recipe_release_id": str(uuid4()),
        "payload": {
            "schema_type": "Product",
            "page_url": "https://example.test/",
            "fact_ids": [ref],
            "claims": [{"text": "Signal", "fact_ids": [ref]}],
        },
    }
    monkeypatch.setattr(service, "structured_context", lambda *a, **k: context)
    calls = []

    async def seal(identity, api, **kwargs):
        calls.append(kwargs)
        assert kwargs["brain_connection"] is api and kwargs["structured_data"] == {
            "@type": "Product",
            "fact_refs": {"name": ref},
        }
        assert (
            kwargs["recipe_key"] == "structured_data_grounded"
            and kwargs["source_path"] == "index.html"
        )
        if failure:
            raise TechnicalRecipeUnavailable("RECIPE_BUILD_FAILED")
        return SimpleNamespace(id=uuid4(), revision_sha256="a" * 64)

    monkeypatch.setattr(service, "seal_technical_recipe_revision", seal)
    kwargs = dict(
        session_token="t" * 43,
        generation="synthetic-generation",
        site_id=uuid4(),
        proposal_id=uuid4(),
        digest="a" * 64,
        fact_fields={"name": ref},
        credential=object(),
        github_transport=object(),
        runner=object(),
    )
    if failure:
        with pytest.raises(TechnicalRecipeUnavailable):
            await seal_visibility_recipe(object(), object(), **kwargs)
    else:
        assert (await seal_visibility_recipe(object(), object(), **kwargs))["state"] == "sealed"
    assert len(calls) == 1
