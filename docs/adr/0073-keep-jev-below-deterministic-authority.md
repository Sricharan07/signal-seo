# ADR-0073: Keep Jev Below Deterministic Authority

Status: Accepted for the internal 0089 foundation, 2026-09-29.

## Context

Revision 4.0 requires deterministic policy to run before Jev and the authorizer
to recheck current authority after Jev. A model decision cannot mint or enlarge
authority. A recommendation may arrive after a revocation, cap consumption, or
recovery-generation change. The current repository has a typed recommendation
client, immutable model records, and 0088 standing grants, but no autonomous
external-write path.

## Decision

The gate obtains the current generation from the independent recovery source,
applies non-model approval-class and sensitive-claim restrictions, then calls a
read-only grant and budget preflight. A deterministic denial never calls Jev.
For eligible work, it sends bounded typed recommendation and risk-class Choice
questions through the 0064 decision service, which persists the answers before
finalization.

The finalizer rechecks the 0088 grant and signed 0103 recipe status, matches
the recorded model input hash and grant threshold, and reserves the site-wide
weekly allowance atomically with an immutable gate record. Only a primary Jev
`ship` with non-high risk at or above the current threshold can yield `ship`; a fallback, low
confidence, `ask_owner`, `reject`, changed authority, or exhausted budget cannot.
Replaying a recorded decision yields `HISTORICAL_GATE_ONLY`, never a reusable
dispatch permit. This slice connects to no PR, CMS, or other external writer.

## Alternatives

- Calling Jev before policy would spend model work on forbidden actions and let
  a persuasive answer influence the hard boundary.
- Trusting preflight after the model call would miss revocation and budget races.
- Reusing a past `ship` on retry would turn historical evidence into fresh
  authority.

## Consequences

The final result is a recorded internal eligibility decision and reservation,
not permission to dispatch an external write. The future sealed-work and
write-intent path must bind candidate fields to authoritative revision evidence,
apply whole-job impact accounting, and recheck the independent generation and
every permission at dispatch. Live TypeSafe success and production OpenBao/
journal composition remain unqualified.

## Verification

See [0089 implementation](../implementation/0089-jev-autonomy-gate.md) and
[0089 evidence](../evidence/0089-jev-autonomy-gate.json). Real PostgreSQL
tests cover positive ship reservation, low confidence, fallback, deterministic
denial, revocation and budget races, recovery failure, and immutable receipts.
