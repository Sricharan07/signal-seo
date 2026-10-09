# Slice 0140: Proactive Internal Linking

Classification: **security-critical**, new candidate-write recipe and workload
composition. Base `cc7f318`; Revision 4.0 sections 4, 6, 16, 19 and unchanged
Revision 3.2 sections 2, 8, 10, 12 apply. Revision 4.1 is unchanged.

## Boundary

Implemented internal boundary; focused qualification passed. Full gate is
**DEFERRED_TO_MERGE_TRAIN** by reviewer direction because concurrent slice 0144
Temporal development server/labs caused shared-machine infrastructure collisions.
Live providers,
deployed employee composition, real repository builds and owner PR/live-site
journey are **NOT_EXECUTED**. No production readiness or publishing authority is
claimed. [ADR-0149](../adr/0149-reviewed-contextual-internal-links.md) records the
recipe; [ADR-0150](../adr/0150-weekly-internal-link-candidate-port.md) records the
existing-standing-authority composition.

The graph uses latest committed 0066 crawl evidence with successful nontruncated
pages on the verified HTTPS origin. Incoming counts deduplicate referring pages.
Zero and one incoming pages receive candidates only with at least two shared
salient title/heading terms. Token counts, bounded vocabulary, tie breaks and
source/target ordering are deterministic. Coverage travels with the evidence;
"no incoming observed" is not a claim of complete-site orphan detection. No
external inventory, Business Brain inference or new model service is invented.

Each `technical_internal_link_add` candidate wraps exactly one existing two- or
three-word phrase in one plain-text main/article paragraph. No new anchor text
is generated. Only directly mapped Eleventy HTML is supported; nav/footer/header,
aside, hidden content, labelled or repeated boilerplate, templates, nested markup,
source drift, existing targets, reciprocal links and repeated exact anchors are
refused. All site candidate anchors and current checkout HTML anchors are checked.
The default cap is three candidates per source URL per UTC week, reducible to one
or two; rejected proposals still count. Repeated crawls cannot reset it.

The signed reviewed release is `owner_review`, `autonomy_eligible=false`. The
0104 five-key A2 set is untouched. Candidates reuse 0081 source patch/preflight,
0080 offline sandbox and existing baseline/candidate build receipts. Baseline
output equals crawl/source bytes; candidate output equals the single-wrapper
result, with all other output artifacts unchanged. Exact current source and
paragraph proof are reconstructed by the existing PR patch path. Existing live
verification requires the complete sealed result digest. Failed, missing or
ambiguous builds cannot seal or dispatch.

Strategy 0077 shows source/target/manifest evidence, incoming count, shared terms,
coverage and an existing Inbox action once sealed. The strict API and dashboard
contracts retain the before/after diff and owner review. The existing conservative
unknown-recipe risk policy remains in force, including fresh-MFA dashboard
approval; no risk threshold or chat allowance is relaxed.

## Weekly Composition

0135 registers `internal_link_proposals` as `draft_patch` after successful strategy
rebuild. Admission uses existing human A1 standing scope and budget reservations,
not link-release autonomy. An at-most-32-unit plan pins source/target resources,
manifest, binding/extension, reviewed release and deterministic build/revision
keys. Both resource exclusions, current grant, epochs, pause and generation are
checked at admission and before every leaf I/O. Closed workflow-only read/build
and seal ports cannot approve or prepare a PR. No human session is manufactured.
The configured single-site operator wires the candidate port; defaults honestly
report it unconfigured. Existing unknown-intent handling, weekly report delivery,
writing quality, built adapters and 0137 learning projections remain intact.

Migration `0090` follows main's `0089`. It adds one forced-RLS, immutable,
function-only candidate accounting table (176 cumulative app tables), narrow
owner/workload ports and the eighth skill projection. Existing migrations and
hash-protected specifications are untouched. Destructive downgrade is refused;
recovery is reviewed roll-forward.

## Verification

