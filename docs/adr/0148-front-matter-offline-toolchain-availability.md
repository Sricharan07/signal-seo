# ADR-0148: Front-Matter Offline Toolchain Availability

Status: accepted for internal qualification; live providers NOT_EXECUTED.
Slice: 0139. Classification: security-critical.

## Context

Detection of front matter is not proof of safe reproducible builds. Astro has the
0126 no-network npm boundary; Hugo/Jekyll have no pinned offline toolchain.

## Decision

Reuse 0126 tarball identity/integrity verification, shared registry egress, cache
and offline container for F1 Astro, Eleventy and Next.js static export. Require
a complete npm lockfile, explicit generator dependency and exact build script:
`astro build`, `eleventy`, or `next build`. Next additionally requires one
parser-proven literal exported configuration with `output: 'export'`. Roots are
configured Astro outDir, `_site`, and `out`. Custom/unproven output, dynamic or
wrapped Next configuration and unsupported commands remain unavailable.

Every F1 seal requires passed baseline and candidate dependency receipts, the
same lockfile/base/tree, encrypted full HTML evidence, committed crawl agreement,
exact changed pages/field replacements, and unchanged remaining artifacts.
Legacy receipt paths cannot seal F1. Dependency-bound builds cannot use legacy
completion to omit receipts. Existing lifecycle-disabled, sandbox, file,
tarball, egress, source/workflow and protected-path limits remain unchanged.

Detect Hugo/Jekyll root markers and front matter but report the explicit reason
`build verification unavailable: no pinned offline toolchain`. Neither format
can prepare or seal delivery. Never install host toolchains, fetch arbitrary
binaries, execute configuration on the host or use network/lifecycle fallback.

## Alternatives

Detection-only readiness is misleading. Host-installed Hugo/Ruby, unqualified
containers and guessed build commands widen the trust boundary. New dependency
services duplicate 0126. These alternatives are rejected.

## Consequences

Synthetic packages prove the real npm/container boundary, not actual generator
compatibility. Live generator/customer/provider qualification stays NOT_EXECUTED.
0125 base acceptance and all PR journal/fence/egress restrictions stay unchanged.

## Verification

The [record](../implementation/0139-front-matter-content-adapter.md) and
[evidence](../evidence/0139-front-matter.json) distinguish local tests, unavailable
non-npm toolchains and the owner's remaining live inputs.
