# Slice 0155 - Data Providers

Classification: **Security-critical**, Revision 4.0 section 19: connector OAuth,
credentials, paid calls, egress and standing spend authority. Based on `1923beb`,
branch `slice/0155-data-providers`, above train 4's 0090/0091/0092. Rebase and
integrated qualification await the train's merge.

## Implemented

- DataForSEO: one bounded service invoked by owner Strategy refresh and weekly
  StrategySkillPort. Maximum five topic/idea subjects, volume then SERP per subject;
  optional maximum two returned competitor backlink summaries. Locale/backlinks
  are explicit operator configuration. Existing adapters/OpenBao are reused.
- 0098 (merge train 5; written as 0093 after 0092) adds one immutable forced-RLS function-only authority/intent mapping (179
  app tables). Closed workflow ports atomically reserve monthly micro-USD and
  standing weekly cents/volume before secrets/I/O, and recheck authority, credential,
  body, month and caps at dispatch. Denials roll back both ledgers. Unknown calls
  retain holds; rotation/new intent/month cannot bypass unresolved identical queries.
  Overruns occupy both ledgers; reservations are not refunded automatically.
- Shared egress keeps pinning, origin serialization/backoff, encrypted robots and
  immutable outcomes. Existing profiles were not widened. One new exact
  uncredentialed DataForSEO robots profile is allow/deny tested. Only pre-dispatch
  deferrals can repeat the same intent, for at most five seconds. No dependency,
  search-engine scrape or Signal-operated service is added.
- Strategy shows dated volume/SERP/backlink receipts and provenance. Reported demand
  adjusts bounded topic priority, never predicts rankings. Provider-only topics need
  approved facts/crawl evidence before brief work. Provider receipts stay separate
  from model ideas/site cohorts. Missing, unconfigured, capped and held states use
  plain words; cached evidence can remain usable when further research is capped.
- Bing: `/v1/sites/{site_id}/bing`, `/auth/bing` and fixed callback expose
  authorize/complete/confirm/revoke through current-owner, five-minute MFA freshness,
  verified-site and browser mutation-proof checks. Only the exact verified site is
  confirmable. Single-use state and private OpenBao TLS options are retained. More
  data contains the shared status pill, owner-only actions and Technical details.
- Weekly Bing imports request both performance kinds with independent evidence.
  The page recorder keeps 0129's complete validation plus closed stage/tenant/binding
  admission. Strategy exposes both named positions with incomplete top-page and
  unknown-granularity semantics. Existing 0144 measurement already reads `bing_page`;
  weekly input is now supplied. GSC/Bing are never combined. Missing page-days remain
  unknown. 0144 had already removed the reported stale measurement sentence; this
  slice adds the remaining position/granularity caveat.

See [ADR-0165](../adr/0165-provider-data-in-strategy.md).

## Composition

Inject `StrategyDataForSeoResearch` into `ComposedBrowserLogin.strategy_research`
and `local_skill_ports(..., strategy_research=...)` or
`WeeklyActivities(..., strategy_research=...)`. Supply the existing workflow
connection factory, OpenBaoDataForSeoCredentials, explicit locale and
StrategyProviderEgressFactory with configured public-address resolver, protected
artifact store/key and private workflow connections. Set the settings gateway's
execution flag only when this executor is installed. No default credentials,
keyless fallback or synthetic production composition exists.

Inject ComposedBingGateway into `create_app(browser_bing=...)` or existing late
composition state with identity connections, current recovery authority, OpenBao
Bing secrets/private TLS and narrowly configured shared owner egress. The incumbent
dedicated-runtime pin/AppRole deployment is unchanged and does not claim new provider
readiness. Private provisioning/live qualification is required before enabling it.

## Verification

Final counts/outcomes: [0155 evidence](../evidence/0155-data-providers.json).

API/identity/tooling/connectors: 2,972 passed. Provider/strategy lab: 83 passed.
Full database lab: 1,345 passed on three PostgreSQL 17.11 shards, stable source,
cleanup completed. OpenBao: 45 passed. Root npm: 46 repository and 285 dashboard
tests, 336 Markdown files, typecheck/build passed; dashboard repository guards
unchanged. Ruff Python-directory check/format passed (558 formatted files).

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q --junitxml=.runtime/0155-fast.xml
.venv/bin/python -m ruff check apps/api services/control_plane scripts tests
.venv/bin/python -m ruff format --check apps/api services/control_plane scripts tests
npm test
.venv/bin/python scripts/provider_data_lab.py
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/openbao_lab.py
```

The provider/strategy lab includes DataForSEO, Bing binding/pages, Strategy, topics
and weekly orchestration with invocation-owned real PostgreSQL and synthetic HTTP
through real shared egress. OpenBao uses actual TLS/ACL/CAS/rotation/destruction with
synthetic credentials. Positives cover owner/weekly paid receipts/cache/provenance
and both Bing imports. Negatives cover both caps, owner/MFA/site/tenant/generation/
pause, exact egress and browser mutation/callback boundaries. Failures cover unknown
transport, rotation, overrun, optional unavailability and safe errors. Existing labs
also cover malformed/echoed secrets, provider auth/rate/failure, binding revocation,
immutability and source-separated measurement.

Disk checked above 4 GiB before heavy labs. No full gate/Temporal started; only
invocation-owned disposable resources cleaned. No foreign containers stopped or
images pruned. Specifications/earlier migrations are unchanged.

The broader `ruff format --check .` also inspects legacy Markdown code examples:
it reports three pre-existing specification files unformatted. They remain
byte-for-byte unchanged; no specification was reformatted to satisfy a formatter.
Synthetic SSR/Playwright Connections and Strategy screenshots at 1440/390px were
inspected with no horizontal page overflow and zero design-detector findings.

## Limits

Live DataForSEO/Bing positive, negative and failure qualification, authenticated
owner journey, dedicated deployment and production use are **NOT_EXECUTED**. No
provider account/credentials were supplied. Optional services remain unavailable
without qualified composition. Unresolved-query reconciliation is intentionally
absent; holds need explicit investigation, not blind retry. Backlinks are off by
default. Bing has no query dimension; topics remain unavailable and positions
stay per source/as reported.

Rebase/renumbering and integrated full gate belong to the merge train. No PR,
main push/merge or production writes are authorized by this slice.
