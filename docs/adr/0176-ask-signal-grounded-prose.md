# ADR-0176 - Ask Signal grounded prose

Status: Accepted for the owner-requested 0169 local boundary; no release authority.

## Context

Owner decision, 2026-10-06: Ask Signal must speak in natural prose while every
factual sentence remains grounded in cited committed records. 0162 accepts only
complete exact excerpts. Its API, private memory and replay semantics must not change.

## Decision

Use internally tagged sentences, deterministic record-token checks and the
Content Writer's sentence, grounding and sensitive-claim helpers. Then run one
budgeted batch of reduction-only entailment questions against each sentence's
cited records. Only approved-fact records can support business claims; memory
and work/status records cannot. Keep sensitive claims exact and uncited text
restricted to discourse. Reject unknown-only references and unsupported details.

Keep the v1 API and dashboard unchanged: joined text and ordered actually-used
citations, with a 1,200-character/four-citation bound. If verification cannot
confirm prose, return existing extractive excerpts or the honest no-record reply.
Budget exhaustion and unknown outcomes remain explicit and replay never spends again.

Migration 0103 (written as 0102; renumbered at merge to compose on 0170's fail-closed 0102) is necessary because the member-scoped ports formerly admit only
the reply ID. Admit one fixed derived verification identity for the same pending
private reply after a confirmed generation receipt. Keep the existing shared
permission, role, cap, digest, dispatch and receipt discipline. Add no table,
runtime grant, provider or authority. Use the operator-configured `owner_answers`
model for both calls; no model name is embedded in this slice.

## Alternatives

Exact excerpts alone preserve safety but do not meet the owner's prose decision.
Free-form prose with citations alone cannot establish support. A separate checker
would duplicate writer safety rules. An unbudgeted or arbitrary-ID second call
would violate spend and replay boundaries. None is adopted.

## Consequences And Verification

Prose adds one bounded model call when candidates survive deterministic checks.
Conservative checks can fall back to excerpts, particularly for sensitive claims.
Entailment reduces output only; it cannot waive a deterministic denial or mint
authority. Unknown usage holds remain retained; API and memory lifecycle are unchanged.

[0169](../implementation/0169-ask-signal-prose.md) records real PostgreSQL/shared-egress
positive, negative and failure qualification and the final full-gate commands.
Live provider/deployment qualification is NOT_EXECUTED; production writes stay disabled.
