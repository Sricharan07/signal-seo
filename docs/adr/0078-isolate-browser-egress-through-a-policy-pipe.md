# ADR-0078: Isolate Browser Egress Through a Policy Pipe

Status: Accepted for the internal browser boundary; no production release.

## Context

Revision 4.0 section 9 and INV-029 require disposable browser isolation and the
crawler's public screening, DNS pinning, robots evidence and global admission.
0065 supplies a library, not a network choke point. An ordinary CONNECT proxy
would create an opaque TLS tunnel and could not enforce read verbs or robots.

## Decision

Compose each browser session on its own Docker internal, isolated-gateway bridge.
Only its disposable browser and credential-free forward-proxy transport attach.
There is no bridge gateway address, default route, published port or host mount.
Both containers use non-root identities, read-only roots, tmpfs, dropped
capabilities, no-new-privileges, default seccomp and bounded processes/memory/CPU.
The Chromium profile and temporary material live only in tmpfs. Runtime image
selection requires an immutable SHA-256 image identity.

The proxy refuses CONNECT, credentials, request bodies and every method except
GET/HEAD. A private Docker attach pipe carries typed requests to the trusted
control-plane shared-egress capability and bounded responses back. Neither
container possesses provider credentials, database access or an upstream route.
Playwright intercepts HTTP(S) resources and fulfills them from this proxy; it
never calls route continuation or route fetch. HTTPS is terminated by the existing
numeric-peer-pinned gateway with certificate validation, not by a browser tunnel.
The pipe is transport only: it cannot admit an origin or manufacture a receipt.
Merge-train composition uses a separate `browser_worker_read` profile for the
reviewed GET/HEAD assets; the existing HTML-only GET `browser_read` profile is
unchanged. Both retain credential and request-body refusal.

## Alternatives

- A proxy environment variable alone leaves direct sockets and bypasses possible.
- Ordinary CONNECT cannot inspect HTTPS requests and would bypass robots/verbs.
- Installing a TLS interception CA broadens secrets and certificate ownership.
- A new public-network service duplicating crawl policy risks policy divergence.

## Consequences

The composition requires Docker isolated gateway mode; unsupported engines fail
closed. Chromium uses Playwright's container-oriented launch; the OCI sandbox is
the qualified boundary, not a claim of separately qualified Chromium user-namespace
sandboxing. Only Chromium headless shell is installed. Fonts, CSS, scripts, images
and JSON use a closed credential-free read profile. WebSockets, workers with
unintercepted network paths, downloads and unsolicited document navigation cannot
use a tunnel or direct route. Unsupported resources remain incomplete rather than
being fetched through another path. This is not exact Googlebot emulation.

The trusted application still owns current scope, robots preparation, immutable
records and encrypted artifact keys. A live deployment must provide these ports
and promote an exact reviewed image. No production process is enabled by this ADR.

## Verification

`scripts/run-browser-worker-tests.py` builds an invocation-private image/cache,
uses real Docker and PostgreSQL, proves direct-network failure from inside the
browser, and verifies proxy denial and cleanup. See [0067](../implementation/0067-sandboxed-browser-worker.md).
Playwright's [network interception contract](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route)
and [container guidance](https://playwright.dev/python/docs/docker) inform the
composition; documentation is not release evidence.
