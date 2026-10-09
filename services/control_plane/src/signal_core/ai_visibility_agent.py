"""Deterministic citation gaps and owner-reviewed recommendations; no provider I/O."""

import hashlib
import re
from collections import defaultdict
from uuid import NAMESPACE_URL, UUID, uuid5

from signal_core.ai_visibility import parse_citations
from signal_core.business_brain import _session
from signal_core.content_writer import (
    ContentWriterRejected,
    ContentWriterUnavailable,
    grounding_report,
    originality_report,
    validate_brief,
)
from signal_core.database import _clean_transaction
from signal_core.decision_contracts import canonical_json

STRUCTURED_DATA_REVIEW = (
    "Accept this recommendation, then prepare a grounded change for Inbox review."
)
_STOP = frozenset("a an the what which how is are for to of in with and or does do".split())
VERSION = "ai-visibility-agent-v1"


def _words(text):
    return set(re.findall(r"[a-z0-9]+", text.casefold())) - _STOP


def relevant_pages(question, pages, *, manifest_id, origin):
    """Rank only that question version's crawl: token overlap, URL/id tie-breaks."""
    words = _words(question)
    ranked = []
    for page in pages:
        if page["manifest_id"] != manifest_id or page["output_truncated"]:
            continue
        # Same-origin crawl metadata is evidence, never a command or an off-site target.
        if not page["url"].startswith(origin.rstrip("/") + "/"):
            continue
        labels = " ".join([page["title"] or "", *[h["text"] for h in page["headings"]]])
        matched = sorted(words & _words(labels))
        if matched:
            ranked.append({**page, "score": len(matched), "matched_terms": matched})
    return sorted(ranked, key=lambda p: (-p["score"], p["url"], p["id"]))[:3]


def analyze_gaps(packet):
    """Keep latest coverage per provider separate from immutable observation history."""
    observations = defaultdict(list)
    for observation in packet["observations"]:
        parsed = None
        if observation["status"] == "complete" and observation["response"] is not None:
            parsed = parse_citations(
                observation["provider"], observation["response"], packet["origin"]
            )
        complete = parsed is not None and parsed.status == "complete"
        observations[observation["question_id"]].append(
            {
                "id": observation["id"],
                "question_id": observation["question_id"],
                "provider": observation["provider"],
                "model": observation["model"],
                "observed_at": observation["observed_at"],
                "provider_evidence_id": observation["provider_evidence_id"],
                "coverage": "complete" if complete else "incomplete",
                "site_cited": bool(parsed.cited_pages) if complete else None,
                "cited_pages": list(parsed.cited_pages) if complete else [],
                # URLs only, never answer text or citation titles.
                "competitor_pages": sorted(set(parsed.citations) - set(parsed.cited_pages))
                if complete
                else [],
                "failure_code": None
                if complete
                else (parsed.failure_code if parsed else observation["usage"].get("failure_code"))
                or "PROVIDER_EVIDENCE_UNAVAILABLE",
            }
        )
    gaps = []
    for question in packet["questions"]:
        history = sorted(
            observations[question["id"]], key=lambda o: (o["observed_at"], o["id"]), reverse=True
        )
        latest = {}
        for observation in history:
            latest.setdefault(observation["provider"], observation)
        pages = relevant_pages(
            question["question"],
            packet["pages"],
            manifest_id=question["crawl_manifest_id"],
            origin=packet["origin"],
        )
        gaps.append(
            {
                **question,
                "observations": [latest[key] for key in sorted(latest)],
                "history": history,
                "relevant_pages": pages,
            }
        )
    return gaps


def review_claims(claims, facts, research):
    """Reuse 0082's exact support and lexical-originality checks for proposal facts."""
    if not claims:
        return {"state": "owner_required", "sentences": []}, {"state": "unavailable"}
    article = {
        "title": claims[0],
        "meta_description": claims[0],
        "sections": [{"heading": claims[0], "sentences": claims}],
        "internal_links": [],
    }
    selected = {ref for claim in claims for ref in claim["fact_ids"]}
    return (
        grounding_report(article, facts, selected_ids=selected),
        originality_report(article, research),
    )


