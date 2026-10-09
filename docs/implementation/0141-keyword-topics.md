# Slice 0141 - Keyword and Topic Ideas (G3)

Classification: Security-critical, reclassified from product because additive
owner/workload SQL projections and a budgeted model role touch existing safety
boundaries. No authority, egress profile, cap or secret access is widened.

## Implemented

- Retained work rebased above 0140 on main `856a996`. Migration `0091` follows `0090`,
  adding one immutable forced-RLS function-only idea table (177 app tables) and
  extending the 0136 role constraint. Frozen migrations/specifications remain
  unchanged; destructive downgrade remains disabled.
- `keyword_topics.py`: Unicode NFKC/casefold, Telugu-safe attached marks, sorted
  complete-link term/bigram clustering, explained membership and metric-independent
  IDs. One query cohort per source avoids double counting overlapping imports.
  Counts describe returned evidence, never complete-site totals. Average position
  is impression-weighted and nullable; ranking pages require a page dimension.
  Gaps use impressions >=100/position >=8 or absent lexical title overlap in an
  observed successful crawl. Missing inventory remains unassessed.
- 0077 snapshots expose topics/content opportunities with exact query/crawl
  evidence, existing approved-fact brief payloads and owner decisions. 0137
  effectiveness/decay remain intact. Existing 0123/0127/0139 delivery recipes,
  acceptance, grounding, approval, builds and delivery paths are unchanged.
- 0135 already imports final GSC query/page evidence. Its registered
  `strategy_rebuild` now includes topic gaps/current stored ideas, then existing
  `brief_proposals` applies the standing grant, draft work type and default-two/
  maximum-five cap. No duplicate stage/workflow, owner session, acceptance or
  external write was added. Worker source wrappers retain exact stage/handle checks.
- Optional owner-requested expansion uses a small `TopicIdeasModel` on 0136's
  `model_reasoning`: GPT-6 Luna/medium, operator-only routing, no tools, current
  owner/tenant/site/generation, persistent monthly budget and shared egress.
  Packet bounds: 20 clusters, 10 members each, 24 KB. Output bounds: 20 ideas,
  200 characters each, exact cluster IDs, no other fields. Inputs remain data.
  Ideas always carry `idea, no volume data`, null volume and model evidence.
  Optional actual volume is separate provider evidence, never a model estimate.
- Completed budget output digests bind immutable idea records; packets bind exact
  query generations. Changed evidence invalidates old expansion. Stable operation
  derivation prevents exact-snapshot dispatch/charge replay. Provider failure,
  malformed output and unknown outcomes retain holds and visibly fail unavailable.
- DataForSEO reuses 0076 storage/gateway/budget receipts without new paid calls.
  Only completed volume receipts for the current enabled credential generation
  qualify. Values carry language/location/date/evidence. Configuration without
  receipts is still unavailable. No scraping, estimates or new secret path.
- Owner `/topics` reuses the dashboard, strict parser, CSRF BFF, evidence links,
  paginated tables and existing accept/dismiss commands. Acceptance here creates
  an unaccepted 0077 brief proposal, not article acceptance/publishing authority.
  Historical snapshots and missing providers remain explicit.

See [ADR-0151](../adr/0151-evidence-only-keyword-topics.md).

## Verification

