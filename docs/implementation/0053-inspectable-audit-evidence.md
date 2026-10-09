# Slice 0053: Inspectable Audit Evidence

- Status: Implemented and locally qualified; customer findings unavailable
- Date: 2026-09-12
- Milestone: M2/M4 partial
- Specification: Revision 3.2 sections 11, 12, 13, and 24.2
- Decision: [ADR-0053](../adr/0053-project-manifest-evidence-without-inventing-findings.md)
- Depends on: [Slice 0052](0052-visible-durable-work-flow.md)

## Scope

This slice makes the latest durable audit receipt inspectable outside the Work
screen. It projects only the already authorized, bounded terminal manifest into
Pages and reflects the latest command on Overview. It adds no provider access,
database authority, workflow behavior, or inferred SEO result.

## Implemented Behavior

| Surface | Behavior |
| --- | --- |
| Overview | Shows the latest current-user site snapshot, terminal or active state, acceptance time, and links to Work and committed evidence |
| Pages | Shows committed coverage, counts, collection time, complete manifest identity, SHA-256 content digest, scope version, crawl-policy version, and source |
| Evidence boundary | Identifies synthetic local evidence, whether the customer origin was read, and that external writes remain blocked |
| Empty state | Routes the user to Work when no terminal manifest exists instead of rendering sample page inventory |
| Input validation | Requires positive bounded scope and crawl-policy versions in addition to the existing exact manifest schema, digest, counts, and terminal coherence |

The local pilot renders `Synthetic local evidence` and `Local no-network
executor`. It also states that no customer SEO findings were produced and that
the manifest cannot support claims about the configured site's SEO condition.

## Verification

Run:

```sh
npm --workspace @signal/dashboard test
npm --workspace @signal/dashboard run typecheck
npm --workspace @signal/dashboard run build
npm test
npm run test:repo
npm run check:docs
```

The dashboard suite covers the positive terminal projection, the no-evidence
state, malformed manifest versions, full provenance rendering, Overview status,
and explicit no-finding language. Browser qualification follows the real local
sign-in, site onboarding, Work execution, Pages inspection, and Overview journey.
Exact results and source hashes are recorded in
[0053 evidence](../evidence/0053-inspectable-audit-evidence.json).

## Failure Behavior

- Missing work or a nonterminal command cannot fabricate a manifest.
- Malformed identities, digests, counts, versions, timestamps, cross-site data,
  or terminal state fail closed before rendering as evidence.
- An unavailable session or API yields the existing closed dashboard state.
- Synthetic URL counts are never expanded into invented pages, issues, metrics,
  recommendations, or SEO conclusions.

## Explicit Limits

- This is an evidence projection over Slice 0052, not a customer crawl or SEO
  analyzer.
- No structured page observation or finding contract is implemented.
- No GSC import, competitor evidence, performance measurement, scoped memory,
  specialist work, proposal, approval, GitHub operation, deployment observation,
  recovery, or undo is added.
- Production crawl execution and every external write remain disabled.

## Next User-Visible Slice

Add a typed, durable finding/evidence contract backed by qualified observations,
then expose a bounded proposal and review journey. Do not derive findings from a
manifest count or activate the unfinished production crawler to populate the UI.