def _research(observation):
    """Use answer excerpts only as a no-copy corpus; never emit them in proposals."""
    response = observation["response"] or {}
    text = []
    if observation["provider"] == "perplexity":
        choices = response.get("choices", [])
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    text.append(message["content"])
    elif observation["provider"] == "gemini":
        for candidate in response.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
                continue
            for part in content["parts"]:
                if not isinstance(part, dict):
                    continue
                if isinstance(part.get("text"), str):
                    text.append(part["text"])
    else:
        for message in response.get("output", []):
            if not isinstance(message, dict):
                continue
            content = message.get("content", [])
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if isinstance(part.get("text"), str):
                    text.append(part["text"])
    return [{"source_id": observation["provider_evidence_id"], "text": " ".join(text)}]


def propose_for_gap(gap, packet):
    """Fixed rationale and exact approved facts; no model-generated factual assertions."""
    proposals = []
    raw = {o["id"]: o for o in packet["observations"]}
    for observation in gap["observations"]:
        if observation["coverage"] != "complete" or observation["site_cited"]:
            continue
        for page in gap["relevant_pages"][:1]:
            labels = " ".join([page["title"] or "", *[h["text"] for h in page["headings"]]])
            facts = sorted(
                (
                    f
                    for f in packet["facts"]
                    if f["category"] != "competitor" and _words(f["statement"]) & _words(labels)
                ),
                key=lambda f: f["fact_id"],
            )[:20]
            if not facts:
                continue
            claims = [{"text": f["statement"], "fact_ids": [f["fact_id"]]} for f in facts]
            grounding, originality = review_claims(
                claims, packet["facts"], _research(raw[observation["id"]])
            )
            state = (
                "rejected"
                if originality["state"] == "rejected"
                else ("ready" if grounding["state"] == "grounded" else "owner_required")
            )
            common = {
                "version": VERSION,
                "question_id": gap["id"],
                "question_set_id": gap["question_set_id"],
                "observation_id": observation["id"],
                "provider_evidence_id": observation["provider_evidence_id"],
                "provider": observation["provider"],
                "model": observation["model"],
                "observed_at": observation["observed_at"],
                "page_id": page["id"],
                "page_url": page["url"],
                "manifest_id": page["manifest_id"],
                "fact_ids": [f["fact_id"] for f in facts],
                "claims": claims,
                "grounding": grounding,
                "originality": originality,
                "state": state,
                "competitor_pages": observation["competitor_pages"],
                "rationale": "Review this crawl-relevant page against the citation gap. "
                "A change may earn citations; no improvement or causal effect is promised.",
            }
            # The 0082 evidence-proposal contract derives topic/query from the source title.
            if page["title"] and page["title"].strip():
                brief = validate_brief(
                    {
                        "topic": page["title"][:200],
                        "query": page["title"][:200],
                        "intent": "informational",
                        "kind": "content_refresh",
                        "source_ids": [page["id"]],
                        "fact_ids": common["fact_ids"],
                        "internal_links": [],
                    }
                )
                proposals.append({**common, "kind": "content", "brief": brief})
            lower = labels.casefold()
            schema_type = (
                "FAQPage"
                if "faq" in _words(lower) or "?" in labels
                else (
                    "HowTo"
                    if "how to" in lower
                    else (
                        "Product" if any(f["category"] == "product" for f in facts) else "Article"
                    )
                )
            )
            proposals.append(
                {
                    **common,
                    "kind": "structured_data",
                    "schema_type": schema_type,
                    "availability": "owner_review",
                    "reason": STRUCTURED_DATA_REVIEW,
                    "supporting_page_labels": labels,
                    "rationale": "Consider this schema type only if the visible page content "
                    "supports it. "
                    "Preparing a candidate still requires current approved facts "
                    "and a supported repository build.",
                }
            )
            applicable = [
                item
                for item in packet.get("broken_link_inputs", [])
                if item["manifest_id"] == page["manifest_id"]
                and item["target_url"] in page["internal_links"]
                and page["url"] == packet["origin"].rstrip("/") + "/"
            ]
            proposals.append(
                {
                    **common,
                    "kind": "internal_link",
                    "availability": "recommendation_only",
                    "reason": "No applicable broken-link recipe input established "
                    "by this citation gap.",
                    "rationale": "Review links to this crawl-relevant page. "
                    "A citation gap alone does not "
                    "establish a broken link or authorize a link edit.",
                    **(
                        {
                            "recipe_inputs": [
                                {"recipe_key": "technical_broken_link", **item}
                                for item in applicable[:8]
                            ],
                            "reason": "Existing broken-link inputs only; exact source, release, "
                            "build and Inbox checks still apply.",
                        }
                        if applicable
                        else {}
                    ),
                }
            )
    return proposals


