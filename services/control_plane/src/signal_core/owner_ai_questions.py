"""Owner-edited questions are bounded data, not model or publishing instructions."""

from dataclasses import asdict
from uuid import UUID

from signal_core.ai_visibility import (
    MAX_OWNER_QUESTIONS,
    MAX_QUESTIONS_PER_SET,
    AiVisibilityUnavailable,
    TargetQuestion,
    _question,
    derive_target_questions,
    load_crawl_question_sources,
    record_target_question_set,
)
from signal_core.business_brain import _session
from signal_core.content_writer import ContentWriterRejected, ContentWriterUnavailable
from signal_core.database import _clean_transaction


def question_context(connection, *, session_token, generation, site_id):
    token_hash, generation = _session(session_token, generation)
    with _clean_transaction(connection):
        context = connection.execute(
            "SELECT control.ai_visibility_owner_context(%s,%s,%s)",
            (token_hash, generation, site_id),
        ).fetchone()[0]
    if context is None:
        raise ContentWriterUnavailable("owner_access_denied")
    return context


def propose_questions(connection, *, session_token, generation, site_id):
    context = question_context(
        connection, session_token=session_token, generation=generation, site_id=site_id
    )
    if context["state"] != "available":
        return {
            "state": "unavailable",
            "reason": "A committed crawl of the verified site is required.",
        }
    try:
        sources = load_crawl_question_sources(
            connection,
            tenant_id=UUID(context["tenant_id"]),
            site_id=site_id,
            crawl_manifest_id=UUID(context["crawl_manifest_id"]),
            session_token=session_token,
            generation=generation,
        )
        questions = derive_target_questions(sources, ())
    except (AiVisibilityUnavailable, ValueError):
        return {
            "state": "unavailable",
            "reason": "The latest crawl has no usable question sources.",
        }
    return {
        "state": "proposed",
        "crawl_manifest_id": context["crawl_manifest_id"],
        "supersedes_id": context["supersedes_id"],
        "questions": [
            {**asdict(q), "source_evidence_id": str(q.source_evidence_id)} for q in questions
        ],
    }


def edited_questions(proposed, values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_QUESTIONS_PER_SET:
        raise ValueError("Choose between one and 25 questions.")
    derived = {q["question"]: q for q in proposed}
    result, seen = [], set()
    for value in values:
        clean = _question(value)
        key = clean.casefold()
        if key in seen:
            raise ValueError("Questions must be distinct.")
        seen.add(key)
        source = derived.get(clean)
        result.append(
            TargetQuestion(
                clean,
                "crawl" if source else "owner",
                UUID(source["source_evidence_id"]) if source else None,
            )
        )
    if sum(q.source_kind == "owner" for q in result) > MAX_OWNER_QUESTIONS:
        raise ValueError("Choose at most ten added or edited questions.")
    return tuple(result)


def approve_questions(
    connection,
    *,
    session_token,
    generation,
    site_id,
    request_id,
    crawl_manifest_id,
    supersedes_id,
    questions,
):
    proposal = propose_questions(
        connection, session_token=session_token, generation=generation, site_id=site_id
    )
    if proposal["state"] != "proposed" or proposal["crawl_manifest_id"] != str(crawl_manifest_id):
        raise ContentWriterRejected("crawl_changed")
    selected = edited_questions(proposal["questions"], questions)
    context = question_context(
        connection, session_token=session_token, generation=generation, site_id=site_id
    )
    recorded = record_target_question_set(
        connection,
        tenant_id=UUID(context["tenant_id"]),
        site_id=site_id,
        crawl_manifest_id=crawl_manifest_id,
        questions=selected,
        session_token=session_token,
        generation=generation,
        request_id=request_id,
        supersedes_id=supersedes_id,
    )
    return {"state": "approved", "question_set_id": str(recorded)}