Focused development checks use actual disposable PostgreSQL and synthetic provider
doubles through shared egress. These are separate overlapping runs, not totals:

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/connectors/test_keyword_topics.py tests/connectors/test_seo_strategy_domain.py tests/connectors/test_observed_learning.py tests/api/test_seo_strategy.py -q` | 62 passed, 0 failed before final extra cohort assertion |
| `.venv/bin/python scripts/keyword_topics_lab.py -k 'optional_model or gateway_paid_call'` | 13 passed, 37 deselected, 0 failed |
| `.venv/bin/python scripts/keyword_topics_lab.py -k 'registry_uses_topics or topics_owner or optional_model'` | 11 passed, 41 deselected, 0 failed |
| `cd apps/dashboard && node --import tsx --test tests/keyword-topics.test.tsx tests/seo-strategy.test.tsx` | 15 passed, 0 failed |
| `npm run typecheck:dashboard` | Passed |
| `.venv/bin/python -m alembic -c database/alembic.ini heads` | Single head 0091 |

Coverage: row permutations, IDs/metrics/unrelated-growth stability, bridge
negatives, Telugu/English/mixed/join-control normalization, overlapping cohorts,
gap thresholds and missing dimensions/position/inventory, labels/closed output/
forgery/stale ideas/injection, configured volume projection/removal/failure,
budget exhaustion/unknown holds/replay, owner/non-owner/tenant/site/generation
denials, immutable private storage, and registry-driven capped unaccepted briefs
without decisions, approvals or writes. Development UUID/timeout failures were
fixed and are not qualification evidence. Labs clean their invocation-owned resources.

Full-page desktop 1440/mobile 390 CSS-pixel synthetic SSR captures with incumbent
CSS were inspected; frontend review disposition was ship, no material findings.
Changed-target detector: no findings. No authenticated/live journey is claimed.
Full-gate deferral and staged leak scan are recorded in
[0141 evidence](../evidence/0141-keyword-topics.json).

## Live Inputs and Limits

Live GSC/Bing/model/DataForSEO/Temporal owner journey, dedicated deployment and
production writes are NOT_EXECUTED. First live run requires an authorized
synthetic tenant/site, current owner MFA/session/recovery generation, verified
origin, completed crawl/artifact key/store, approved Business Brain facts, bound
GSC property with query-dimensional imports and scoped connections. Weekly use
also requires the existing reviewed release, human standing authorization,
explicit weekly work/volume/spend/Writer caps and qualified worker/Temporal
composition. Optional expansion needs the existing OpenBao model capability,
shared egress and operator-reviewed monthly cap/prices; optional real volumes
need a configured DataForSEO generation and completed 0076 volume receipts.
No credentials are committed.

Current Bing daily/top-page data have no query dimension; Bing topics/position
stay unavailable. Lexical targeting cannot prove semantic intent, site-wide
absence or traffic gains. Expansion is owner-requested, not an extra automatic
model job or paid weekly lookup. Briefs require existing human acceptance and
later approval/delivery gates. Screenshot evidence does not qualify interactions,
expanded disclosures or the authenticated shell.

## Infrastructure Stop

The full gate was invoked once with
`.venv/bin/python scripts/run-full-gate.py --range cc7f318..HEAD`.
Raw report: `.runtime/full-gate/run-lzdcpkkg/summary.json`, seven passing steps,
16 failed/incomplete steps, including cancellations (not 16 failed assertions).
The browser-worker image build failed downloading from `files.pythonhosted.org`
with `ReadTimeoutError`. Candidate-sandbox passed all 32 cases, but its global
candidate-label cleanup check reported `CANDIDATE_SANDBOX_CLEANUP_UNCONFIRMED`.
Shared candidate containers were left untouched; their creation times were checked.

The slice-owned OpenAPI mutation-list omission was corrected by adding only the
new bounded route; `.venv/bin/python -m pytest tests/api tests/identity
tests/tooling tests/connectors -q --junitxml=.runtime/0141-pytest-corrected.xml`
then passed 2896/0. An oversized optional packet cannot disable observed topics;
that regression is included in the corrected Python suite. Exact snapshot/source
pre-dispatch checking and its PostgreSQL stale-source assertion were added after
the earlier focused database runs and are qualified by the final full lab below.
The two in-flight delivery shards were stopped because source edits invalidated
their whole-control-plane fingerprints. No delivery code changed; the owner
directed that these shards not be rerun for this handoff.

Per the stop rule, the remaining gate was cancelled after infrastructure failure.
The runner recorded completed artifact cleanup for every step. Process inspection
confirmed the invocation's runner/shards exited, and its disposable PostgreSQL
containers were gone. Other tracks' subsequently created resources were untouched.
Ruff check/format (639 files), pip check, npm (313/0), authority-journal (16/0),
consumer (3/0), crawler-network (14/0), repository documentation checks and staged
gitleaks using unchanged main configuration passed. This historical full-gate run
did not pass and is not represented as completed.

## Merge Train Handoff

The owner reviewed the infrastructure failures: the global candidate-sandbox
cleanup check sees containers from parallel tracks 0140 and 0144, and PyPI timed
out once. These are not slice 0141 defects. Full-gate status is
**DEFERRED_TO_MERGE_TRAIN**; no full-gate rerun was performed. The merge train will
run it on the integrated branch and handle the later dashboard redesign rebase.

Final bounded requalification on 2026-10-04:

| Command | Result |
| --- | --- |
| `.venv/bin/python scripts/keyword_topics_lab.py` | 53 passed, 0 failed; all PostgreSQL cases, including exact-source pre-dispatch and oversized-input assertions |
| `cd apps/dashboard && npm test` | 267 passed, 0 failed, 0 skipped |
| `gitleaks protect --staged --config=.runtime/0141-main-gitleaks.toml --redact --no-banner` | Passed, 0 leaks; configuration extracted unchanged from `main:.gitleaks.toml` |

Delivery shards were skipped because no delivery code changed. The PostgreSQL
lab cleaned only its invocation-owned disposable resources. Other worktrees and
containers were untouched. Integrated commit-range scanning is deferred to the
merge train; this handoff uses the owner's requested staged scan. Live-provider
and deployment limits above remain NOT_EXECUTED.

## Merge Integration: Weekly Brief Cap Fixture

The first integrated database run exposed an obsolete 0135 fixture assumption:
`startup planning 0` through `startup planning 3` share two terms and correctly
form one 0141 topic. That is not a planner defect or a reason to bypass clustering.
The cap test now supplies four genuinely distinct observed queries and explicitly
asserts all four clusters before checking that only two unaccepted proposals exist.
Its cap, exact admitted payload, no acceptance/decision and replay assertions are
unchanged. The topic integration test additionally verifies its related-query
cluster has two members. No runtime behavior or safety limit changes.

Historical failing log: merge-train receipt directory,
`train4-43/database-before-topic-fixture-fix.log`. Requalification is recorded below.

## Merge Train 4

Rebased in the accepted order above main `856a996` (head 0089).
Migration `0091` follows `0090`; 177 cumulative app tables.
Frozen migrations 0001-0089, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2943 API/identity/tooling/connector
cases, 1250 PostgreSQL suite cases,
46 repository and 276 dashboard cases; Ruff check and format passed.
Main's Direction C presentation and unchanged design guards are retained.
Earlier qualification and deferrals above are historical. Integrated qualification
requires the final-tip 0138 full-gate receipt and PR comment; they supersede the
slice deferral only when every gate passes. Live-provider and production limits
remain unchanged.
