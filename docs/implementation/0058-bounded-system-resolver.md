# Slice 0058: Bounded System Resolver

## Objective

Compose the already-qualified exact-origin ownership flow into the disposable
pilot with a real DNS resolver, while retaining the crawler's public-address,
exact-origin, response, redirect, and timeout gates.

## Implemented

- Added `BoundedSystemResolver`, a callable A/AAAA resolver backed by the host
  resolver. Each lookup has a caller-visible deadline, duplicate answers are
  removed without changing resolver order, and concurrent unresolved lookups are
  capped.
- Kept destination admission in `PinnedHttpFetcher`: every returned address must
  be public before any connection is attempted, the selected address is pinned to
  the connection, and TLS still validates the requested hostname.
- Composed that boundary into `npm run pilot`. The existing owner-only,
  immutable HTTP well-known challenge can now verify a public HTTPS origin that
  the developer actually controls.
- Added positive, invalid-input, resolver failure, timeout, capacity, and
  private-address rejection coverage.

## Safety Properties

- A DNS timeout or exhausted resolver capacity fails closed. A timed-out host
  lookup runs only in a daemon worker and retains no request or credential data.
- Private, loopback, link-local, reserved, multicast, unspecified, and otherwise
  disallowed answers are still rejected by the existing crawl URL boundary.
- This enables only the GET-based ownership proof. It does not authorize a site
  crawl, model access to a customer page, GitHub, Telegram, CMS writes, merge, or
  deployment.
- The pilot remains disposable and must use a developer-controlled public origin;
  it must not be pointed at a customer site without authorization.

## Verification

Run from the repository root:

```sh
.venv/bin/pytest -q tests/tooling/test_crawl_http.py tests/tooling/test_local_pilot.py
.venv/bin/ruff check services/control_plane/src/signal_core/crawl_http.py scripts/local_pilot.py tests/tooling/test_crawl_http.py tests/tooling/test_local_pilot.py
.venv/bin/ruff format --check services/control_plane/src/signal_core/crawl_http.py scripts/local_pilot.py tests/tooling/test_crawl_http.py tests/tooling/test_local_pilot.py
npm test
```

## Limits And Next Work

The default production API still has no controlled resolver/egress composition.
The pilot can prove ownership but its audit and finding paths remain synthetic.
Next, use the current verified-origin claim to authorize one immutable,
GET-only homepage metadata observation and feed that evidence into the existing
deterministic finding and supervised proposal path.
