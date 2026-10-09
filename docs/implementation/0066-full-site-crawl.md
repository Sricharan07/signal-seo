# Slice 0066: Full-Site Crawl Composition

Status: **IMPLEMENTED AND LOCALLY COMPONENT-QUALIFIED; REAL OWNER-ORIGIN JOURNEY NOT EXECUTED; PRODUCTION COMPOSITION UNAVAILABLE**.

## Objective And Tier

Compose the existing immutable crawl run, frontier lease, robots, shared egress,
encrypted artifact, fetch observation, and Temporal boundaries for one currently
verified site. Settle every discovered URL and produce a coverage-aware manifest.
This is security-critical under Revision 4.0 section 19 because it touches egress,
site authority, and restart recovery. [ADR-0063](../adr/0063-settle-every-crawl-frontier-with-evidence.md)
records the recovery decision.

## Implemented

- Migration `0037` adds forced-RLS, function-only robots dispatches, bounded parsed
  page records, exact terminal frontier settlements, and immutable crawl manifests.
  It admits the `settled` frontier and `completed` run states without loosening
  immutable run identity or unrelated legacy checks.
- The executor checks current proof of the exact primary origin before opening a
  run. Robots bootstrap uses a global permit and durable dispatch before a pinned
  request. Pages use the shared egress gateway with current robots evidence and
  the same origin bucket. Only same-origin, admitted HTML links expand the frontier.
- HTML parsing extracts bounded title, description, headings, canonical,
  hreflang, robots directives, JSON-LD types, and internal/external links as inert
  data. Page records bind an encrypted fetch observation and body digest. Frontier
  settlement binds the exact lease, egress operation, robots snapshot, observation,
  page, and byte count; forged cross-bindings fail closed.
- A restart first reconciles durable observations. It never repeats an unresolved
  robots or page dispatch. A finalized run has a closed terminal class for each
  frontier URL; revocation instead halts the run without pretending it finalized.
  Manifest coverage is partial for a budget limit, unknown dispatch, failure,
  parse error, or robots denial. `complete` describes the bounded, link-discovered
  frontier, not undiscovered or sitemap-only pages.
- The real executor is registered through the existing `CrawlSiteActivities`
  adapter in explicit `npm run pilot:verified-crawl` mode. The default pilot
  remains no-network and synthetic. The real mode requires current origin proof,
  uses an invocation-owned artifact key/directory, and leaves external writes
  disabled. Its worker cooperates with Temporal cancellation and emits heartbeats.

## Failure And Authority Boundaries

- An unverified, archived, or re-verification-required origin cannot dispatch a
  page. A proof revoked after robots retrieval still blocks the page request.
- An interrupted network dispatch becomes `dispatch_unknown`, not a retry. A
  completed robots dispatch can be reused and is not misclassified as unknown.
- Byte accounting is serialized by the crawl run row. Pending URLs are settled as
  budget-exhausted before finalization; a manifest cannot omit a discovered URL.
- Runtime roles have no direct read/write access to the new tables. All four are
  forced-RLS, and the migration fails atomically without losing revision `0036`.
- No model-generated text is interpreted as a command. The crawl uses GET only,
  never signs in, follows an outbound link, submits a form, or performs a write.

## Verification

```sh
.venv/bin/pytest -q tests/tooling/test_crawl_parser.py tests/tooling/test_full_site_crawl.py tests/tooling/test_local_pilot.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests/api tests/consumer tests/container tests/control_plane tests/crawler tests/identity tests/page_attempt tests/temporal tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests/api tests/consumer tests/container tests/control_plane tests/crawler tests/identity tests/page_attempt tests/temporal tests/tooling
.venv/bin/python -m pip check
npm test
npm run pilot:verified-crawl # wait for ready, then Ctrl-C; startup only
```

The exact counts, environment versions, and outcomes are recorded in
[the evidence record](../evidence/0066-full-site-crawl.json). The PostgreSQL lab
includes positive verified one- and two-page crawls, unverified denial, forged
settlement rejection, robots denial, revocation after robots, byte exhaustion,
crash recovery at robots and page dispatch, RLS/function privileges, and rollback.
Separate isolated-network tests exercise the real pinned HTTP boundary; Temporal
tests exercise workflow retry, replay, and cancellation. The optional pilot
reached ready, served dashboard HTTP 200, and cleaned up its invocation-owned
providers on Ctrl-C; no owner-origin crawl was run.

## Limits And Next Work

No owner-controlled public origin was supplied for a complete browser-to-network
verified-crawl pilot run, so that journey is `NOT_EXECUTED`. There is no production
resolver or worker deployment, distributed artifact/key lifecycle, sitemap-only
discovery, per-page crawl viewer, restore drill for this new manifest, or R1
release approval. A `complete` bounded-frontier manifest is not a statement that
every page on a website was found. Production identity and writes remain disabled.
Slice 0067 next adds the isolated browser worker; release qualification remains
slice 0078.
