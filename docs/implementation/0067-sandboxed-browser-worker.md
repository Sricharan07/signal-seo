# Slice 0067: Sandboxed Browser Worker

Status: **INTERNAL IMPLEMENTATION; LOCALLY QUALIFIED; PRODUCTION UNAVAILABLE**.

## Contract

Security-critical: Revision 4.0 section 9, REQ-023, INV-028/029/031/032,
EC-024/131/132/133/134, plus Revision 3.2 sections 2, 11 and 13.
No authority, connector secret, publishing capability or production release is added.
ADRs [0078](../adr/0078-isolate-browser-egress-through-a-policy-pipe.md) and
[0079](../adr/0079-bound-browser-choice-to-recorded-link-ids.md) explain the boundaries.

## Implemented Boundary

- `BrowserWorkerService` exposes bounded internal `render_url`, `read_page` and
  `verify_deployed_page` methods. Missing composition returns `unavailable`.
  Verification requires `SealedBrowserFragment` with matching UTF-8 SHA-256 and
  compares visible rendered text; this does not correlate a deployment identity.
  0085's existing independent GET verification is untouched.
- Each session has a private internal Docker bridge in isolated gateway mode with
  exactly two containers: worker and proxy transport. Neither has an internet
  route, host gateway, host mount, published port or credentials. Browser image
  identity must be an immutable SHA-256 digest. Non-root, read-only roots, tmpfs,
  dropped capabilities, no-new-privileges, default seccomp, one CPU, 512 MiB and
  128-process limits are enforced by the runner, not caller options.
- Playwright 1.63.0 and its pinned browser-only Python dependency graph install
  Chromium headless shell only. No dependency is added to the control-plane
  environment. The OCI container is the sandbox qualification boundary; a separate
  Chromium user-namespace sandbox is not claimed.
- HTTP(S) routes use only the credential-free forward proxy. CONNECT and all
  non-read verbs, request bodies, cookies, authorization, upgrades and redirects
  are denied. The trusted pipe handler invokes `execute_shared_egress`, retaining
  public-address screening, numeric-peer pinning/TLS, current robots, durable
  dispatch and shared global-origin admission. Bounded deferrals wait without
  changing politeness; uncertain dispatch is never repeated.
- The closed browser-read profile accepts GET/HEAD and a bounded media list for
  HTML, text, CSS, JS, JSON, images and fonts. All subresources require their own
  admitted origin and robots/admission evidence. No external asset exception is
  inferred. Search-engine and assistant consumer origin families are denied even
  if present in the caller's exact origin scope.
- Worker actions are exactly navigate, recorded-link GET, scroll, bounded idle
  wait, reduced AX-tree read and screenshot. Forms, buttons, inputs, typing,
  uploads, downloads, dialogs, extra windows, service workers, WebSockets and
  unsolicited document/frame navigation have no permitted interaction path.
  Dialogs are dismissed; Chromium download behavior is denied.
- Link reduction keeps bounded text, URL, role and id, prunes deterministically to
  255/16 KiB, and excludes authentication/transaction links. Jev uses the existing
  recorded Choice path. Invalid or low-confidence choices, model rejection and
  every labelled fallback stop. Page text remains data. The planner makes at most
  one link choice; there is no frontier planning or vision.
- The Jev wait shares the proxy/session deadline. Expiry records a labelled
  deterministic timeout and tears down the containers without following a link.
- Snapshots and PNG screenshots use the existing AES-GCM artifact store and
  authenticated readback, with catalog and upload-readback attestations committed
  together with the immutable step. Artifact retention is 30 days. Session limits
  are 1-20 actions, 1-120 seconds and 1 KiB-20 MiB; default 8/60/5 MiB. The proxy
  additionally caps request count at 64 and individual reads at the crawl bound.
  Uncertain/failed transfers retain conservative byte reservations. Terminal
  evidence retains screenshot usage. Incomplete reads never become verification.

## Data And Migration