Focused tests cover graph counts/determinism, orphan/weak opportunities,
salient-term SQL/Python parity, static paragraph/anchor limits, caps, off-site
refusal, boilerplate/duplicates/stuffing, exact all-artifact build assertions,
PR reconstruction and live digest failures. Real PostgreSQL and shared-egress
provider doubles cover committed sources, reviewed release, candidate sealing,
replay, role/tenant/site/generation negatives, owner approval, weekly admission,
exact-resource denials, pause and budget-backed candidate-only behavior.

Focused tooling/API qualification passed 96 cases. Focused real PostgreSQL
candidate/weekly qualification passed 21 cases. Dashboard type checking passed.
These checks passed since the final source change and were not rerun on resume.
The reviewer-directed outstanding PostgreSQL qualification passed another 16
cases: eight candidate failure paths and eight migration/head/RLS/backfill checks
on disposable PostgreSQL 17.11 upgraded to 0090. The invocation removed only its
owned database and network. No Temporal server was started. The dashboard suite
(`cd apps/dashboard && npm test`) passed 263 cases, zero failures/skips. Focused
Python totals are 133 passed, zero failed; dashboard totals are 263 passed.

The full gate was run once:

```sh
.venv/bin/python scripts/run-full-gate.py --range cc7f318..HEAD --jobs 4 --docker-jobs 2 --database-shards 3
```

Six steps passed: npm (309 cases), API/identity/tooling/connectors (2,916 cases),
ruff check, ruff format, pip check and authority journal (16 checks). Both delivery
shards failed in the existing article-delivery measurement replay tests because
the local Temporal server could not start within five seconds (ConnectionRefused).
They passed 57 and 56 cases respectively before those infrastructure failures.
The remaining 15 steps were cancelled under the stop rule; the runner labels
cancelled steps FAIL, not additional test failures. All counted test cases total
3,338 passed and two failed, with 19 passed checks. Neither delivery shard passed.
The reviewer identified a concurrent slice 0144 Temporal/lab collision and directed
that the full gate not be rerun here. The integrated merge train will run it alone;
full-gate qualification remains **DEFERRED_TO_MERGE_TRAIN**.

Exact step commands and counts are in [evidence](../evidence/0140-internal-links.json).
Private invocation logs and summary remain under `.runtime/full-gate/run-79jmj8og`.
The runner exited and removed its owned runtime artifacts; unrelated shared
containers were left untouched. No gate processes or candidate/build containers
remain running. A staged leak scan using main's unchanged config passed with zero
leaks. Commit and PR handoff proceed under the reviewer-directed bounded gate,
without claiming integrated qualification. Live provider runs remain NOT_EXECUTED.
The merge train owns rebasing onto the newer main and adapting the weekly-report
label to its redesigned presentation; this slice does not change another branch.

## First Live Inputs

1. Dedicated authorized verified synthetic site, latest committed crawl,
   directly mapped static Eleventy repository pages and an exact offline build.
2. Reviewed signed `technical_internal_link_add` release; current owner session
   and recovery generation. For weekly preparation, a human `research_audit`
   and `draft_patch` standing grant with reviewed matching A0/A1 ranges, paths,
   weekly volume/spend caps and the existing configured single-site worker.
3. Existing private role DSNs, OpenBao read-only App credential, tenant/site-bound
   GitHub shared-egress context, artifact configuration and sandbox image.
4. Explicit current-owner exact-revision approval, fresh MFA and existing journal/
   PR/deployment/live-observation inputs before any separately authorized PR run.
   Signal never merges, deploys or pushes the default branch.

## Merge Train 4

Rebased in the accepted order above main `856a996` (head 0089).
Migration `0090` follows `0089`; 176 cumulative app tables.
Frozen migrations 0001-0089, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2916 API/identity/tooling/connector
cases, 1237 PostgreSQL suite cases,
46 repository and 270 dashboard cases; Ruff check and format passed.
Main's Direction C presentation and unchanged design guards are retained.
Earlier qualification and deferrals above are historical. Integrated qualification
requires the final-tip 0138 full-gate receipt and PR comment; they supersede the
slice deferral only when every gate passes. Live-provider and production limits
remain unchanged.
