# Slice 0073 - Business Brain

Classification: Product. This slice uses existing owner/session authorization and
does not add external writes, egress, secrets, or autonomy.

## Implemented boundary

- `0056_business_brain` adds forced-RLS, function-only, append-only fact, event,
  voice-profile, and audit tables. Facts are tenant/site scoped and retain page
  evidence, brand-document range, or owner-membership provenance.
- Facts begin as `proposed`; approval, correction (as an explicitly owner-made
  approved successor), and removal append history. The approved query excludes
  proposed, superseded, and removed records.
- Follow-up migration `0057` preserves `0056`, binds page provenance to the actual
  0066 `crawl_page_records`, and adds forced-RLS, immutable extraction intents and
  result receipts. The merge owner may renumber this linear branch chain.
- `business_brain_extraction.py` reads integrity-checked encrypted crawl bodies
  or exact character ranges from the screened 0072 document port. Both inputs
  remain untrusted data. The typed page-type Choice uses `DecisionService` and
  `PostgresDecisionRecorder`; Jev unavailability invokes the labelled
  `openai_fallback`, not an invented page type. The reasoner role in
  `model_reasoning.py` uses only the existing OpenAI shared-egress profile,
  tool-less strict JSON-schema responses, and closed candidate validation.
- Intents are unique per tenant/site, source, document range, and extraction
  version. Completion atomically seals the output digest and proposed facts,
  each with the exact source and decision id. Replays do not contact providers
  or duplicate facts. Failed results stay failed; an unresolved dispatch stays
  `outcome_unknown` and cannot trigger a blind retry. No category auto-approves,
  including pricing, legal, medical, financial, and product claims.
- Competitors are a first-class provenance-bearing category. Voice updates
  append a new profile with an exact current-profile supersession check and an
  audit record. Owner corrections cite the correcting membership and retain
  their predecessor rather than falsely attributing corrected text to a source.
- `apps/api` exposes owner-scoped facts/status, provenance, approved facts,
  approve/correct/remove, manual statements, voice read/update, extraction, and
  extraction status. Mutations use existing same-origin session/CSRF checks and
  current database owner authorization, with append-only audit records. Internal
  product edits grant no authority and do not introduce a new step-up grant.
- The dashboard Business Brain destination groups facts by status/category,
  links exact provenance, offers owner edits and versioned voice, and displays
  unavailable extraction without disabling manual facts. The local pilot wires
  the existing session gateway, with extraction intentionally unconfigured.

## Authority and composition

This remains Product: it consumes existing owner/session authority, the existing
artifact key, the existing model credential port, decision-recorder role, and
shared-egress profiles without widening any grant or network profile. It adds
no repository/customer external writes or new secret paths. ADR-0085 records
the source/decision/approval separation.

Live extraction needs an explicitly composed `BusinessBrainExtractor`, existing
OpenBao model credentials, a qualified shared-egress gateway, the existing
encrypted artifact store/key, and the existing scoped workflow decision-record
connection factory. Missing model composition returns `MODEL_UNCONFIGURED`
before any extraction or fact write. Missing/withheld source data fails closed.

## Verification

Run `./.venv/bin/python scripts/run-database-tests.py` for the disposable real
PostgreSQL lab. The focused coverage includes provenance, approval, supersession,
removal, different-tenant and different-site forced-RLS/function-only negatives,
every non-owner mutation, page and document injection data, sensitive categories,
real-source extraction and replay, invalid output, ambiguous dispatch, and labelled
Jev fallback through provider doubles behind the real shared gateway. API and
dashboard tests cover strict schemas, browser mutation proofs, unauthorized
roles, provenance destinations, owner controls, and visible unavailable state.

Live Jev and frontier-model extraction are `NOT_EXECUTED`: no provider credential
or production composition is configured. Manual owner statements continue to use
the local record boundary. Full gate commands/counts are recorded in
[the evidence](../evidence/0073-business-brain.json); disposable lab reports are
retained under `.runtime/` and all invocation-owned resources are cleaned up.
