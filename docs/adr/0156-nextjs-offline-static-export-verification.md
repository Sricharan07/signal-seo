# ADR-0156: Next.js Offline Static Export Verification

Status: accepted for internal qualification; live builds NOT_EXECUTED.
Slice: 0143. Classification: security-critical.

## Context

Next.js [static export](https://nextjs.org/docs/app/guides/static-exports)
produces inspectable HTML; server-rendered deployment does not establish that
static build proof. [Google font integration](https://nextjs.org/docs/app/api-reference/components/font)
can download fonts during build. Repository build output is untrusted data.

## Decision

Detection retains App/Pages Router observations separately from deliverability.
An inventory without observed configuration reports `NEXT_STATIC_EXPORT_UNOBSERVED`.
A verified checkout must have the existing conservative literal `output: 'export'`
configuration, pinned npm graph and exact `next build` script. Otherwise report
**build verification unavailable: Next.js requires a literal static export**.
Wrapped/dynamic configuration is unavailable, not executed on the host.

Reuse 0126 shared registry egress and integrity cache. The build container has no
credentials and uses `--network none`; install is `npm ci --ignore-scripts --offline`
and build is `npm run build --ignore-scripts --offline`, invoking `next build`.
Next telemetry is disabled. No network-enabled retry exists. Observed
`next/font/google` imports report **build verification unavailable: Next.js
requires network access; network disabled** before container execution.
Other network-needing builds fail in the sandbox. Bounded log markers select a
closed diagnostic reason only; log instructions cannot change execution/policy.
Failures, resource limits and rejected output retain a durable unavailable reason.

Baseline and candidate must retain the same base/tree/lockfile. Every HTML file
and artifact is inventoried. Only declared target fields may differ, and every
other byte, including assets and non-target HTML, must be unchanged. Flat export
paths (`out/about.html`) map to `/about`, while `index.html` maps to its directory.
Failed/missing/unavailable build receipts cannot seal a candidate.

## Consequences

Server mode and builds requiring fonts, remote data/images or other network
resources are unavailable; owner approval cannot override isolation. Ordinary
Next builds may also change RSC payloads or hashed assets; those extra changes
fail the existing exact-artifact assertion rather than being exempted. Actual
engine compatibility requires a future live qualification with retained receipts.
Synthetic CLI packages qualify offline npm execution and the proof boundary only.
This slice does not simulate real-repository readiness or production authority.

## Verification

Run the commands in [0143](../implementation/0143-nextjs-metadata-delivery.md).
Container tests cover paired App/Pages builds, multi-page impact, lifecycle and
telemetry disabling, and denied network attempts. PostgreSQL checks persist the
closed reasons and deny sealing failed builds. Results and explicit NOT_EXECUTED
items are in [evidence](../evidence/0143-nextjs-metadata.json).