The worker uses the distinct `browser_worker_read` shared-egress profile. The
pre-existing `browser_read` profile remains HTML-only GET, unchanged. The worker
profile carries the reviewed credential-free GET/HEAD asset allowlist through
the same robots, origin admission, pinning, byte limits and immutable receipts;
it cannot borrow another connector's credentials or scope.

Migration **0072**, following unchanged **0071**, adds `app.browser_sessions`,
`app.browser_steps` and `app.browser_step_egress`, all forced-RLS and append-only.
Runtime roles have function-only access. Session records bind tenant/site/running
crawl, goal, start URL, image digest, purpose, limits and sealed-fragment digest.
Steps bind sequence, action/outcome, goal, element/snapshot digests, choice/confidence
or labelled fallback, resulting URL, byte accounting, encrypted artifact ids and
decision id. Egress references bind the existing immutable operation/admission and
robots snapshot to the same site/run. No raw page text or screenshot is stored in
PostgreSQL. Terminal sessions cannot accept another step.

## Verification

```sh
.venv/bin/pytest -q tests/tooling/test_browser_policy.py
.venv/bin/python scripts/run-browser-worker-tests.py
.venv/bin/python scripts/run-database-tests.py
```

The browser lab uses real Docker and PostgreSQL with synthetic origins and a Jev
protocol double behind shared egress. It proves direct-network failure from inside
the worker, proxy screening/robots/rate/redirect/size denial, allowed rendering,
inert injection, worker action rejection, bounded choices, encrypted evidence and
session/container/network cleanup. A dedicated BuildKit builder owns its cache;
the lab removes that builder and its exported image, never shared cache or another
track's containers. Reports are in `.runtime/browser-worker/latest.json`.
The [evidence record](../evidence/0067-sandboxed-browser-worker.json) contains exact
commands, final counts, source hashes, image identity, cleanup and failure accounting.
The final local gate passed **2,446 cases, 0 failed**: browser 35, cumulative
PostgreSQL 840 (9 new browser-record cases), API/identity/tooling/connectors 1,285,
other Docker/Temporal/OpenBao/journal labs 114, repository 24 and dashboard 148.
The supplemental focused policy run passed 42/0, also covered by the wider Python
suite. Every `scripts/run-*-tests.py` lab, OpenBao and GSC boundary checks, full
Ruff checks (including delivery/page-attempt), pip consistency, dashboard typecheck
and production build passed. Staged secrets scanning used main's unchanged config;
the commit-range scan runs after commit and its result is reported in the PR.

## Live Run Inputs And Limits

Live Jev, real owner-site browser qualification, production image promotion,
deployed worker composition and signed release admission are **NOT_EXECUTED**.
Lighthouse, Core Web Vitals, frontier planning and vision stay **unavailable**.
No browser route or dashboard control is exposed and no weekly or delivery path
is automatically switched to this worker.

A first live run needs an owner-controlled public site and explicit admitted
origins for its required assets; current running read/workflow authority; current
robots evidence prepared through 0065; scoped admission, ingest and workflow
connections; an encrypted artifact backend and OpenBao-sourced artifact key; a
reviewed exact browser image digest; and an engine supporting isolated bridge
gateways. Optional Jev additionally needs its OpenBao credential and separately
admitted TypeSafe shared-egress context. None of these secrets enter a browser
container. Use synthetic data until the applicable provider/release gates pass.

## Merge Train 2

Rebased above merged Train 1 (main `7754746`) in the accepted PR order.
Migration `0072` follows `0071`; 133 cumulative app tables.
Migrations 0001-0070 and the 1200-second aggregate database budget are unchanged.
Fast checks passed: 1948 API/identity/tooling/connectors, 975 PostgreSQL,
43 repository and 203 dashboard cases; Ruff check and format passed.
The earlier qualification above is historical; final-stack full qualification is
recorded separately in the PR comments. No live or production authority is added.

Restacked above #24 measurement integration fix `381306f`.
The fast counts above qualify this restacked source; the original commits, authors
and messages are preserved. Final-tip full qualification is recorded in the PR comments.

Also includes #24 test-only generation-order correction `9a8a85f`: explicit
fixture timestamps and equal-timestamp UUID tie coverage; production selection unchanged.
