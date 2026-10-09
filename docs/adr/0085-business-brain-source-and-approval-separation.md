# ADR-0085: Business Brain Sources Do Not Approve Their Own Claims

Status: accepted for slice 0073, 2026-09-29.

## Context

REQ-021 and INV-030 require business memory with inspectable provenance and only
owner-approved claim grounding. Crawled pages and screened documents may contain
instructions addressed to a model. Classification and extraction are uncertain
provider operations, not sources of owner authority.

## Decision

Use the existing owner/session policy, Jev Choice/decision-record path, reasoner
boundary, artifact store, and shared egress. A classification has a durable
decision id and a labelled frontier fallback when Jev cannot answer. Extraction
has an immutable source/range/version intent before provider I/O and an atomic
completion receipt. Every candidate is proposed regardless of category or model
confidence; only a current owner mutation can approve it. Invalid output writes
no facts, and an ambiguous dispatch cannot be silently retried.

Facts, events, extraction receipts, voice versions, and audit records are
append-only and tenant/site scoped under forced RLS. Corrections are new owner
statements that supersede the predecessor. Approved queries exclude proposed,
removed, and superseded facts. Voice edits require the exact current version.

## Consequences

- No new authority, secret path, egress grant, dependency, or external write is
  introduced; this is a Product slice using already qualified boundaries.
- Provider configuration and live qualification are explicit. Missing model
  composition is visibly unavailable and writes nothing; manual owner facts work.
- A failed/unknown version is retained rather than automatically retried. Future
  reconciliation or re-extraction versions need an explicit bounded follow-up.
- Injection screening is defensive, not a claim of perfect semantic model
  resistance. Typed schemas and deterministic approval policy constrain effects.

## Verification

See [0073](../implementation/0073-business-brain.md) and its gate evidence.
