# Crawler HTTP Boundary Runbook

This runbook covers the qualified URL/destination/fetch boundary from Slice 0034.
It is not a production crawler startup guide. No production resolver, workflow
registration, public egress, or customer authority exists.

## Local Qualification

Prerequisites:

- Docker Engine is responsive;
- at least 2 GiB is free;
- the locked Python environment is installed; and
- no source file changes during the run.

Run:

```sh
.venv/bin/python scripts/run-crawler-network-tests.py
```

The command builds one disposable image, creates one internal/non-masqueraded
bridge, starts one synthetic HTTP origin with a public-shaped address, executes
five real socket cases, records JUnit/JSON evidence, and removes only resources
carrying that invocation's label.

No host port or host path is published. The test client and origin run as UID/GID
10001 with read-only roots, dropped capabilities, no-new-privileges, and bounded
resources. The synthetic origin has no production credential or customer data.

## Expected Evidence

`.runtime/crawler-network/latest.json` must show:

- exactly five passing cases and no skip/failure;
- Docker server and local test-image identities;
- the pinned Python base digest;
- internal network true, IP masquerading false, and the expected subnet;
- `production_authority` false; and
- cleanup `completed`.

The checked-in evidence copy is
`docs/evidence/0034-crawler-network.json`. Its `source_sha256` map must match the
current Dockerfile, boundary modules, lab, CI/static assertions, and tests. A stale
hash means rerun; do not edit evidence by hand.

## Failure Classification

| Signal | Meaning | First safe action |
| --- | --- | --- |
| URL/scope rejection | Input, origin, redirect, address, framing, or retained-body contract failed | Preserve the closed reason; inspect admitted scope and fixture without broadening it |
| Destination unavailable | Resolver or all admitted sockets failed, response truncated, or total timeout elapsed | Retry only under the same read-only crawl identity and bounded workflow policy |
| Low disk preflight | The lab cannot safely build and clean a temporary image | Remove only verified disposable cache/artifacts; do not delete active project resources |
| Synthetic origin not ready | Server failed under its hardened container contract | Inspect this invocation's container state/logs, then run exact labeled cleanup |
| Source changed | Evidence would not describe one stable source set | Let the run clean up, stop concurrent edits, and rerun |
| Cleanup unconfirmed | One or more invocation-owned resources may remain | Inspect by exact `dev.signal.crawler-network-lab=<run-id>` label before any removal |

Never replace the internal public-shaped subnet with a private/loopback exception in
the production address policy merely to make a test easier.

## Runtime Invariants

- Normalize and admit every initial URL and redirect against exact origins.
- Validate every resolver answer; a mixed safe/unsafe set is unsafe.
- Connect to the admitted numeric address and compare the actual peer.
- Use the admitted hostname for TLS SNI and certificate verification.
- Send no cookie or authorization header and never accept provider instructions as
  authority.
- Retain only allowlisted response metadata and bounded HTML/XHTML bytes.
- A `304` without a hash-verified retained prior body is not usable evidence.
- Unsupported compression/media and body limits remain explicit outcomes.

## Not A Production Start Procedure

Do not register `PinnedHttpFetcher` with `CrawlSite` directly. Production use first
requires durable exact scope, a selected controlled resolver/egress service,
robots and origin-wide politeness, frontier/budget state, immutable artifact
storage, observation persistence, monitoring, cancellation, and the remaining
G-02/G-03 evidence. There is no emergency flag that bypasses destination checks.
