# ADR-0064: Prioritize a GitHub-First Beta Path

- Status: Accepted
- Date: 2026-09-29
- Owners: Signal product and engineering
- Related: [ADR-0060](0060-autonomous-seo-employee-direction.md), [roadmap](../implementation/roadmap.md), Revision 4.0 sections 17 and 19

## Context

Slice 0066 established a locally qualified, bounded verified-origin crawl. The
R1-first sequence still places browser rendering and several provider integrations
before the first complete path from crawl evidence to a GitHub pull request. The
owner needs that end-to-end path sooner, without representing a beta as an R1,
R2, R3, or R4 release.

## Decision

Keep the accepted Revision 4.0 contract and every existing slice identifier.
Prioritize an internal GitHub-first beta path: 0068 technical findings, 0070
durable GitHub read binding, 0079 pull-request authority, 0080 isolated candidate
build, 0081 sealed technical recipes, 0083 exact-revision Inbox, 0084 reconciled
pull-request creation, 0085 check/deployment observation and shared-egress live
GET verification, then 0088 standing authorization, 0089 the deterministic-first
Jev autonomy gate, and 0090 the weekly loop. A browser is not required for the
beta's exact HTTP live check.

The beta is an internal integration milestone, not release admission or permission
to enable production writes. Slices 0067, 0069, 0071-0078, 0082, 0086-0087,
0091-0095, and R4 remain in scope for their owning releases. Their capabilities
stay visibly unavailable until qualified. Security-critical slices retain their
full provider, failure, recovery, and evidence gates. Product slices cannot gain
authority or external-write paths by being called product work.

## Alternatives

- Keep the original R1 order. Rejected because it delays the first useful
  crawl-to-PR journey behind independent breadth work.
- Drop release requirements or count the beta as R1-R3. Rejected because it would
  change the accepted contract and misrepresent unqualified capability.
- Bypass browser isolation for live verification. Rejected; the beta uses the
  reviewed shared-egress GET path and reserves browser verification for its later
  qualified slice.

## Consequences

The beta initially supports a narrow GitHub-first, verified-site journey. It
does not claim browser-rendered evidence, GSC/Bing data, Business Brain, AI-search
visibility, articles, CMS delivery, managed SaaS, or release readiness. Missing
provider resources are recorded as `NOT_EXECUTED` live qualification, not as a
passing provider test. No deterministic policy, credential, recovery, sandbox,
or external-write gate changes in this decision.

## Verification

The roadmap and implementation-status links are checked by `npm test`. Each
reordered slice still carries its own tier-specific tests, evidence, and release
status; this sequencing decision itself grants no runtime authority.
