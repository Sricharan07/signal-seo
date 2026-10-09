# Slice 0154: AI Answers Completeness

Classification: **product**, with the structured-data Candidate Inbox sealing
boundary treated as **security-critical**. Based on main `856a996`, branch
`slice/0154-ai-answers`. Migration `0094_owner_ai_questions` follows unchanged
`0093` (merge train 5; written as 0090 after 0089) and adds one immutable
function-only forced-RLS receipt table (178 app tables). [ADR-0164](../adr/0164-owner-ai-question-sets.md) records the decision.
No dependency, accepted specification, existing migration, egress profile,
standing authorization or external publishing authority changes.

## Changes Per Finding

1. Owner-only `questions-propose` and `questions-approve` routes now reuse
   `load_crawl_question_sources`, `derive_target_questions` and
   `record_target_question_set`. The AI answers page proposes from the latest
   committed verified-site crawl, permits editing/adding/removing, and approves
   an immutable version. Unchanged questions retain crawl evidence; edits become
   owner data. Existing 25 total, ten added/edited and 8-512-character bounds,
   case-insensitive uniqueness, exact prior version and latest crawl are checked.
   Approval requires fresh MFA, current selected-site owner/recovery authority
   and browser mutation proof. Actor/request receipts make exact retries immutable.
   Scheduled runs consume the recorded version through the existing scheduler.
2. An accepted grounded structured-data recommendation now exposes approved-fact
   selectors and `seal`. The owner producer reuses the existing candidate worker
   ports and `seal_technical_recipe_revision(structured_data=...,
   brain_connection=...)`. It resolves a supported static extension, reviewed
   release and same-page current finding; refuses unavailable facts and unsupported
   values; checks exact source and baseline/candidate output; and seals a pending
   owner-review revision. The final database wrapper rechecks proposal digest,
   fact selections, provenance and fresh MFA. The exact revision remains in the
   Inbox for separate approval. No PR, merge or deploy happens here.
3. Scheduled observation wording now derives from real settings, current authority,
   persistent held cap and worker configuration. Enabled, paused, cap reached,
   worker unavailable and unreadable states are distinct. Structured-data readiness
   reflects proposal acceptance, facts, type, repository/release/finding inputs,
   worker configuration or a sealed Inbox revision. Historical payload digests
   remain the decision identity; current readiness is separately projected.
   No user-facing slice references remain in these messages.
4. Status rows and historical changelog claims distinguish the old internal
   foundations from the newly reachable owner question and sealing paths. The
   independent AI schedule is not a weekly-loop question producer. The separate
   0149 schedule-control UI remains outside this slice.

Dashboard additions inherit DESIGN.md, sentence-case copy, existing form surfaces,
ink `primary-command` buttons and collapsed Technical details. No gradients, glass,
kickers or design guard changes. `dashboard-view.tsx` and all weekly-loop modules
remain untouched.

## Qualification

The [evidence record](../evidence/0154-ai-answers.json) records final commands,
counts and local/live scope. Run the bounded checks without the full gate:

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q
.venv/bin/python scripts/run-ai-answers-tests.py
.venv/bin/python scripts/run-database-tests.py --shards 3
.venv/bin/python scripts/run-candidate-sandbox-tests.py
.venv/bin/ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
.venv/bin/ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
npm test
gitleaks git --pre-commit --staged --config .gitleaks.toml .
```

The focused AI answers lab includes AI visibility, its scheduler activities without
Temporal, owner question versions, technical recipes and grounded database sealing.
The candidate lab includes real no-network static baseline/candidate output checks.
All database work uses invocation-owned disposable PostgreSQL. Provider responses
are synthetic; they supplement, not replace, the recorded live limitations.

Final counts: 2,904 fast Python, 1,233 PostgreSQL, 61 focused visibility/recipe,
32 sandbox, 46 repository and 272 dashboard cases pass, with zero final failures
or skips. Ruff check and formatting (639 files), 328 Markdown-file checks,
dashboard typecheck and production build pass. The two Python warnings are existing
Starlette/AnyIO deprecations. Development failures are recorded in the evidence;
no policy gate was reduced to repair them.

Synthetic full-component captures at 1440 and 390 pixels were inspected. The
independent design reviewer scored its two fixes (ink primary specificity and
unavailable/MFA reason rendering) resolved, a `ship` verdict at that fix-list scope,
not a signed-in or live-provider journey qualification. The read-only documentation
handoff confirmed the incumbent system; preexisting design metadata drift was left
untouched. Temporary preview code, server and build-input types were removed.

## Limits

Not deployed. Live OpenAI/Perplexity/Gemini, dedicated repository source/build,
provider delivery and effect measurement remain NOT_EXECUTED. No production writes
or release authority. Temporal was not started; workflow-runtime qualification is
unchanged. Existing scheduler configuration must supply private qualified provider
ports; existing Content Writer candidate composition must supply private credentials,
shared-egress GitHub transport and isolated runner for sealing. Unconfigured ports
are explicitly unavailable. No customer credentials or data are used.

The handoff supports Article headline, one FAQ pair, and Organization/Product name
and optional description, all selected from current proposal-approved claims.
HowTo is unavailable; broader grounded types/fields remain internal to their own
recipe contract, not automatically generated here. A clean page without a current
finding, templated/CMS sources, rendered visibility with CSS/scripts and unsupported
static URL mappings remain unavailable. No fabricated finding or fact substitutes
for missing evidence. Scheduler spend remains unpriced and holds carry forward.
