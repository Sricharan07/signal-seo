# ADR-0087: Evidence-only onboarding strategy

Status: Accepted for local product implementation; no release admission.

## Context

Revision 4.0 sections 2.4, 15 and 17 require an onboarding baseline and strategy.
Upstream imports are incomplete; 0076 DataForSEO is absent. The existing Inbox
reviews exact built/sealed revisions, never raw findings. Plans confer no authority.

## Decision

Seal versioned canonical snapshots of existing observations without provider I/O.
Keep GSC/Bing cohorts separate, link measurements to pinned records, and list
missing inputs explicitly. Score only deterministic evidence-backed candidates
and record the labelled deterministic fallback. Model reordering stays unavailable.

Owner acceptance creates an existing unaccepted Content Writer proposal after its
current source/fact checks, atomically with the planning decision. Technical
acceptance references a matching pending sealed Inbox revision only. No fake diff,
draft or approval fills an unsupported state. Stable decisions prevent duplicates.

## Alternatives

Estimated search volumes invent readiness. Combining source totals obscures
coverage and double-counts cohorts. Automatic candidate preparation introduces
provider orchestration into analysis. A raw-finding Inbox changes the exact-revision
contract. All are rejected.

## Consequences

Partial evidence still yields analysis, but unsupported opportunities and unsealed
technical work cannot enter delivery. Release eligibility is informative, not an
owner dispatch grant. Snapshots retain history; decisions use current owner/session
authority and latest plan identity. This slice remains Product.

## Verification

Real PostgreSQL replay, proposal, immutability and isolation tests; deterministic
provenance/injection cases; API/CSRF negatives; dashboard component/BFF tests are
in [0077](../implementation/0077-seo-baseline-strategy.md).