def _call(connection, session_token, generation, site_id, name, *args):
    if name not in {"read", "propose", "decide"}:
        raise ValueError("Invalid visibility port.")
    token_hash, generation = _session(session_token, generation)
    with _clean_transaction(connection):
        result = connection.execute(
            f"SELECT control.ai_visibility_agent_{name}("
            + ",".join(["%s"] * (3 + len(args)))
            + ")",
            (token_hash, generation, site_id, *args),
        ).fetchone()[0]
    if (
        result is None
        or result == "denied"
        or isinstance(result, dict)
        and result.get("state") == "denied"
    ):
        raise ContentWriterUnavailable("owner_access_denied")
    return result


def reobservation_state(schedule, *, runtime_configured=False):
    if schedule is None:
        state, reason = "unavailable", "The observation schedule could not be checked."
    elif not schedule["enabled"]:
        state, reason = "paused", "Scheduled observations are turned off."
    elif not schedule["authority_current"]:
        state, reason = (
            "paused",
            "Scheduled observations are paused or need current owner authorization.",
        )
    elif schedule["cap_reached"]:
        state, reason = "cap_reached", "The observation spending cap has been reached."
    elif not runtime_configured:
        state, reason = "worker_unavailable", "The observation worker is unavailable."
    else:
        state, reason = "enabled", "Scheduled observations are enabled."
    return {"state": state, "reason": reason}


def read_agent(
    connection,
    *,
    session_token,
    generation,
    site_id,
    runtime_configured=False,
    recipe_configured=False,
):
    from signal_core.ai_visibility_recipe import recipe_availability, structured_context
    from signal_core.ai_visibility_schedule import read_visibility_schedule

    packet = _call(connection, session_token, generation, site_id, "read")
    schedule = read_visibility_schedule(
        connection, session_token=session_token, generation=generation, site_id=site_id
    )
    proposals = packet.get("proposals", [])
    for proposal in proposals:
        if proposal["kind"] == "structured_data":
            context = structured_context(
                connection,
                session_token=session_token,
                generation=generation,
                site_id=site_id,
                proposal_id=UUID(proposal["id"]),
                digest=proposal["digest"],
            )
            proposal["sealing"] = recipe_availability(context, configured=recipe_configured)
            # Historical immutable payloads remain intact; current readiness is a projection.
            proposal["payload"] = {**proposal["payload"], "reason": proposal["sealing"]["reason"]}
    return {
        "state": packet["state"],
        "gaps": analyze_gaps(packet) if packet["state"] == "available" else [],
        "proposals": proposals,
        "providers": [
            {
                "provider": provider,
                "state": "historical_observations_only"
                if any(o["provider"] == provider for o in packet.get("observations", []))
                else "unavailable",
                "reason": "Historical API observations only; new observations use the schedule.",
            }
            for provider in ("openai", "perplexity", "gemini")
        ],
        "reobservation": reobservation_state(schedule, runtime_configured=runtime_configured),
        "on_demand_observation": {
            "state": "unavailable",
            "reason": "No owner-triggered AI-visibility observation path yet.",
        },
        "comparison_wording": "Observed change; not evidence of causation. "
        "API answers can differ from consumer applications.",
    }


def prepare_proposals(connection, *, session_token, generation, site_id):
    packet = _call(connection, session_token, generation, site_id, "read")
    if packet["state"] != "available":
        return {"state": "unavailable", "reason": packet["state"]}
    count = 0
    for gap in analyze_gaps(packet):
        for payload in propose_for_gap(gap, packet):
            digest = hashlib.sha256(canonical_json(payload)).digest()
            proposal_id = uuid5(NAMESPACE_URL, f"{site_id}:{VERSION}:{digest.hex()}")
            outcome = _call(
                connection,
                session_token,
                generation,
                site_id,
                "propose",
                proposal_id,
                UUID(payload["observation_id"]),
                UUID(payload["page_id"]),
                payload["kind"],
                canonical_json(payload),
                digest,
            )
            if outcome not in {"proposed", "replayed"}:
                raise ContentWriterRejected(outcome)
            count += outcome == "proposed"
    return {"state": "prepared", "created": count}


def decide_proposal(
    connection, *, session_token, generation, site_id, proposal_id, digest, decision
):
    result = _call(
        connection,
        session_token,
        generation,
        site_id,
        "decide",
        proposal_id,
        bytes.fromhex(digest),
        decision,
    )
    if result["state"] not in {"accepted", "dismissed", "replayed"}:
        raise ContentWriterRejected(result["state"])
    return result
