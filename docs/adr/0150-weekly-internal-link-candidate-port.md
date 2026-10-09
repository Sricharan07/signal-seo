# ADR-0150: Weekly Internal-Link Candidate Port

Status: Accepted for the internal local boundary.
Date: 2026-10-04.

## Decision

Extend 0135's existing registry with `internal_link_proposals`, after a completed
cycle strategy rebuild. It consumes the human-granted `draft_patch` A1 proposal
scope and existing standing volume/spend reservations. The reviewed link release
is a candidate contract, not an A2 standing release. No grant is minted or changed.

Admit a closed plan of source/target evidence IDs, crawl manifest, extension and
binding, reviewed release, and deterministic revision/candidate/baseline keys.
Both source and target resource exclusions are rechecked. Opaque workload handles
remain workload identities, never owner sessions or authentication assertions.
Use the existing current standing resolver, owner/site epochs, recovery generation,
pause, expiry and revocation before credentials, provider requests and builds.

Reuse a closed list of existing read/build SQL implementations with workload
resolution and exact admitted-resource guards, as 0135 does for its other skills.
Only workflow may execute those ports. The facade independently rejects arbitrary
SQL, approval, grant changes and PR preparation/dispatch. A sealed revision must
use its own admitted candidate and baseline build keys. No write transport exists
in this skill. Preserve 0137 learning projections when wrapping strategy sources.

## Consequences

The configured single-site weekly operator can prepare evidence-backed Inbox
candidates automatically. Unconfigured runtime ports report `PORT_UNCONFIGURED`;
they do not pretend readiness. Unknown intents and failed builds retain the
existing durable no-blind-retry behavior and budget holds. Candidate preparation
does not approve or deliver a PR; only existing owner authority can do that.

Qualification and owner inputs: [0140](../implementation/0140-internal-links.md).
