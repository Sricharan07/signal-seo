# ADR-0044: Persist Active Site In The Server Session

- Status: Accepted
- Date: 2026-09-09
- Scope: Tenant-session site context and dashboard selection

## Context

Signal can authenticate an existing user, establish one tenant session, and list
the sites that user may currently access. The browser still had no durable current
site. A posted site UUID therefore could not distinguish navigation intent from
authority, and a stale tab could silently replace a newer selection. Site-scoped
commands accepted any currently authorized requested site rather than requiring
the server context displayed to the owner.

The active site is reversible session navigation, not an external SEO change. It
does not require consequential-action approval, but it must remain tenant-bound,
current-authority-checked, concurrency-safe, and auditable.

## Decision

Store nullable `active_site_id` and monotonic `session_version` on the existing
hash-only tenant session. New sessions begin with no active site. A narrow
`control.select_session_site` security-definer function resolves the opaque token
hash, locks the exact tenant session, requires the caller's expected version,
rechecks the parent identity, recovery generation, user, tenant membership, site
membership, permission set, tenant lifecycle, and site lifecycle, then changes
the selected site and increments the version in one transaction.

Each real transition appends one immutable `session_site_context_events` row. The
row records bounded authority facts and a SHA-256 hash chained to the preceding
event for that tenant session. The event identifier is retry-allocated; a
collision rolls the whole transition back before retry. Reselecting the current
site is idempotent, does not increment the version, and appends no duplicate event.

Current-session inspection returns schema version 2 with the session version and
the active site only when its site grant remains current. The stored UUID is not
itself authorization. Existing snapshot authorization now additionally requires
the requested site to equal the server-selected active site before it rechecks
ordinary live authority.

The dashboard performs selection through a same-origin POST route. That BFF
accepts only one site UUID and expected version, obtains a tenant-cookie-bound CSRF
proof, calls `PUT /v1/session/site`, validates the exact response, and permits no
cookie mutation. A conflict asks the owner to refresh rather than silently
overwriting newer context. The independently validated site directory must contain
the projected active site or the page fails closed.

## Consequences

- Tenant and user identity never come from the selection form.
- Two stale tabs cannot both replace the same context version; one transition
  commits and the other receives a closed conflict.
- Site revocation immediately removes the active-site projection and blocks
  commands even though the historical session and event evidence remain.
- Audit rows are immutable and inaccessible to the runtime identity role except
  through the narrow selection operation.
- Site changes across one session form one evidence chain while forced site RLS
  remains enabled; the function temporarily reads the prior event under its prior
  site scope before inserting under the new scope.
- Selecting a different authorized site reverses the navigation choice. This is
  not the product undo control for consequential GitHub or provider changes.
- Current-session consumers must adopt response schema version 2.

## Alternatives Rejected

- **Keep active site only in React state or a browser cookie:** makes the browser
  an authority source and does not constrain server-side commands.
- **Trust a site UUID on each command:** permits stale or confused-deputy requests
  that do not match the context shown to the owner.
- **Automatically select the first site:** hides owner intent and changes behavior
  when directory order or membership changes.
- **Use last-write-wins selection:** lets stale tabs silently replace newer
  context and obscures ordering during incident review.
- **Append an event for idempotent reselection:** creates noise without recording
  a state transition.
- **Treat selection as an approved external operation:** conflates reversible
  session navigation with the later approval and undo boundary for provider writes.

## Verification

Real PostgreSQL tests cover exact selection, idempotency, stale and concurrent
conflicts, wrong-tenant and ungranted sites, parent/session/site revocation,
selected-site command enforcement, event-ID collision rollback/retry, hash-chain
integrity, immutability, least privilege, malformed inputs, migration rollback,
and schema upgrades. API and dashboard tests cover exact contracts, CSRF,
same-origin forms, strict response fields, version transitions, conflicts,
directory reconciliation, safe notices, and dependency failures. Production
writes, site onboarding, origin verification, and provider connectors remain off.
