# Slice 0068: Crawl-Wide Technical Findings

Status: **IMPLEMENTED FOR OBSERVED HTML AND SETTLEMENT EVIDENCE; SITEMAP CONTENT NOT ASSESSED; R1 PARTIAL**.

## Objective And Tier

Turn slice 0066's immutable completed-crawl records into a bounded, replay-safe
technical audit for the GitHub-first beta. This is **security-critical** under
Revision 4.0 section 19 because the new function-only database grant extends
internal crawl-ingest read/write authority. It creates no new network,
credential, publishing, or user authorization capability. [ADR-0065](../adr/0065-seal-crawl-audit-evidence-under-ingest-role.md)
records the evidence boundary; [ADR-0064](../adr/0064-prioritize-github-first-beta-path.md)
records the beta reorder without changing Revision 4.0.

## Implemented

- Migration `0038` gives only the crawl-ingest role a scoped read of one completed
  manifest's URL settlements and parsed page records. It stores one immutable,
  forced-RLS report per manifest and detector release. Each finding names an exact
  page record or frontier settlement in that manifest. Input count must match the
  manifest's discovered count; database validation rejects wrong-site, forged
  evidence, unsupported coverage, and mismatched replay.
- The deterministic detector reports missing or duplicate titles, missing meta
  descriptions, missing or multiple H1s, absent or external canonical links,
  malformed JSON-LD, observed 404/410 internal targets, and a robots-denied
  homepage. It neither interprets page text as instructions nor infers a broken
  link from an unobserved target.
- Completed real crawls seal the report before the activity returns. Replay
  recomputes against immutable source evidence and reuses the same report.
  Sitemaps are explicitly `not_assessed`: the current crawl does not fetch XML
  sitemaps. Metadata coverage is partial when bounded parsing truncated output;
  no sitemap defect or site-wide count is fabricated.

## Qualification

```sh
.venv/bin/pytest -q tests/tooling/test_crawl_audit.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests/api tests/consumer tests/container tests/control_plane tests/crawler tests/identity tests/page_attempt tests/temporal tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests/api tests/consumer tests/container tests/control_plane tests/crawler tests/identity tests/page_attempt tests/temporal tests/tooling
.venv/bin/python -m pip check
npm test
```

Exact command outcomes are in [the evidence record](../evidence/0068-crawl-technical-findings.json).

The disposable PostgreSQL suite exercises a real completed crawl, deterministic
replay, broken-link evidence, cross-site rejection, forged source/count rejection,
function-only access, immutable reports, and migration rollback. Unit tests cover
clean pages, multiple rule classes, robots denial, and incomplete evidence.

## Limits And Next Work

This is an internal report, not yet an authenticated customer-facing Pages or
Inbox projection. The bounded crawler does not inspect sitemap XML or rendered
JavaScript. `not_assessed` is not a passed sitemap check. No owner-controlled
public-origin end-to-end crawl was run in this slice. Production composition,
release admission, and external writes remain disabled. The next beta-path slice
is 0070, the durable owner-selected GitHub read binding.
