# Slice 0126: Astro Dependency And Build Boundary

Classification: **security-critical**. Decision: [ADR-0121](../adr/0121-astro-offline-build-boundary.md).
Stacked on slice 0125. Migration **0077**, following 0076; two new
forced-RLS, function-only immutable tables. Accepted specifications and historical
migrations are unchanged. This slice does **not** enable Astro delivery.

## Scope

0079 format observation now requires `astro.config.js/mjs/ts/mts`, the explicit
`astro` dependency, and one npm package-lock/shrinkwrap. A complete bounded
checkout supplies independent blob evidence. Configuration is parsed, not executed
on the host, with pinned Tree-sitter and its JavaScript/TypeScript grammars. Literal
static output/src directories and content-collection entries are observed;
dynamic or server configuration remains detected but unavailable for building.

The new typed, closed shared-egress profile permits only public official registry
tarball GETs. It has no authorization, request body, redirects, package metadata,
write endpoint or credential fallback. Tarballs must match canonical SHA-256 or
SHA-512 lockfile integrity and package identity. Package-lock v2/v3 and shrinkwrap
are supported; workspaces, aliases, git/file/link packages, missing integrity and
nonofficial URLs are refused. Existing robots, pinning, politeness, backoff,
admission and durable provider evidence remain mandatory.

The cache is `.runtime/npm-registry` inside the configured worktree, containing
only public tarballs, never provider credentials. Cache hits are reverified before
use. Symlinks/nonregular entries are refused, writes use no-follow/exclusive files
and a fail-closed write lock, and pruning only removes this cache's hash filenames.
Invocation-owned lab caches are deleted after each test.

Source and cache bytes are transferred, not mounted, into the existing pinned
Node 22.18 container. Local `npm cache add` seeds a private container cache, then
`npm ci --ignore-scripts --offline` and `npm run build --ignore-scripts --offline`
execute without network. The only admitted Astro build script is `astro build`.
User/global npm configuration is explicitly isolated, audit/funding are disabled,
and repository .npmrc, .env, node_modules and reserved generated roots are refused.
Dependency install and root pre/postbuild lifecycle scripts do not execute. Builds
needing lifecycle-produced assets remain unavailable with the reason; no retry
weakens this boundary. Custom commands, adapters and unsupported configurations
do not silently receive a broader build profile.

## Bounds

- Existing checkout limits: 256 source files, 128 KiB per blob, 8 MiB total.
- At most 512 distinct locked package entries; each tarball at most 5 MiB.
- Registry fetch at most five seconds per request and 600 seconds per manifest.
- Public cache and transferred dependencies at most 128 MiB; expanded tar data
  at most 64 MiB per package and 256 MiB per manifest, at most 4,096 members each.
- Container: network none, no mounts, non-root, read-only base, no capabilities,
  private executable 512 MiB workspace, 768 MiB memory, 256 MiB Node heap,
  0.5 CPU, 32 PIDs, bounded descriptors/files. No host cache or socket is exposed.
- Offline install and build each at most 120 seconds; combined logs 64 KiB.
- Existing output bound: 16 MiB, 1,000 artifacts. HTML is UTF-8, at most 512 KiB
  per page; title/description assertions each at most 4 KiB and receipt 128 KiB.

Oversized real dependency graphs or lockfiles are explicitly unavailable under
these bounds, not evidence that the customer's repository is supported.

## Durable Receipt

The preparation hash includes the source lockfile digest, bound immutably before
dispatch. An Astro build cannot use the old completion function to omit dependency
evidence. Completion atomically stores both the original receipt and fixed offline
command evidence, lockfile digest, every built HTML page's path/digest/title/meta
description and any typed unavailable reason. Positive page assertions must match
all HTML artifact hashes exactly; omitted, duplicated or invented pages fail.
Receipt mutation and conflicting replay are refused. Current Owner, verified site,
scope, authorization epochs, recovery and existing receipt guards remain.

The builder rejects any modified source, workflow or undeclared output; only the
declared output directory can add artifacts. Baseline facts do not prove an SEO
change. All Astro patches are refused with `ASTRO_DELIVERY_UNAVAILABLE`. Per-page
recipes, dynamic/shared-template classification, impact counts/sample diffs,
fresh-MFA approvals and exact before/after scope assertions are deferred to 0127.

## Qualification

The final frozen-source full gate passed: 902 PostgreSQL, 37 PostgreSQL/Temporal
delivery, 1,721 API/identity/tooling/connector and 14 real sandbox cases, plus all
other requested labs, repository/dashboard tests and formatting/build checks.
Exact commands, counts and source-hashed cleanup are in
[the evidence record](../evidence/0126-astro-build-boundary.json).
The first delivery run passed 36/37, failing in the existing crawl robots setup
before candidate preparation. An unchanged full rerun passed 37/37; the root cause
is not established and this fixture/timing risk is not hidden. An initial
page-attempt Docker setup failure also passed on an unchanged retry. No code,
test, safety gate or secret-scan allowlist was weakened to obtain these results.
Synthetic fixture packages exercise the real pinned npm install/build boundary,
not the actual Astro package or a customer build. Live GitHub, official-registry
fetches, actual Astro-engine compatibility, private customer repository and
production composition remain **NOT_EXECUTED**. No production write is enabled.

## First Live Run

The integration thread needs the owner-selected verified site/repository and
current App binding, private OpenBao configuration, exact admitted GitHub and
official-registry shared-egress contexts with valid robots evidence, a current
Owner session, the pinned sandbox image and a worktree-local cache directory.
Do not fabricate a crawl to obtain a registry context; if a valid configured
context is absent, the internal service returns `NPM_EGRESS_UNAVAILABLE`. The
narrower owner-onboarding context is deliberately not widened or borrowed here.
Inspect the actual lockfile/graph against these bounds, exercise integrity and
offline/lifecycle failure cases, and retain redacted receipts. The owner and
integration thread must separately qualify real Astro compatibility. Delivery
remains unavailable until 0127 is reviewed and independently qualified.

## Merge Train 2

Rebased above merged Train 1 (main `7754746`) in the accepted PR order.
Migration `0077` follows `0076`; 152 cumulative app tables.
Migrations 0001-0070 and the 1200-second aggregate database budget are unchanged.
Fast checks passed: 2135 API/identity/tooling/connectors, 1022 PostgreSQL,
43 repository and 222 dashboard cases; Ruff check and format passed.
The earlier qualification above is historical; final-stack full qualification is
recorded separately in the PR comments. No live or production authority is added.

Restacked above #24 measurement integration fix `381306f`.
The fast counts above qualify this restacked source; the original commits, authors
and messages are preserved. Final-tip full qualification is recorded in the PR comments.

Also includes #24 test-only generation-order correction `9a8a85f`: explicit
fixture timestamps and equal-timestamp UUID tie coverage; production selection unchanged.
