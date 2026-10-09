"""Bounded outline/draft/critique/revise with one quality regeneration."""

from dataclasses import asdict

from signal_core.content_writer import article_sentences
from signal_core.jev_decisions import _derived_operation_id
from signal_core.writing_quality import quality_report


def operation(parent, name):
    return _derived_operation_id(parent, "writing-0136:" + name)


def model_receipt(result, role):
    return {
        "role": role,
        "response_id": result.provider_response_id,
        "model": result.model_reported,
        "model_requested": result.model_requested,
        "release": result.release,
        "effort": result.effort,
        "usage": asdict(result.usage),
        "request_sha256": result.request_sha256,
        "output_sha256": result.output_sha256,
    }


async def draft_with_quality(model, packet, draft_id):
    receipts, attempts = [], []
    for attempt in range(2):
        prefix = str(attempt)
        outline = await model.outline(packet, operation(draft_id, prefix + ":outline"))
        receipts.append(model_receipt(outline, "article_outline"))
        draft = await model.draft_article(
            {**packet, "untrusted_outline": outline.output}, operation(draft_id, prefix + ":draft")
        )
        receipts.append(model_receipt(draft, "article_draft"))
        critique = await model.critique(
            {**packet, "untrusted_draft": draft.output}, operation(draft_id, prefix + ":critique")
        )
        receipts.append(model_receipt(critique, "article_critique"))
        result = await model.revise(
            {**packet, "untrusted_draft": draft.output, "untrusted_critique": critique.output},
            operation(draft_id, prefix + ":revise"),
        )
        receipts.append(model_receipt(result, "article_revise"))
        quality = quality_report(
            [item["text"] for _, item in article_sentences(result.output)],
            language=packet["voice_examples"]["target_language"],
        )
        attempts.append(quality)
        if quality["state"] == "passed":
            break
        packet = {**packet, "quality_reasons": quality["reasons"]}
    return result, {**quality, "attempts": attempts, "regenerations": len(attempts) - 1}, receipts
