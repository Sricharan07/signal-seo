# Slice 0096: AI-Visibility Optimization Agent

Classification: Product. No new authority, provider, secret, egress or external
write. Based on main `4f3aca5`, independent of 0077, 0093 and 0094.

## Implemented

- Deterministic per-question-version gaps reparse immutable 0074 responses using
  0075's citation parsers. Each provider's newest observation retains model,
  date, coverage and evidence. Older observations remain inspectable. A missing
  or malformed provider observation is incomplete, not "not cited".
- Other cited page URLs come only from structured parsed citations. Crawl title
  and heading token overlap ranks up to three same-version site pages, using
  stable URL/id tie-breaks. No external reads or competitor content copying.
- Owner-triggered preparation creates immutable evidence-linked recommendations
  for the highest-ranked page of each uncited complete provider observation.
  Selected current approved facts must share a page-label term. Fixed rationale
  asserts no factual business claims or improvement guarantees; supporting
  factual statements are exact approved facts with IDs.
- Reused 0082 grounding and originality reject unsupported/stale/sensitive
  assertions and verbatim/reordered lexical copying from answer research excerpts.
  Models do not draft this bounded implementation; no new model prompt is added.
- Content refresh recommendations create 0082 evidence-proposal briefs, unaccepted
  until owner acceptance. Acceptance calls the existing brief acceptance function
  atomically with the exact proposal-digest decision. No draft, candidate or
  external operation is automatically dispatched. Dismissal leaves a brief
  proposed; the independent Content Writer owner controls remain available.
- Structured-data recommendations are not candidates. They name one allowed
  type, exact page, supporting crawl labels and approved facts, and display
  `unavailable: needs a structured-data recipe extension (slice 0131)`.
- Internal-link suggestions are recommendations. Same-manifest existing broken
  homepage link findings may supply exact 0081 inputs, linked to Inbox; every
  usual source/release/build check still applies. Acknowledgment is not approval.
- Scheduled re-observation is unavailable with
  `no AI-visibility scheduler yet (slice 0131)`. On-demand observation is also
  unavailable because 0075 has no owner observation route. History says
  "Observed change", with no causal claims and API-versus-consumer caveat.
- Owner API, strict same-origin BFF and dashboard expose gaps, evidence, approved
  facts, flags, acceptance/dismissal and history. Provider projections distinguish
  historical evidence from unavailable current observation composition.

Migration `0078` follows `0077` in the accepted merge train.
Two immutable forced-RLS tables use existing owner/session/site/recovery checks
and `signal_api` writer connections. Read coverage exceeding 500 questions,
500 observations or 1,500 proposals is explicitly unavailable, never truncated
into a false complete result. No previous migration or specification is edited.
See [ADR-0123](../adr/0123-proposal-only-ai-visibility-agent.md).

## Qualification

**Locally qualified product slice.** One database run passed 892 tests but failed
the unchanged `test_real_sources_shared_egress_injection_and_replay[True-page_evidence]`
at its source crawl, before any 0096 path, with `RobotsSnapshotUnavailable`.
The exact exception is `signal_core.crawl_robots.RobotsSnapshotUnavailable`, raised
at `services/control_plane/src/signal_core/crawl_robots.py:307` from the unchanged
test's source-crawl call at `tests/control_plane/test_business_brain_extraction.py:254`.
Diff review confirms no changes to that test's shared fixtures, `conftest`, robots,
admission, provider-fetcher or source-crawl modules. The owner identifies it as a
known load-sensitive pre-existing test: it waits 1.1 seconds for shared admission
cooldown and depends on robots evidence while parallel tracks load this machine.
Treat this as a suspected load flake, not a proven 0096 defect. An earlier full
database run passed 893 tests; the owner-requested unchanged rerun also passed all
893, including all four extraction-test variants. No test or safety gate was weakened.
The initially stopped delivery run had 15 passes, removed its two owned containers,
and raised `KeyError` during interrupted JUnit summarization. The unchanged,
unsharded delivery rerun passed all 36 cases and completed owned cleanup.

All 17 full gate commands passed: 2,868 test cases and 23 OpenBao checks, zero
failures in the final qualification runs. Staged-diff and pre-commit validation
commit-range secret scans passed with zero findings using the unchanged main
configuration; the final branch range is scanned again before push. All 19 new
PostgreSQL cases and 1,682 API/identity/tooling/connector
cases passed; npm passed 43 repository and 159 dashboard tests plus docs,
typecheck and build. Remaining completed labs passed 91 cases and OpenBao 23
checks. Desktop/mobile synthetic component checks and the finish/documentation
review passed; authenticated and deployed qualification remain NOT_EXECUTED.

The full gate commands and counts are recorded in
[the evidence](../evidence/0096-ai-visibility-agent.json). Real PostgreSQL tests
cover citation evidence behind the shared gateway, proposed/accepted briefs,
replay/conflict, stale facts, role/site/tenant/recovery negatives and no candidates
or external writes. Deterministic tests cover all citation parsers, crawl-version
ranking, incomplete latest observations, unsupported facts and copied research.
API/BFF tests cover closed schema, CSRF, unconfigured ports and role composition.
Dashboard tests cover honest empty/provider/0131 states, evidence, history and
escaping; visual fixtures are synthetic and never production data.

## Live Inputs And Limits

Live OpenAI, Perplexity and Gemini remain **NOT_EXECUTED**. First live use needs
an existing current owner session, verified site, completed crawl/artifact
resources, approved Business Brain facts, and real 0074/0075 observation records
under the previously qualified provider/shared-egress and spend boundaries.
Configure the API's visibility port with the existing writer/API connection
factory; production and dedicated-test deployment are **NOT_EXECUTED**. The
disposable local pilot wires records, not live observation or model availability.

No new-article model generation, semantic plagiarism certificate, broader 0081
schema patch, automatic PR, scheduler, measurement integration or R4 release is
claimed. Structured-data and scheduling foundations wait for separate slice 0131.

## Merge Train 2

Rebased above merged Train 1 (main `7754746`) in the accepted PR order.
Migration `0078` follows `0077`; 154 cumulative app tables.
Migrations 0001-0070 and the 1200-second aggregate database budget are unchanged.
Fast checks passed: 2158 API/identity/tooling/connectors, 1041 PostgreSQL,
43 repository and 227 dashboard cases; Ruff check and format passed.
The earlier qualification above is historical; final-stack full qualification is
recorded separately in the PR comments. No live or production authority is added.

Restacked above #24 measurement integration fix `381306f`.
The fast counts above qualify this restacked source; the original commits, authors
and messages are preserved. Final-tip full qualification is recorded in the PR comments.

Also includes #24 test-only generation-order correction `9a8a85f`: explicit
fixture timestamps and equal-timestamp UUID tie coverage; production selection unchanged.
