# Slice 0054: Durable Fixture Finding

- Status: Implemented and locally qualified; customer-site analysis unavailable
- Date: 2026-09-13
- Milestone: M2/M4 partial
- Specification: Revision 3.2 sections 11, 12, 13, 16, and 24.2
- Decision: [ADR-0054](../adr/0054-prove-the-finding-pipeline-with-a-fixed-fixture.md)
- Depends on: [Slice 0053](0053-inspectable-audit-evidence.md)

## Scope

This slice adds the first runnable evidence-to-finding path after a completed
local audit. A deterministic metadata detector analyzes one fixed repository
fixture, stores immutable evidence and a current finding in PostgreSQL, exposes
the result through authenticated API and same-origin dashboard boundaries, and
renders its exact provenance on Pages.

The detector never resolves or requests the configured site. The result is
therefore a product-pipeline test, not evidence about a customer's SEO state.

## Implemented Behavior

| Surface | Behavior |
| --- | --- |
| Detector | Parses one bounded fixed HTML document and detects a missing non-empty meta description |
| Evidence | Stores exact command and manifest provenance, fixture identity, SHA-256 digest, observation time, rights, quality, and `customer_origin_read=false` |
| Finding | Maintains one current deterministic medium-severity finding and appends supporting evidence after later completed audits |
| Authority | Requires a live tenant session, current recovery generation, selected exact site, current membership, and the current actor's completed audit |
| API | Adds strict `POST /analysis/local-fixture` and bounded `GET /findings` contracts behind an explicitly injected gateway |
| Dashboard | Adds a same-origin **Analyze test fixture** action and a Pages result with evidence UUID, digest, time, confidence, and an explicit fixture-only warning |
| Retry | Reuses evidence for the same completed audit; concurrent retries converge on one evidence record and one current finding |

Migration `0030` adds `app.evidence_records`, `app.findings`, and
`app.finding_evidence`. All three force tenant/site RLS. Runtime roles receive no
direct table privileges. Evidence and links are append-only; the finding guard
allows only monotonic latest-command and last-seen advancement through the narrow
security-definer operation.

## Verification

Run:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/pytest -q tests/api
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
npm test
```

The focused tests cover parser presence/absence and malformed input, missing or
unfinished audits, exact replay, a later audit, concurrent retries, conflicting
evidence, wrong-site and stale-session authority, forced RLS, direct privilege
denial, immutable records, strict HTTP input/output, same-origin enforcement,
unconfigured gateway failure, dashboard parsing, and visible provenance. Exact
results and source hashes are recorded in
[0054 evidence](../evidence/0054-durable-fixture-finding.json).

## Failure Behavior

- Analysis before a successful current-user audit returns `AUDIT_NOT_READY` and
  persists no finding.
- Invalid session, stale recovery generation, wrong selected site, or reduced
  membership fails closed before result access.
- A conflicting digest for the same command/source identity returns
  `EVIDENCE_CONFLICT`; committed evidence is not replaced.
- Malformed, oversized, redirected, cross-origin, or cookie-ambiguous dashboard
  traffic cannot invoke the operation.
- The default API composition has no finding gateway and returns unavailable.

## Explicit Limits

- The only source is the checked-in synthetic fixture. No configured origin,
  artifact body, GSC property, competitor source, or customer data is read.
- The `signal_identity` role temporarily owns only the two narrow function calls
  because this local path already composes that authenticated connection. A
  dedicated analysis role is required before customer evidence is enabled.
- Detector releases are represented by one fixed immutable UUID, not yet by a
  signed release registry.
- Finding resolution/retraction, planning, specialist work, proposals, approvals,
  GitHub operations, deployment observation, recovery, and undo are not added.
- Production crawling and every external write remain disabled.

## Next User-Visible Slice

Replace the fixed fixture input with one authorized, immutable page observation
from the selected site's qualified crawl path. Preserve the same provenance and
no-invention rules, then expose the resulting real page fact on Pages before
building proposal and approval controls.
