# Slice 0128: Page Speed And Core Web Vitals

Classification: **security-critical provider, credentials and egress**.
Status: **IMPLEMENTED; LOCAL REGRESSION QUALIFIED; NOT DEPLOYED**.
Revision 4.0 sections 9.2, 11 and 19, INV-018, and the retained Revision 3.2
performance, evidence and network requirements apply. Decisions:
[ADR 0125](../adr/0125-closed-pagespeed-egress.md) and
[ADR 0126](../adr/0126-source-separated-core-web-vitals.md).

## Implemented

PSI v5 `runPagespeed` uses the closed `PAGESPEED` profile in `shared_egress`:
GET, exact Google API host/path, exact query set, mobile/desktop, performance
category, same verified HTTPS origin, 512 KiB and 25 seconds. Existing public DNS
screening, pinned TLS, no redirects, robots checks, shared politeness and backoff
are unchanged. Current MFA-owner/session/recovery/site authority is checked before
any network activity. The optional key is read only from an exact OpenBao KV v2
path; absent configuration means keyless quota, not readiness failure. Unreadable
configured secrets do not fall back. Keys never enter receipt URLs, rows, request
hashes, fixed errors or raw response storage.

Migration `0083`, after `0082` on this base, adds immutable function-only forced-RLS
samples, observations and failures, the closed provider admission and owner read
projection. It preserves existing owner-egress authority and short permits.
The JSON parser rejects duplicate keys, invalid scope, malformed numbers/times,
invalid source identities, unsafe/mismatching redirects, and oversized responses.
SQL independently validates the stored closed numeric projection and exact receipt,
body digest, sample URL/strategy, source locator and finding linkage.

Evidence records URL, strategy, fetch time, Lighthouse version and lab run time,
LCP/FCP/TBT/Speed index/CLS and performance score, plus separate URL/origin CrUX
p75 LCP/INP/CLS and reported collection periods. Missing or insufficient field
metrics and missing periods retain unavailable reasons; origin fallback is not
reported as page data. Lab data never supplies field INP or estimates.
Poor field CWV values produce only source-linked 0068 audit-model observations,
using the documented thresholds. No automated fix recipes or external writes.

The internal weekly scheduler seals at most five successfully crawled exact-origin
pages for both strategies. Fresh (at most 35 days) current-bound GSC web clicks
rank known pages, including a page dimension that is not the first dimension;
otherwise crawl order determines the sample. SQL serializes the four-request
per-site UTC daily cap before robots/provider admission. Failed and ambiguous
dispatches consume the cap. Exact replay never resends a provider request.
The collector stops on rate limits or provider backoff and stores fixed unavailable
codes rather than fabricated measurements.

`GET /v1/sites/{site_id}/performance` and its same-origin BFF require the exact
session cookie and current MFA-owner authority. The Pages dashboard labels page
field, origin field and Lighthouse lab separately, exposes collection-period gaps,
and links evidence IDs/digests and observation-only findings. Unknown, scheduled,
failed, empty and loading states remain distinct. Local Lighthouse is unavailable.

## Verification

The [evidence record](../evidence/0128-page-speed.json) lists exact commands and
counts. Provider tests use synthetic credentials and doubles behind the actual
shared gateway. Database authority, admission and persistence tests use disposable
real PostgreSQL; optional-key isolation also uses disposable TLS OpenBao.
All 16 pre-commit gate commands passed: 893 PostgreSQL, 14 authority-journal,
36 autonomy-delivery, 10 candidate-sandbox, 3 consumer, 14 pinned-network,
1 page-attempt, 7 Temporal, 6 consumer-image, 27 OpenBao, 1,715 non-database
Python, 43 repository and 162 dashboard cases (2,931 cases, zero failures).
The GSC boundary, Ruff check, 425-file format check, dependency check, 249-file
documentation check, typecheck and production build passed. All integration
report source hashes match the qualified source; invocation-owned cleanup completed.
The staged secret scan used unchanged main configuration with zero leaks; the
post-commit `main..HEAD` scan is required before push and recorded in the PR.
Earlier new-slice fixture/head/count and exception-chaining failures were repaired
without weakening gates, followed by the full stable-source rerun. The observed
shared Next installation was 16.3.4 (declared 16.3.8); shared dependencies were not
modified and this record does not claim a clean locked-dependency installation.
Desktop/mobile captures exercise the actual component and CSS with a labelled
synthetic fixture, not a live PSI claim: [desktop](../evidence/0128-pagespeed-desktop.png)
and [mobile](../evidence/0128-pagespeed-mobile.png). A fresh review qualified the UI
at that scope; incumbent design documentation and pre-existing detector drift
remain unchanged. Six hydrated synthetic browser checks qualified initial read,
keyboard refresh/focus and evidence disclosure, failure clearing, retry recovery
and keyed selected-site isolation. The local dev server was checked on loopback
at `http://127.0.0.1:43128` and stopped; no test listener remains running.

## Remaining Gates And First Live Inputs

Live PSI is **NOT_EXECUTED** by instruction. Local browser-sandbox Lighthouse
remains **UNAVAILABLE**, with no changes to slice 0067. No customer data,
production traffic, new deployed service, standing authorization or external-write
capability is enabled. The internal daily collector needs a current owner context
on each invocation. There is no stored-session worker, deployed periodic trigger
or newly enabled unattended schedule; that composition needs separate qualification.
This branch was created from the assigned `4f3aca5` base. Upstream merge sequencing
and migration-chain reconciliation remain the integrator's responsibility; no other
track branch or existing migration was modified.

A first approved live run needs a verified HTTPS site with a completed crawl,
current MFA-owner session and recovery generation, the existing least-privilege
database/egress roles and protected robots artifact backend/key, and privately
configured screened Google API network admission. Optionally provision a PSI key
in OpenBao with an exact read-only ACL and workload token; otherwise explicitly
use the provider's keyless quota. No key should be supplied in HTTP request bodies,
database rows, shell arguments or public run records. GSC ranking additionally
needs a current property binding and fresh page-click generation; otherwise the
documented crawl-order fallback applies. Actual quota/field availability must be
observed on that run, never inferred from doubles.

Provider documentation: [PSI getting started](https://developers.google.com/speed/docs/insights/v5/get-started),
[PSI field and lab data](https://developers.google.com/codelabs/chrome-web-vitals-psi-crux),
and [CWV thresholds](https://web.dev/articles/defining-core-web-vitals-thresholds).

## Merge Train 3

Rebased in the accepted order above main `168510b` (head 0079).
Migration `0083` follows `0082`; 160 cumulative app tables.
Frozen migrations 0001-0079, specification hashes and the 1200-second database
subprocess budget are unchanged. Fast checks passed: 2586 API/identity/tooling/connector
cases, 1083 PostgreSQL cases (including the runner's repeated Brain checks),
46 repository and 242 dashboard cases; Ruff check and format passed.
Earlier qualification above is historical; full-stack results are recorded at the
final tip. No live-provider or production authority is added.
The slice-owned OpenBao qualification passed with the new optional PageSpeed
credential and keyless paths; only invocation-owned infrastructure was cleaned.
