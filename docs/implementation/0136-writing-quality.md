# Slice 0136 - Writing Quality and Claim Grounding

Classification: Security-critical. Changes the model-call boundary, grounding
rules and cost admission; no new secret path or publishing authority.

## Implemented

- Migration `0086` follows `0085` above main `168510b`. Four immutable forced-RLS,
  function-only tables record monthly model caps, reservations, dispatches and
  usage receipts. Default cap USD 25, integer micro-USD, warning at 80 percent,
  UTC-month accounting. Unknown and failed calls retain their holds; replays
  cannot dispatch again. Current owner, generation, tenant/site, body digest,
  month and lowered cap are checked before shared egress. Actual overspend is
  recorded and stops subsequent calls rather than hidden. See [ADR-0142](../adr/0142-role-models-writing-quality-and-monthly-budget.md).
- `model_roles.py` provides all nine defaults from the brief and operator-only
  role overrides. `model_reasoning.py` preserves strict schemas, tool-less calls,
  shared egress and bounds; existing OpenAI reasoning jobs require a scoped budget.
  Metadata intent/proposal records now use the Luna v2 protocol and bind an
  operator-selected requested model before dispatch. Old records are not edited;
  their exact v1 release/prompt pairs remain readable without enabling new v1 jobs.
- `writing_style.py` supplies a stable cacheable prompt prefix, banned phrases,
  approved-fact specificity and multilingual voice rules. Bounded excerpts use
  current completed-crawl pages ranked by final page-dimensional GSC clicks,
  otherwise brief-selected pages or the approved voice profile. Voice and brief
  text remain data, never facts or instructions.
- `article_pipeline.py` runs outline, draft, five-facet critique and revision.
  The deterministic gate measures all requested metrics and target language.
  Failure regenerates once, with every pass separately reserved and charged.
  A second failure is owner-required with `low_quality` reasons. Originality
  remains an independent rejection gate. English-only passive/readability
  proxies are explicitly not assessed for Telugu or mixed text.
- Content Writer extracts atomic claims with complete field coverage, performs
  Jev Noul coverage/entailment checks and persists per-claim citations and
  decisions. Labelled Luna high-effort fallback cannot clear primary uncertainty
  or deterministic flags. Nonclaims are allowed with supported coverage;
  unsupported/uncertain claims require the owner. Sensitive categories keep
  exact matching and always require the owner. See [ADR-0143](../adr/0143-claim-level-grounding.md).
- Older standalone fixture/homepage metadata and technical-recipe contracts retain
  their existing owner-only evidence checks. They do not carry approved Business
  Brain fact IDs or gain C2 citations, semantic-grounded status or autonomy. Their
  model calls do use the new role routing, style prefix, quality checks and budget;
  budget exhaustion or a second quality failure returns explicit HTTP 503
  unavailability. Migrating their grounding contracts remains a scope decision,
  not an implicitly qualified capability.
- Dashboard displays monthly holds/spend, exhaustion, quality flags/reasons and
  per-claim fact links in the existing owner review flow. Exhaustion disables
  drafting, not owner inspection. Candidate sealing retains current-fact
  revalidation, originality, A2 and `autonomy_eligible=false`.
- `scripts/evaluate-writing-quality.py` runs five fixed synthetic briefs through
  the same pipeline with deterministic metrics. `--live` remains NOT_EXECUTED
  without an explicitly composed OpenBao credential, shared gateway and real
  PostgreSQL budget in a dedicated synthetic scope. No raw-key/direct-HTTP path.

## Configuration

Per role, set `SIGNAL_MODEL_<ROLE>_MODEL` and/or `SIGNAL_MODEL_<ROLE>_EFFORT` in
protected self-host operator configuration. Roles are the nine uppercase names
from `model_roles.py`. For a different model also supply `INPUT_RATE`,
`CACHED_RATE`, `OUTPUT_RATE` and `CACHE_WRITE_RATE`, integer micro-USD per million
tokens, under the same prefix. No request or source can supply these overrides.
Invalid configuration fails closed. An override is not a live-qualified release.
Monthly cap adjustments use the scoped owner-only `model_budget_set_cap` port;
the slice does not add a new browser settings workflow or automatic refunds.

## Verification

Full runnable commands and actual results are recorded in
[0136 evidence](../evidence/0136-writing-quality.json). Synthetic evaluations test
thresholds, one regeneration, language, injection, all role defaults/overrides,
claim support/uncertainty/nonclaims/sensitive categories and reduction-only flags.
Real PostgreSQL tests exercise owner and non-owner roles, tenants/sites,
immutable history, concurrency, reservations, replay, month rollover, exhaustion,
current-owner changes, model identity and unknown holds through real shared egress.
The concurrency regression uses five independently raced reservation pairs. Budget
mutations use `NO KEY UPDATE`, compatible with the existing authorizer's `KEY SHARE`
lock, while retaining site/advisory serialization and immutable cost history.

The full sequential local gate passed: PostgreSQL 979/0, other labs 151/0,
Python API/identity/tooling/connectors 1949/0, repository/dashboard JavaScript
245/0, synthetic evaluation 5/0, and GSC boundary, Ruff, formatting, dependency,
documentation, typecheck and build checks passed. Invocation-owned resources
were cleaned by each lab. The 3 GiB stop guard observed a minimum 13.89 GiB free.
Recovery/final audits checked all 46 touched files for nonempty UTF-8 content,
NULs, complete tails/newlines, Python AST and JSON syntax; no truncation found.
The staged diff was leak-scanned with main's unchanged configuration; the final
commit-range result is recorded in the PR after the commit exists.

Existing Content Writer test changes are intentional: the provider double now
answers four article passes and claim extraction; the fallback can support an
otherwise valid nonsensitive claim while remaining labelled; primary uncertainty
still requires the owner; provider labels reflect any per-claim fallback, not
only the summary decision. Added assertions cover pass efforts, monthly usage,
quality and claim citations. Existing exact-match tests without inference receipts,
originality, source injection, role negatives, caps, sealing and owner review are
retained. Metadata/model/decision fixtures advance requested/reported model and
release to Luna v2; database head/table counts advance to `0086`/171.

## Live Inputs and Limits

Live model, Jev, GSC-ranked voice selection, customer-source qualification,
authenticated owner journey and production composition are NOT_EXECUTED.
First live run needs a dedicated authorized synthetic tenant/site and current
owner session/generation, completed crawl/artifact store/key, approved Business
Brain facts/voice, existing OpenBao model and optional Jev capabilities, scoped
API/identity connections and qualified shared egress, an explicit monthly cap,
and human review of multilingual held-out drafts. To run the optional harness,
provide `SIGNAL_WRITING_EVAL_RUNTIME_FACTORY=module:function` and
`SIGNAL_WRITING_EVAL_SYNTHETIC_ONLY=1`; the factory composes only existing ports.
No customer data, production write, article autonomy, merge or deployment is
enabled. No report-generation service is invented.

## Merge Train 3

Rebased in the accepted order above main `168510b` (head 0079).
Migration `0086` follows `0085`; 171 cumulative app tables.
Frozen migrations 0001-0079, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2721 API/identity/tooling/connector
cases, 1154 PostgreSQL suite cases,
46 repository and 253 dashboard cases; Ruff check and format passed.
Earlier qualification above is historical; full-stack results are recorded at the
final tip. No live-provider or production authority is added.

The slice-owned `scripts/evaluate-writing-quality.py` synthetic evaluation
passed all five fixed cases. It uses test doubles; live models remain NOT_EXECUTED.
