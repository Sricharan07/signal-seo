# Slice 0129: Bing Per-Page Performance Import

Date: 2026-10-03. Classification: **security-critical**, existing provider boundary.
Status: Implemented and locally qualified; live Bing NOT_EXECUTED.

## Contract and Scope

Revision 4.0 sections 4, 11, 12, 16 and 19; Revision 3.2 sections 2, 7, 10, 11,
12, 27 and 30. Preserves INV-018 missing-data honesty, INV-029 shared egress,
INV-033 untrusted provider data and tenant/credential isolation.
[ADR-0127](../adr/0127-bing-top-page-api-contract.md) records official endpoint,
field and temporal findings; [ADR-0128](../adr/0128-bing-page-read-port.md) records
the restricted read port and deferred measurement integration.

Implemented in existing `bing_protocol`, `bing_binding`, `egress_profiles`, and
`shared_egress` modules; no new service or dependency. Only GetPageStats is added.
GET only, fixed existing Bing origin/path, exact single `siteUrl` parameter,
matching bound OAuth token, 15-second limit and 1-MiB response limit. Other
profiles are unchanged. Existing Bing reads are narrowed to their known exact
paths/parameters; GetPageQueryStats, writes and query-string keys stay denied.

The import preserves per-page clicks/impressions and both integer average
positions as named by Bing. It preserves the provider Date string and explicitly
records unknown date granularity with weekly provider updates. It does not infer
daily metrics, complete inventory or empty-page zeroes. Valid other-origin rows
are dropped/countable; malformed, duplicate, unknown-field, oversized and
credential-echo responses are rejected with fixed nonsecret errors.

## Persistence and Read Port

Alembic migration **0079**, parent **0078**, adds the page kind to the existing
generation table plus two restricted functions. No existing migration or 0071
recorder is changed. Full current-binding and revocation checks precede durable
insertion; exact shared-egress response provenance is required. The generation
carries the observation coverage verbatim (`complete=false`,
`missing_data=unknown`), never upgraded by a successful import.

`import_bing_observation(..., kind="page_performance")` reuses the existing
OpenBao refresh/CAS/reauthorization boundary. `read_bing_page_performance` uses
an ingest-only function to return the current binding's latest page generation
or None, without credentials or provider I/O. The role is an internal trusted
worker, not a browser role. API, identity and direct table access remain denied.

0094 is **unchanged**. Follow-up: compose this read port after 0094 merges,
preserve source/provenance and coverage, and qualify the provider period before
allowing date-window measurement. Until then per-page Bing measurement remains
unavailable. Existing live qualification script still covers only 0071; extend
it in the separate provider-qualification slice.

## Threats and Tests

Malicious provider data cannot select routes, supply credentials, claim complete
coverage or insert cross-origin pages. A compromised caller cannot borrow another
profile or token for page reads. Invalid provider fields are data errors, not
instructions or values rendered in logs. Revocation during the read blocks its
commit. Persisted receipts contain credential presence/digests, never credentials.

New connector tests cover bounded strict parsing, two position fields, host,
scheme and subdomain filtering, profile negatives, secret echoes, provider
statuses and empty incomplete observations. Real PostgreSQL tests exercise the
shared gateway with synthetic provider/secret doubles, generation persistence,
verbatim coverage/readback, role and tenant mismatches, malformed SQL inputs,
immutable records, failed providers and mid-request revocation. Existing Bing
tests are unchanged.

## Verification

Stable-source full gate results and exact commands are recorded in
[0129 evidence](../evidence/0129-bing-pages.json): all nine `run-*-tests.py` labs
passed (14 journal, 36 delivery, 10 candidate, 3 consumer, 14 crawler-network,
879 PostgreSQL, 1 page-attempt, 7 Temporal, 6 workflow-image). OpenBao passed
23 cases; API/identity/tooling/connector regression passed 1,710. Focused Bing
connector regression passed 62 and the focused real-PostgreSQL file passed 5.
All counts have zero failures on final runs.

GSC boundary, ruff check, ruff format (422 files), pip check and `npm test`
passed; npm includes 43 repository tests, 154 dashboard tests, 249 Markdown
files, TypeScript and production build. Lint/format includes delivery and
page-attempt tests. The PostgreSQL migration-head assertions now expect 0079;
no earlier migration or Bing test was edited.

An initially missing pinned container image was an infrastructure interruption;
the final container labs passed once it was available. The shared virtual
environment requires `python -m pytest`; a stale prior-branch `.next` cache was
regenerated before the final passing npm gate. These retries changed no safety
gate. Labs cleaned up their own resources without stopping other tracks.

The explicit staged diff was inspected and its gitleaks scan passed with zero
findings using main's unchanged `.gitleaks.toml`. A precommit candidate tree is
also scanned over its main-based commit range; after commit and before push,
repeat the commit-range command in the evidence and record its result in the PR.
Live provider tests remain separate from these synthetic local qualifications.

## First Live Run Inputs and Limits

Owner-controlled verified HTTPS site, a current owner-confirmed Bing binding,
dedicated Bing read-only OAuth client/redirect/consent, OpenBao client and refresh
secret references with least-privilege workload access, and an admitted shared
crawler-egress context/resolver/robots/global-origin policy are required. Supply
identifiers privately, never in repository files. A disposable authorized site
must have Bing performance history. Provider response shape, page URL semantics,
position fields and reporting period/timezone need independent live qualification.

Live consent, GetPageStats, authorized import/readback and production composition
are **NOT_EXECUTED**. No customer credentials, external writes, production
authority, release qualification or measurement readiness are claimed.

## Merge Train 2

Rebased above merged Train 1 (main `7754746`) in the accepted PR order.
Migration `0079` follows `0078`; 154 cumulative app tables.
Migrations 0001-0070 and the 1200-second aggregate database budget are unchanged.
Fast checks passed: 2209 API/identity/tooling/connectors, 1046 PostgreSQL,
43 repository and 227 dashboard cases; Ruff check and format passed.
The earlier qualification above is historical; final-stack full qualification is
recorded separately in the PR comments. No live or production authority is added.

Restacked above #24 measurement integration fix `381306f`.
The fast counts above qualify this restacked source; the original commits, authors
and messages are preserved. Final-tip full qualification is recorded in the PR comments.

Also includes #24 test-only generation-order correction `9a8a85f`: explicit
fixture timestamps and equal-timestamp UUID tie coverage; production selection unchanged.
