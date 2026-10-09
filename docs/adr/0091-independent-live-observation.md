# ADR-0091: Observe Customer Delivery Without Certifying It

Status: Accepted for the internal GitHub-first beta path, 2026-09-29.

## Context

An opened PR, a successful check, and even a merge are not delivery (INV-011).
Slice 0085 must observe the customer's own delivery system without acquiring
merge or deployment authority. Revision 3.2 section 21.5 still requires a
signed deployment receipt plus an independent live check for delivery
certification. No safety gate is changed by beta sequencing.

## Decision

- Use a separate, one-repository read adapter requesting contents, pull
  requests, checks, commit statuses, and deployments read permissions. Every
  request uses the existing GitHub shared-egress profile and current owner,
  binding, site-proof, recovery-generation, and attempt checks.
- An owner selects the deployment environment and trusted provider actor ID.
  Inspect the latest deployment in that environment, not just an older success
  filtered by the desired SHA. Require the exact merged commit, the entire
  sealed candidate tree, non-transient production status, trusted deployment
  and status actors, chronological evidence, and the verified site origin.
- Independently fetch the affected static homepage through the existing
  leased crawl frontier, robots policy, origin admission, and pinned GET
  gateway. Match semantic postconditions AND the exact sealed result digest.
  Re-observe deployment identity after fetching; a changed deployment is
  inconclusive. Old content is stale, altered content is inconclusive, and
  harmful indexability/canonical/HTTP outcomes are regressed.
- Persist append-only RFC 8785 receipts whose provider and live evidence
  reference committed egress records. Attempts have a five-minute lifetime,
  exponential 30-second to one-hour backoff, and a 24-attempt operation cap.
  A lost observation stays unknown; retrying its identity cannot refetch.
- Report these as external/manual observations. Every receipt explicitly
  sets `delivery_certified: false`. A page can be independently verified
  without claiming signed delivery certification or measured SEO impact.

## Alternatives

Treating merge/check success as delivery, accepting an arbitrary preview URL,
or ignoring served-byte drift would invent confidence. SHA-filtered deployment
search can hide a later deployment of different content. Direct HTTP would
bypass existing robots and origin controls. All are rejected.

## Consequences

This conservative subset may be inconclusive when a CDN rewrites HTML or
unrelated changes alter the merged tree. Unsupported formats remain unavailable.
The signed delivery contract, production observation scheduling, real-provider
qualification, browser verification, and revert-PR execution remain later
gates. No merge, deployment, repository write, or secret-reading route is added.
See the [slice record](../implementation/0085-live-verification.md).
