# Slice 0030: GitHub-First Core V1 Contract Revision

Status: **DOCUMENTATION CONTRACT COMPLETE; NO NEW RUNTIME CAPABILITY**.

## Outcome

Signal now has one unambiguous active product contract for the owner's first real
test. Revision 3.2, the Core V1 PRD, roadmap, and ADR agree that the initial
product path connects an owner-controlled public site, Google Search Console,
GitHub, and Telegram; exposes a clean operational dashboard; uses all nine
non-CMS specialist roles; creates only an approved GitHub pull request; observes
customer-controlled delivery; independently verifies the live result; measures;
and supports conflict-aware recovery.

This slice changes documentation and repository consistency checks only. It does
not implement, configure, connect, or qualify GSC, GitHub, Telegram, the dashboard,
any agent, repository execution, deployment observation, or live verification. It
does not grant pilot, CMS, merge, deployment, or production authority.

## Contract Changes

| Area | Decision |
| --- | --- |
| Specification | Added Revision 3.2; preserved Revisions 3.1 and 3.0 unchanged |
| Product scope | GitHub-first Core V1 aligned to the owner's exact first test |
| Workforce | Nine explicit, typed, permissioned, evaluated logical roles are required |
| Architecture | Roles may share a bounded runtime; deterministic services retain authority |
| Delivery | One certified recipe, exact approval, PR-only operation, observed deployment, independent live verification |
| User experience | Owner-ready onboarding, clean dashboard, Telegram control, errors, recovery, and data lifecycle are required |
| Deferral | CMS-specific teams and broader GA1 scope are deferred; core specialists and safeguards are not |
| Evidence | Full real-provider, failure, restart, isolation, agent-eval, UX, and recovery matrix remains required |

## Files

- `Signal_Production_Engineering_Specification_Revision_3_2.md`
- `docs/product/core-v1-prd.md`
- `docs/implementation/core-v1-roadmap.md`
- `docs/adr/0030-github-first-core-v1.md`
- `docs/implementation/0030-core-v1-contract-revision.md`
- `README.md`
- `AGENTS.md`
- `CONTRIBUTING.md`
- `docs/README.md`
- `docs/adr/README.md`
- `docs/adr/0001-incremental-delivery.md`
- `docs/implementation/status.md`
- `CHANGELOG.md`
- `scripts/check-repository.mjs`
- `tests/repository/documentation.test.mjs`

## Verification

```sh
npm test
```

The repository suite verifies:

- protected SHA-256 hashes for all three accepted specification revisions;
- balanced Markdown fences and parseable structured examples in Revision 3.2;
- unique anchors, resolved internal references, source references, and logical
  catalog identities;
- complete requirement, invariant, edge-case, milestone, and gate inventories;
- the required Core V1 resources, roles, flow, scope boundary, and explicit
  `NOT_EXECUTED` evidence statements; and
- documentation indexes and working agreements that point to the active contract.

All 12 repository cases pass, including the new accepted-revision and Core V1
contract assertions. Documentation checks pass across 77 Markdown files. The
protected specification hashes are:

| Revision | SHA-256 |
| --- | --- |
| 3.0 | `e9b6fa79a93c84427bfcf100e3b80dcebdfd7e08b6d107df3181a975863c3676` |
| 3.1 | `14421ec851a4241718b9229e6d53dd3217bc48eb5d62fa7bb2265f0f7d604899` |
| 3.2 | `74181cb8ea1e3c902f3de3055add9f70fdaa9f097f24bf8bf0180aeee68b6cd7` |

The checks ran in a fresh detached checkout at base
`2004d31d91b1c449131e47a12db99c712211d9bb` with the exact Slice 0030 working
files overlaid. A direct run in the iCloud-backed working directory blocked before
test execution while macOS attempted to materialize two unchanged, locally evicted
repository-test files. The clean-checkout run used the same base content and exact
edited files and completed normally.

No Python, PostgreSQL, Temporal, browser, provider, agent-quality, or recovery test
is necessary to prove a documentation-only edit, and none is claimed as newly
executed here.

## Preserved Evidence And Limits

Revision 3.0 and Revision 3.1 remain reviewed, hash-protected baselines. Earlier
implementation records and provider evidence remain accurate within their stated
limits. ADR-0030 supersedes only the future pilot sequence in ADR-0001 and does not
turn the WordPress lab in ADR-0002 into production certification.

All newly specified Core V1 runtime evidence begins `NOT_EXECUTED`. The
[implementation status](status.md) remains the source for delivered capability,
and the [Core V1 roadmap](core-v1-roadmap.md) is the order for earning the missing
evidence one bounded slice at a time.
