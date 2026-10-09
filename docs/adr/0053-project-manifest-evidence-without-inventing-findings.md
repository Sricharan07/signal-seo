# ADR-0053: Project Manifest Evidence Without Inventing Findings

- Status: Accepted
- Date: 2026-09-12
- Scope: Dashboard evidence projection for the current selected site

## Context

Slice 0052 made one durable local audit runnable through the product and stored a
bounded terminal manifest. The Work receipt intentionally showed only summary
counts. Users still needed a place to inspect what evidence identity was committed,
while the system had no authorized customer page observations or SEO findings.

The reference Pages experience includes page inventory and issue details, but
rendering those structures from a synthetic one-URL manifest would turn workflow
evidence into unsupported SEO claims. A new database or API endpoint is also
unnecessary because the current-user latest-work response already performs the
required live session, tenant, site, membership, and actor checks.

## Decision

Project the existing latest-work terminal manifest into Pages and Overview. The
dashboard preserves and validates the complete manifest identity, SHA-256 digest,
coverage, discovered and terminal counts, scope version, crawl-policy version,
collection time, and source. Version values must be positive bounded integers,
matching the API contract.

For the explicit local pilot, label the evidence as synthetic and no-network,
state that the configured customer origin was not read, and show an explicit
empty finding state. Do not synthesize URLs, issues, metrics, recommendations, or
page history from the manifest. When no terminal manifest exists, direct the user
to Work rather than rendering reference data.

Reuse the current-user latest-work authority boundary. Add no dashboard database
access, new provider authority, workflow transition, or external operation.

## Consequences

- Users can move from a completed Work receipt to an inspectable evidence record.
- Overview reflects actual durable work instead of a stale no-read-model message.
- The full identity and policy context needed for later evidence correlation is
  visible and testable.
- The Pages surface remains sparse until a separately reviewed observation and
  finding contract exists.
- This slice provides no evidence about customer SEO quality and grants no
  production or provider authority.

## Alternatives Rejected

- **Render reference page rows:** visually richer, but fabricated and not tied to
  authorized evidence.
- **Treat one discovered URL as a customer page:** false because the local executor
  never contacts the configured origin.
- **Add a duplicate evidence endpoint:** expands authority and maintenance without
  a new domain contract.
- **Hide provenance behind a short receipt:** prevents users and operators from
  auditing exactly which immutable result and policy versions are displayed.

## Verification

Component and parser tests cover valid evidence, absent evidence, complete
provenance, Overview projection, and malformed versions. Type checking and the
production build validate the presentation contract. A real disposable browser
journey verifies Pages and Overview against the PostgreSQL/Temporal result created
through Slice 0052, with no customer-origin request or external write.
