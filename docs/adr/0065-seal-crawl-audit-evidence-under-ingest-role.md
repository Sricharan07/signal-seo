# ADR-0065: Seal Crawl Audit Evidence Under the Ingest Role

- Status: Accepted
- Date: 2026-09-29
- Owners: Signal crawl evidence and technical audit
- Related: [ADR-0063](0063-settle-every-crawl-frontier-with-evidence.md), [ADR-0064](0064-prioritize-github-first-beta-path.md), Revision 4.0 section 19

## Context

The crawl-ingest role cannot read page and settlement tables directly. A
technical audit requires those facts, but giving the role table-level SELECT
or allowing a model to supply findings would weaken the evidence boundary.
The new scoped read and report-write functions extend internal role authority,
so slice 0068 is security-critical rather than the roadmap's originally planned
product tier.

## Decision

Grant only two function calls to `signal_crawl_ingest`: load evidence from one
completed manifest and seal one immutable report for that manifest and detector
release. Keep direct table access denied and forced RLS in place. Require exact
tenant, site, manifest hash, discovered-input count, bounded report shape,
coverage state, and valid page/settlement source references. Serialize same-
manifest report recording and reject conflicting replay. Derive findings in
deterministic code; never treat page text or a model response as a rule.

Reports mark sitemap content `not_assessed` until a qualified sitemap fetch
exists. A completed report is not an R1 release certificate or permission to
ship a change.

## Alternatives

- Grant direct SELECT on crawl evidence tables. Rejected because it needlessly
  broadens the runtime role and bypasses exact-manifest scoping.
- Store only transient in-memory findings. Rejected because downstream sealed
  fixes need durable evidence and crash/replay consistency.
- Assert a sitemap defect without fetching sitemap XML. Rejected as fabricated
  evidence; explicit unavailable coverage is safer.

## Consequences

The crawl activity now seals its technical report before returning. A report
failure after manifest finalization stops the activity rather than returning an
unaudited success; retry can re-evaluate the immutable manifest. Customer-facing
Pages/Inbox projection and live owner-origin qualification remain later work.

## Verification

The real PostgreSQL lab checks exact-manifest replay, cross-site rejection,
forged source/count rejection, malformed report rejection, function-only access,
forced RLS, immutability, and atomic migration rollback. The tooling suite
checks deterministic positive, negative, and incomplete-evidence cases. See
[slice 0068](../implementation/0068-crawl-technical-findings.md) and its
[evidence record](../evidence/0068-crawl-technical-findings.json).
