# ADR-0120: Explicit Owner Risk Acceptance For An Unprotected Default Branch

Status: Accepted, 2026-10-02.

## Context

Some private GitHub repositories cannot enable branch protections on their plan.
The owner approved a narrow, per-binding risk acceptance instead of making such a
repository public or removing the protected-base default. This is an authority
change and is security-critical. [Revision 4.1](../../Signal_Production_Engineering_Specification_Revision_4_1.md)
amends only the PR-only protection requirement; all accepted baselines are preserved.

## Decision

Use a distinct explicit dashboard command, not an optional flag on normal binding.
The normal path continues to fail durably on an unprotected base. Acceptance
re-inspects the exact repository and actual App installation permissions through
shared egress, then records immutable owner/fresh-MFA, provider identity, epochs,
and recovery-generation evidence in one PostgreSQL activation transaction.
`protected` remains false; acceptance never masquerades as protection.

Keep immutable acceptances and invalidations separate from mutable binding state.
A monotonically increasing risk generation prevents revival when provider state
reverts. Current observations gate builds and PR use. Provider errors, identity,
branch, installation, and permission drift invalidate the exception. Recovery and
authorization epochs deny restored grants. Protection, when observed, supersedes
the exception; a later unprotected state needs a new acceptance.

Require exact dashboard owner Inbox authority for every PR on the exception.
PostgreSQL checks this at operation eligibility and each existing fenced dispatch
step. Standing-dispatch eligibility separately requires actual protection. The
App-installation GET is read-only and uses the existing shared-egress admission,
robots, pinning, and immutable receipts. It does not mint a token or add repository
administration authority. See the [official endpoint contract](https://docs.github.com/en/rest/apps/apps#get-an-installation-for-the-authenticated-app).

## Consequences

No merge, deployment, default-branch push, workflow, secret, or protected-path
guard is relaxed. Existing workflow certification can still block outbound work.
The dashboard keeps an owner-accepted warning visible and never preselects risk
consent. Stale MFA requires ordinary authenticated sign-in, not a fabricated
step-up. Live GitHub and production deployment remain `NOT_EXECUTED` in this slice.
Astro dependency/build and delivery work are separate slices 0126 and 0127.

## Alternatives

Keep protection mandatory without an exception: safer default, but does not meet
the explicit owner decision. Make acceptance site-wide or enable standing grants:
rejected because either would widen authority beyond one observed binding.

## Verification

Real PostgreSQL tests cover owner/fresh-MFA requirements, immutable records,
sticky invalidation, recovery denial and actual protection preference. A real
PostgreSQL/Temporal lab with provider doubles behind shared egress exercises
standing denial followed by exact dashboard Inbox delivery. Dashboard and API
tests cover explicit consent, warning persistence and command denial. Commands,
counts and live limitations are in [0125](../implementation/0125-unprotected-base-acceptance.md).
