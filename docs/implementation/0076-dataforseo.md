# Slice 0076: Optional DataForSEO Adapter

Security-critical R1 slice: Revision 4.0 sections 11 and 18, EC-138, INV-029,
INV-031, INV-032 and INV-033; Revision 3.2 sections 6, 10, 11 and 20 remain in
force. Live DataForSEO: **NOT_EXECUTED**, no owner account supplied. No production
release or publishing authority is established.

## Boundary

Migration **0064**, directly following unchanged **0063** on the merge train, adds
site settings, immutable settings events, call reservations and terminal receipts.
All tables force tenant/site RLS and are function-only for runtime roles.
The API role can configure and read through current owner authority; ingestion
can reserve/record calls; admission can bind the paid profile. No role can edit
or delete a sealed receipt. ADRs [0104](../adr/0104-optional-site-bound-dataforseo.md)
and [0105](../adr/0105-reserve-paid-research-before-egress.md) record the choices.

The dashboard Connectors band has masked login/password fields, credential
removal, a monthly USD cap and usage including unresolved holds. The same-origin
BFF streams at most 2048 request bytes, obtains session CSRF server-side and
returns only the closed nonsecret settings projection. API commands are parsed
without credential-bearing validation errors. No credential is returned to the
browser, persisted in SQL, or placed in model context.

The OpenBao mount `signal-dataforseo` is KV v2, with CAS required and one retained
version. Secret paths are `data/{tenant_id}/{site_id}/{credential_generation}`;
the payload has exactly `login` and `password`. Setup has create/read and metadata
delete on its authorized site paths; executor credentials have read only. Removal
disables SQL dispatch first and permanently deletes the old metadata. Failed
removal is an explicit unknown cleanup outcome, not a success. Settings events
retain nonsecret old generations for operator cleanup. Restores must not resume
provider egress until surviving secrets and outstanding call intents reconcile.

## Calls and Evidence

`dataforseo_service.execute_call` accepts an immutable operation UUID, exact
tenant/site and credential generation, a `DataForSeoQuery`, a composed connector
`SharedEgressProvider`, and optional conservative estimate. It commits the hold
before I/O. Reservations serialize on the site row, including concurrent calls.
Cap exhaustion returns `unavailable` and an explicit failure code. Replays never
reissue a request, including unknown outcomes and process death after reservation.

The `DATAFORSEO` profile permits only `https://api.dataforseo.com`, POST, JSON,
4096 request bytes, 128 KiB response bytes, 30 seconds and these exact routes:

- `/v3/serp/google/organic/live/advanced`: one keyword, location/language, depth
  10, maximum one page; paid search operators and percent/plus escapes denied.
- `/v3/keywords_data/google_ads/search_volume/live`: one keyword and explicit
  location/language; missing volume is null, never a fabricated zero.
- `/v3/backlinks/summary/live`: one normalized competitor domain, including
  subdomains; no URL, arbitrary filters or pagination.

Basic authentication must match the typed OpenBao credential scope. Before fetch,
the database binds the reservation/body identity, current generation, month and
cap to the shared-egress operation. Existing robots, global admission, screened
resolver, numeric-peer pinning and no-redirect behavior remain mandatory.

Envelope/task identities, status/cost fields, echoed queries and endpoint paths
are checked before accepting data. Duplicate JSON fields, nonfinite numbers,
invalid counts/types, mismatched subjects/locales, invalid URLs/ranks and oversized
responses fail explicitly. Provider prose and additional metadata are untrusted,
not instructions. The business record stores only typed competitor rank/domain/URL
lists, search volume, or backlink/referring-domain/rank summary, with a response
digest and matching observed shared-egress evidence. Credential echoes are denied.

Read API: `GET /v1/sites/{site_id}/dataforseo` (current owner, no-store) returns
availability, feature availability, UTC month, cap, usage and the latest 100
evidence-linked call records. `POST` accepts only `credential`, `remove`, and
`cap` commands. `control.dataforseo_call` is the ingestion read port by exact
tenant/site/call ID. No Strategy, Competitor Research or 0077 behavior is changed.

## Pricing Snapshot

Reviewed 2026-10-02 against official [SERP pricing](https://dataforseo.com/pricing/google-serp/google-organic-serp-api),
[Google Ads pricing](https://dataforseo.com/pricing/keywords-data/google-ads) and
[backlinks pricing](https://dataforseo.com/pricing/backlinks/backlinks): reserve
USD 0.002 for one base live SERP, USD 0.09 for one live volume task and USD
0.024036 for a backlink task plus one summary row. Provider-reported USD costs
are converted exactly to integer millionths. Unknown/rejected calls retain their
hold; a larger reported cost is never discarded. Price changes require review or
a larger configured estimate. No retries, automatic unknown-cost release or
provider subscription readiness is simulated.

Official contracts: [SERP](https://docs.dataforseo.com/v3/serp-se-type-live-advanced/),
[volume](https://docs.dataforseo.com/v3/keywords_data/google_ads/search_volume/live/),
[summary](https://docs.dataforseo.com/v3/backlinks/summary/live/).

## Composition and First Live Run

`create_app(browser_dataforseo=ComposedDataForSeoGateway(...))` composes a narrowly
scoped API connection factory, OpenBao settings writer, and independent recovery
authority. The default is unavailable, not a hidden dependency. Only a configured
private executor composition may set `execution_configured`; a stored credential
alone does not make research available. Production deployment, automated research
scheduling and live-account qualification remain `NOT_EXECUTED`.

Owner inputs: a dedicated disposable DataForSEO account with API login/password,
paid credit and Backlinks API entitlement; a verified disposable site; owner
session and recovery authority; the narrowly scoped TLS OpenBao mount; a private
connector-purpose shared-egress run/policy/current robots evidence for the official
API origin; reviewed pricing/estimate and a small monthly cap. Enter secrets only
in the dedicated dashboard form, never shell history, chat, environment files or
the repository. Call each typed query once with a fresh operation ID, inspect
cost/egress/record identity, then remove the credential and verify dispatch denial.
No live run command or account result is claimed in this record.

## Verification

Exact commands, counts and limitations are recorded in
[0076 evidence](../evidence/0076-dataforseo.json). Tests use synthetic secrets,
real disposable PostgreSQL and OpenBao, and provider doubles behind shared egress.
Required full labs, static checks, dashboard/repository checks and secret scanning
are mandatory before commit. Unknown provider outcomes remain explicit.

## Merge-Train Verification

PR #14 is rebased onto the preceding accepted train head. Migration 0064
follows unchanged 0063; the cumulative app-table assertion is 110.
All five requested fast checks passed. The original qualification above is
historical; current counts and commands are in the `merge_train` entry of
[the evidence](../evidence/0076-dataforseo.json). No production authority changed.
