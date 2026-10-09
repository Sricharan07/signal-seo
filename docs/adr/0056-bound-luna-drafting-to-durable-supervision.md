# ADR-0056: Bound Luna drafting to durable supervision

## Status

Accepted on 2026-09-13.

## Context

The supervised local proposal flow proved exact evidence, immutable revisions, and
human decisions, but its copy was deterministic. Signal therefore had no working
model boundary that a user could exercise through the product. Replacing every
role or safety decision with a model in one step would make authority, retries,
cost, and failure outcomes difficult to audit. Sending a credential directly from
the browser or storing it in PostgreSQL would also cross the established secret
boundary.

The requested provider model is `gpt-5.6-luna`. That identifier is an exact
request value, not an immutable weights snapshot. A production role release still
requires a separately approved model release and evaluation cohort.

## Decision

Add one bounded model-backed responsibility to the existing fixture flow:

1. The Content Strategy responsibility may call the OpenAI Responses API with
   exact model `gpt-5.6-luna`, a hash-bound instruction release, one canonical
   synthetic evidence packet, strict structured output, no tools, no parallel tool
   calls, low reasoning effort, `store=false`, and fixed input/output/time bounds.
2. A developer supplies the credential only to the disposable pilot. The pilot
   removes it from its environment immediately, stores it in a separate
   invocation-owned OpenBao KV mount, and gives the adapter an exact read-only
   capability. The credential is never accepted from the browser, stored in the
   business database, or written to evidence.
3. PostgreSQL records immutable `agent_runs` and `model_calls`. The request intent
   commits before provider I/O. A known failure may start a bounded later attempt;
   an unknown transport or persistence outcome blocks blind retry.
4. Successful output is schema-validated, canonicalized, hashed, and atomically
   bound into a new proposal revision. PostgreSQL reconstructs the exact accepted
   model manifest from current finding evidence, run identity, provider receipt,
   output, usage, prompt hash, and input hash before committing it.
5. Deterministic policy, scope checks, approval authority, and decision handling
   remain outside the model. Approval still accepts only a local synthetic draft
   and dispatches no external operation.

## Alternatives

- **Replace all roles with Luna immediately.** Rejected because the role schemas,
  tools, budgets, evaluations, and disagreement handling are not qualified. Model
  reasoning must not replace deterministic authority or safety policy.
- **Call the model before recording intent.** Rejected because a crash or timeout
  could cause an untracked and blindly repeated provider operation.
- **Put the API key in `.env`, PostgreSQL, or browser configuration.** Rejected
  because those surfaces broaden retention and disclosure beyond the one model
  capability.
- **Treat the model name as an immutable release.** Rejected. Signal records the
  requested and provider-reported model plus prompt, input, and output identities;
  production still needs a reviewed pinned release and evaluations.

## Consequences

Users can now exercise a real model call from Signal Chat and inspect the exact
model provenance and output in Approvals. Provider ambiguity and failures become
durable, sanitized states rather than invisible retries. The model has no browser,
network-tool, customer-origin, GitHub, approval, or execution authority.

This remains one synthetic metadata role, not the nine-role workforce, arbitrary
chat, customer SEO analysis, competitor research, a repository candidate, a
GitHub pull request, Telegram control, production deployment, or external undo.

## Verification

- Unit tests cover the exact request, strict output schema, no-tool/no-storage
  profile, output and usage validation, response bounds, provider failure classes,
  credential parsing, and secret-safe representations.
- Real OpenBao tests prove the exact secret read and denied mutation capability.
- Real PostgreSQL tests cover intent-before-I/O state, bounded retry, unknown-
  outcome blocking, stale evidence, manifest reconstruction, tamper rejection,
  append-only transitions, forced RLS, and migration rollback.
- API and dashboard tests cover the model route, closed error mapping, dual
  deterministic/model projections, model audit rendering, and exact human
  decisions.
- A disposable real-stack journey completed one live Responses call and rendered
  its sealed Luna revision without reading a customer origin or issuing an
  external write.
