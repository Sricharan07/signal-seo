# ADR-0048: Verify One Exact Public Origin Before Binding Authority

- Status: Accepted
- Date: 2026-09-12
- Scope: Public-origin ownership proof, evidence, expiry, and global claims

## Context

Signal can create an owner-controlled site, but that record is intentionally
unverified. Treating submitted configuration, a reachable page, a redirect, or a
related hostname as control proof would let one tenant claim a resource it does
not own. Verification also crosses a hostile network boundary, so it must preserve
SSRF controls, avoid holding database transactions during I/O, and retain failed
observations rather than collapsing them into an unauditable error.

Core V1 needs one small proof method now. DNS and verified upstream bindings can
be added later, but are not needed to establish the first exact-origin contract.

## Decision

Require a current tenant session, the selected site, and a live owner role to issue
a 30-minute HTTP challenge for the site's exact configured canonical HTTPS DNS
origin. The resource is fixed at
`/.well-known/signal-site-verification.txt`; its exact ASCII body is
`signal-site-verification=<challenge UUID>\n`. PostgreSQL stores the SHA-256 digest
and reconstructable UUID, not a separate raw body column. At most ten unexpired
challenges may exist for one site.

Verification has three phases:

1. A short PostgreSQL transaction rechecks the session, selected site, owner
   authority, challenge, expiry, idempotency, and ten-attempt ceiling.
2. The existing numeric-peer-pinned HTTP boundary resolves and admits every public
   address, requests the exact URL with `Accept: text/plain`, follows no redirect,
   and reads at most 1 KiB within fixed request and total timeouts.
3. A new short PostgreSQL transaction rechecks authority and atomically appends the
   sanitized observation. Only exact status `200`, media type `text/plain`, final
   URL, response digest, body, public peer, and timing evidence can verify.

Successful proof creates an immutable verification with method, resource identity,
one permitted origin, verification time, 30-day recheck time, and closed revocation
conditions. It promotes the site to `active`/`verified` and reserves the canonical
origin in a global protected claim registry. A claim held by another tenant/site
returns a generic conflict without disclosing its owner. Reverification by the
same site advances the claim generation.

Failed proof observations are committed before their typed domain error is returned.
Runtime roles have no direct access to challenge, attempt, verification, or claim
tables; the identity role can execute only the three narrow security-definer
functions. Expose issuance and verification through tenant-CSRF-protected strict
API routes. The default API remains unconfigured because it has no production
identity gateway or controlled resolver.

## Consequences

- A proof for `www.example.com` grants no authority over `example.com`, another
  port, another scheme, a redirect target, or any other origin.
- Network I/O cannot extend a database lock or transaction lifetime.
- Exact retries converge; changed request identity conflicts; failures and global
  claim collisions remain immutable evidence.
- A successful proof establishes only control of one public origin. It does not
  authorize crawling, GSC, GitHub, Telegram, CMS access, repository writes, merge,
  deployment, approval, or undo.
- Recheck and revocation conditions are recorded, but this slice does not schedule
  rechecks or implement origin mutation/revocation workflows. Until those exist,
  production enablement must treat overdue or changed claims as unavailable.
- The public proof value may appear at the origin by design, but it is still hidden
  from Python object representations and never logged by this boundary.

## Alternatives Rejected

- **Verify reachability only:** proves that Signal can read a server, not that the
  owner controls it.
- **Follow same-origin or canonical redirects:** silently changes the proved
  resource and creates hostname/port ambiguity.
- **Allow HTML or substring matches:** lets templates, wrappers, and unrelated page
  content satisfy the proof accidentally.
- **Perform HTTP while holding the authority transaction:** couples lock duration
  to attacker-controlled latency and harms availability.
- **Keep claims tenant-local:** permits two tenants to acquire conflicting control
  authority over the same public origin.
- **Store full response bodies:** is unnecessary for exact comparison and expands
  sensitive evidence retention.
- **Enable crawling immediately after proof:** combines ownership with a separate
  robots, budget, workflow, and production-egress authority decision.

## Verification

Real PostgreSQL tests cover issuance, owner and selected-site authority, exact and
conflicting replay, challenge and attempt limits, expiry, rechecks after network
preparation, immutable success/failure evidence, site promotion, 30-day recheck,
function-only privilege, cross-tenant global claim conflict, and failed migration
rollback. Pure and isolated-network tests cover plaintext negotiation, exact body,
no redirects, public-address pinning, response classification, body/time limits,
and sanitized transport failure. API tests cover strict bodies and projections,
tenant browser proof, closed status mapping, and unconfigured composition.
