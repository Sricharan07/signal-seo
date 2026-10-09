# ADR-0121: Lockfile-Bound Astro Dependencies And Offline Builds

Status: Accepted for internal qualification, 2026-10-02.

## Context

Astro needs dependencies; the existing candidate sandbox deliberately has no
network, credentials or host mounts. Giving repository-controlled npm scripts a
networked install would bypass shared egress and expose publishing credentials.
Owner approval divides compatibility into slices 0126 (this boundary) and 0127
(delivery after review). Revision 4.1 changes no sandbox or patch guard.

## Decision

Recognize Astro only with an observed root configuration, an explicit package
dependency and an npm lockfile. Parse configuration with pinned Tree-sitter
JavaScript/TypeScript grammars; never evaluate it on the host. Observe static
literal `outDir`/`srcDir` and content-collection entries. Dynamic configurations,
server output, other package managers and unsupported build commands are visibly
unavailable. Detection alone grants no build or delivery readiness.

Use a new closed `npm_registry` connector profile: credential-free GETs of exact
canonical public tarball URLs on `https://registry.npmjs.org`, no redirects,
metadata lookup, POST or credential headers. Existing shared-egress robots,
public-address pinning, origin admission, global backoff and immutable receipts
stay in force. It requires a real admitted crawl-run connector context; it cannot
borrow the narrower owner-onboarding connector context. Unconfigured composition
is explicitly unavailable, never a fabricated crawl or fallback HTTP client.

Validate npm v2/v3 package-lock or shrinkwrap against package.json, every resolved
package identity and canonical SHA-256/SHA-512 integrity. Reject git/file/link,
workspace and alias dependencies. Verify compressed integrity, archive paths,
regular-file types, package identity and expansion before caching. Keep public
tarballs solely in the worktree's `.runtime/npm-registry`, reverify cache hits,
reject symlinks and prune only this cache's reviewed hash filenames. Integrity
establishes consistency with untrusted source, not that a package is trustworthy.

Transfer source and verified tarballs into the pinned disposable Node container,
without mounting the host cache. Seed npm's container-only cache with local
tarballs, run `npm ci --ignore-scripts --offline`, then the exact `astro build`
script through `npm run build --ignore-scripts --offline`. Root pre/postbuild and
dependency lifecycle scripts never run. npm's lifecycle behavior is documented
in its [official ci contract](https://docs.npmjs.com/cli/v10/commands/npm-ci/), and
local tarball caching in [npm-cache](https://docs.npmjs.com/cli/v10/commands/npm-cache/).
The configuration subset follows [Astro's configuration contract](https://docs.astro.build/en/reference/configuration-reference/).

The container stays non-root, credential-free, read-only outside bounded tmpfs,
network `none`, capability-free and without a host socket. Its private workspace
allows executable installed tooling, including native binaries, under these same
isolation limits. There is no lifecycle-enabled retry. Missing generated assets
or unsupported installation/build behavior produce a typed unavailable reason.

Bind the lockfile digest before dispatch. Record it with immutable built-HTML
path/digest/title/description assertions atomically with the existing receipt.
SQL refuses unbound Astro dispatch and old receipt completion without dependency
evidence. Assertions cover all HTML artifacts and cannot omit or invent a page;
all original authority, scope, epoch, recovery and replay rules remain.

## Consequences

Bounds and unsupported cases are explicit in [0126](../implementation/0126-astro-build-boundary.md).
This is a bounded internal build path, not broad Astro compatibility or a deployed
runner. Every Astro patch is still refused, including workflow/protected paths.
No new recipe, standing-dispatch, Inbox approval, GitHub write, merge or deploy
authority is introduced. Exact intended-change and impact assertions belong to
0127 and cannot be replaced by these baseline facts. Real registry, actual Astro
engine, customer repository and live GitHub qualification are `NOT_EXECUTED`.

## Alternatives

Networked npm install, lifecycle-enabled retries, host node_modules/cache mounts,
configuration evaluation on the host and a direct registry HTTP client are
rejected. A production prebuilt dependency image would need its own release and
provenance boundary rather than an implicit bypass.

## Verification

Real PostgreSQL tests exercise the shared registry profile, cache integrity,
immutability, atomic failure, scope, epochs and receipt replay. Real isolated
containers use the pinned npm engine and a clearly synthetic package fixture to
prove offline installation, lifecycle suppression and malicious-build rejection.
The fixture is not represented as a real Astro compatibility test. The full gate
and live limitations are recorded in slice 0126's evidence.
