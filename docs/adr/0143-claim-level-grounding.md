# ADR-0143: Claim-Level Grounding

Status: Accepted for the internal Content Writer boundary; live NOT_EXECUTED.
Date: 2026-10-03.
Classification: Security-critical.

## Decision

Replace exact whole-statement matching for nonsensitive Content Writer text
with C2 claim-level entailment. Include every sentence, title, description,
heading and internal-link anchor. A closed extraction response must cover each
exact field path once, with bounded atomic claims and candidate approved-fact
IDs. Tags proposed by the writer or extractor are not proof.

For each field, persist a Jev Noul coverage decision that asks whether extraction
omitted any factual assertion. For every claim, ask whether any detail is absent
from its supporting, current-approved facts. Noul at most 0.05 means supported;
at least 0.95 means unsupported; the middle remains uncertain. An unavailable
primary may use explicitly labelled GPT-6 Luna, high-effort entailment fallback.
A primary uncertainty remains flagged even if an additional fallback claims
support. Unavailable fallback remains uncertain. Every recommendation has an
owner-only ceiling and immutable decision provenance, never execution authority.

Nonfactual sentences are allowed only with a complete, supported empty-claim
coverage review. Store per-claim text, citations, status, decision IDs, provider
and fallback labels in the immutable draft. Recheck cited facts before sealing.
Unsupported, uncertain, omitted, nonexistent, stale or unselected facts require
the owner. Earlier deterministic flags are unioned, never cleared by a model
(INV-030). Historical drafts without inference receipts retain exact matching.

Pricing, legal, medical, financial and product claims retain exact normalized
approved-statement matching and always require the owner, even if entailment
reports support. Competitor flags and originality rejection remain in force.
Current authorization and selected facts come from scoped deterministic database
ports, not source text, model confidence or stale brief snapshots.

## Consequences

This permits grounded paraphrases without pretending model output is an approved
fact. Claim extraction and entailment are fallible; missing coverage is explicit,
not assumed away. Source and brief injection cannot alter the system prompt,
schema, model routing, cost cap, tool policy, current fact registry or authority.
Synthetic doubles qualify orchestration and flags, not live semantic accuracy.

Article revisions remain A2, `autonomy_eligible=false`, and owner reviewed.
No model can mint authority, merge, deploy or publish. Live Jev, live model,
authenticated owner journey and production composition remain NOT_EXECUTED.
See [0136 implementation](../implementation/0136-writing-quality.md).
