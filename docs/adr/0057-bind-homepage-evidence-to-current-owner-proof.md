# ADR-0057: Bind homepage evidence to current owner proof

## Status

Accepted on 2026-09-20.

## Context

Signal's visible audit, finding, model, and approval flow operated only on a fixed
internal fixture. That proved control-plane behavior but could not improve a real
site. Reading an arbitrary URL directly from the dashboard would bypass current
ownership, selected-site authority, destination screening, and durable intent.
Running a full crawler first would delay the smallest useful customer-visible
outcome and combine several still-incomplete production gates.

## Decision

Introduce one narrow verified-homepage observation:

1. Require a current selected-site session, completed audit manifest, unexpired
   exact-origin verification, and matching global origin claim.
2. Commit an immutable idempotent intent containing those identities before any
   network request.
3. Fetch only the verified origin's homepage through the existing pinned public
   HTTP boundary. Permit at most three same-origin redirects and retain fixed
   timeout and body limits.
4. Parse only bounded title, first H1, and meta-description fields. Persist their
   bounded values and the body SHA-256, not the customer HTML body.
5. Record known failures durably without creating evidence. On success, append
   immutable verified-owner evidence and create a deterministic missing-meta
   finding only when the observed field is absent.
6. Expose the action and result through authenticated API and same-origin
   dashboard boundaries. Keep all external write authority disabled.

## Alternatives

- **Continue using fixture evidence.** Rejected because it cannot establish a
  customer outcome or support a real proposal.
- **Fetch any user-supplied URL.** Rejected because it would create an SSRF and
  cross-resource authorization boundary.
- **Store the full HTML in PostgreSQL.** Rejected for this slice because the
  metadata detector needs only bounded extracted facts and a content identity.
- **Wait for the full production crawler.** Rejected because one exact homepage
  read can safely advance the user journey without claiming crawl coverage.

## Consequences

An owner can now produce the first real customer-origin SEO evidence and inspect
its provenance in the product. The read remains intentionally narrow and must not
be described as a crawl or broad SEO audit. Changes to the page under the same
completed audit require a new durable intent and cannot silently replace evidence.

This decision does not qualify production egress, a distributed artifact store,
GSC, competitor research, performance evidence, repository candidates, GitHub
pull requests, deployment verification, or recovery.

## Verification

- Real PostgreSQL tests cover prerequisites, exact replay, successful evidence,
  no-finding success, durable failure, authority reduction, function-only access,
  forced RLS, immutability, and migration behavior.
- Unit tests cover parsing bounds, exact-root fetches, redirects and response
  failures, invalid HTML, and private-address rejection through the existing
  pinned boundary.
- API and dashboard tests cover strict schemas, same-origin mutation proof,
  incoherent provenance rejection, visible customer evidence, and closed failure
  states.
