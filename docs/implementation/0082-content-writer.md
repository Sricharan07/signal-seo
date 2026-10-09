# Slice 0082 - Content Writer

Classification: Product. Existing owner/session, model-egress, GitHub read and
candidate-sandbox boundaries are reused without enlarging authority or grants.
There are no repository writes, new secret paths or new egress profiles.

## Implemented boundary

- Migration `0059`, down `0058`, adds eight tenant/site-scoped forced-RLS tables
  with function-only runtime access and immutable history. Briefs retain intent,
  topic, owner-entered/evidence-derived query, completed crawl evidence ids,
  current approved fact ids and only crawled internal URLs. Evidence proposals
  remain proposed until owner acceptance; revisions supersede rather than edit.
- `ContentWriterModelAdapter` uses the existing OpenAI reasoner/shared-egress
  boundary, strict closed JSON output, no tools and no storage. Brand voice is
  style input; briefs and source text remain untrusted data. Missing provider
  composition is visibly unavailable before a draft intent or provider call.
- Each factual field is a single tagged sentence. The current approved-facts
  query supplies the immutable selected-fact snapshot. Exact normalized support
  is fail-closed: untagged/unsupported/stale facts, sensitive categories and
  competitor names produce exact-sentence owner flags. Jev Noul decisions are
  persisted through `DecisionService`/`PostgresDecisionRecorder`; uncertain or
  unavailable Jev records labelled frontier fallback. Neither model may clear a
  deterministic escalation, even when its response claims support.
- Originality evidence compares all available completed site crawl page bodies,
  including selected research sources, using eight-word overlap (reject at 0.20)
  and reordered twelve-content-word windows (reject at 0.60 Jaccard). Copying
  and close lexical paraphrase reject the draft. Missing, secret-like, over-24k
  source text or over-32-page coverage stays unavailable rather than silently
  skipping sources. This bounded subset is not semantic plagiarism certification.
- New articles and refreshes share a rolling-seven-day cap: default two, maximum
  five. The owner may adjust within the maximum. An atomic scope-locked intent
  consumes a slot before dispatch, including failed/unknown outcomes. Replays
  cannot create another draft or consume another slot.
- Plain Eleventy HTML is supported. A new article is exactly one new HTML file
  beside the owner-selected existing content file; a refresh changes only that
  selected file's single article region, title and description, preserving its
  shell. No required index is inherent in this supported format. Templates,
  ambiguous regions, protected paths, bulk generation and other formats remain
  unavailable or rejected. All generated text is HTML-escaped.
- Existing GitHub read inspection and protected no-network candidate builds
  precede sealing. The canonical revision contains one exact diff, source/result
  digests, base/patch, passed build receipt, grounding and originality evidence,
  A2/high threshold and `autonomy_eligible=false`. Database ports retain their
  existing API/identity roles through separate composition factories.
- The owner API exposes briefs/acceptance, cap, draft, seal, exact revision review
  and current status behind existing session/CSRF authorization. Every mutation
  has current database owner checks and an append-only audit. Internal editing
  and editorial review introduce no authority grant or new step-up operation.
- Dashboard Content Writer shows briefs, structured drafts, linked fact/source
  provenance, exact flagged sentences, originality, caps, unavailable providers
  and sealed-candidate links. The existing Inbox destination also renders these
  sealed article revisions for exact approval/rejection/change requests. This
  editorial decision does not feed the technical PR dispatcher or autonomy registry.

## Verification and live inputs

Run `.venv/bin/python scripts/run-database-tests.py` for real PostgreSQL source,
decision, draft, cap and candidate/review composition. Coverage includes current
support, sensitive/unsupported/stale/removed facts, competitor names, injection,
model attempts to clear flags, copied/paraphrased/original text, crawled-link
limits, unsupported formats, one-file refresh, cap exhaustion, replay, provider
unavailability, failed builds, different tenant/site RLS and every non-owner role.
API tests cover all mutations, strict ingress, CSRF, owner negatives and correct
database-role composition. Dashboard tests cover unavailable/cap/grounding/Inbox
rendering, escaping and bounded same-origin command relay.

Full commands and actual counts are in [the evidence](../evidence/0082-content-writer.json).
The mainline rebase onto `fdb0f00` preserves Slack approvals alongside Content
Writer. Migration `0059` follows Slack's `0058`; migrations `0001` through `0058`
are unchanged. Database expectations now cover 100 forced-RLS tables and head
`0059`. The completed rebased full gate passed 2,283 tests with zero failures,
including 820 real-PostgreSQL cases; static, build and secret checks passed.
Live model, Jev, customer GitHub/source qualification, production composition and
authenticated owner journey remain `NOT_EXECUTED`. First live use needs an active
owner session, completed verified-origin crawl and existing artifact store/key,
approved Business Brain facts (optional voice), existing OpenBao model/optional
Jev credentials, qualified shared gateway/decision recorder, a current selected
GitHub PR extension for plain Eleventy HTML, and the existing isolated sandbox
runner. API writer composition must use the existing `signal_api` connection;
GitHub/build composition retains the existing `signal_identity` connection.
The local pilot wires manual writer records but leaves model/candidate unavailable.

Articles are not autonomy-eligible and do not publish. The 0104 registry and later
owner-reviewed article dispatch integration are outside this slice.
