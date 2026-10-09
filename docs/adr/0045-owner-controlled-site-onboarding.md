# ADR-0045: Onboard Sites Through Owner Authority

- Status: Accepted
- Date: 2026-09-12
- Scope: Initial site creation, membership, session context, and audit evidence

## Context

Signal can authenticate an existing user, establish one tenant session, list that
user's current sites, and persist an active-site selection. An owner still could
not add the public site that later origin, GSC, and repository bindings need. A
browser-supplied tenant or user identifier would make onboarding a confused-deputy
boundary, while direct writes to the forced-RLS site tables would either broaden
the identity role or require a sequence of independently fallible mutations.

Site creation is durable account configuration, but it is not an external or
irreversible SEO operation. It therefore needs live owner authority, idempotency,
concurrency control, bounded resource use, and immutable evidence, but not the
later exact-change approval or provider undo workflow.

## Decision

Accept one canonical HTTPS DNS origin, bounded display name, valid IANA timezone,
three-letter reporting currency, expected tenant-session version, and UUIDv4
idempotency key. Resolve tenant and actor only from the opaque tenant session.
Require a current owner membership and active tenant, and cap active/onboarding
sites at 100 per tenant.

Perform creation through one `control.onboard_site` security-definer function.
The function locks the exact current session and owner/tenant authority, handles
idempotent replay before optimistic-version comparison, rejects an existing
non-archived canonical origin, and atomically:

1. creates an `onboarding` and `unverified` `app.sites` row;
2. creates the owner's active site membership with the currently implemented
   harmless snapshot-request permission;
3. selects the site and increments the tenant-session version;
4. extends the session's immutable active-site hash chain; and
5. appends one immutable, request-hashed, explicitly tenant-scoped
   `site.onboarded` event that references the created site.

Because `app.sites` forces exact-site RLS, maintain a private
`control.tenant_site_routes` mirror with tenant, site, origin, and lifecycle only.
A security-definer trigger backfills and maintains the mirror. Runtime roles have
no direct table access; only the onboarding function can use it for the tenant-wide
origin collision and site-count checks. Existing origin values are preserved
during backfill rather than retroactively rewritten or rejected.

Expose the operation as strict tenant-CSRF-protected `POST /v1/sites`. The
same-origin dashboard BFF forwards exactly one tenant cookie and a bounded form,
validates the exact response, and permits no cookie mutation. Only owners see the
Add site control. A successful result explicitly remains unverified and is selected
for the current session.

## Consequences

- Browser input cannot choose tenant, user, role, membership, lifecycle, or site
  permission state.
- Exact retries return the original site without adding rows or advancing the
  session again; different input under the same key conflicts.
- A stale tab cannot add and select a site after another context transition.
- Creation, owner grant, context transition, and both evidence records either all
  commit or all roll back.
- Tenant-wide count and origin checks do not require broad read access to the
  customer-owned site table.
- The onboarding action is tenant-scoped because its idempotency and limit
  authority govern site creation for the organization; `scope_kind = tenant`
  distinguishes that ownership from its exact created-site reference.
- The origin is syntax-normalized only. It is not owned, reachable, safe to crawl,
  connected to GSC, or bound to a repository until later proof steps succeed.
- Site deletion, archive UI, invitation changes, connector writes, approval, and
  product undo remain absent.

## Alternatives Rejected

- **Let the browser send tenant or user scope:** treats untrusted routing data as
  authority and risks cross-tenant creation.
- **Grant the identity role direct table writes:** expands a narrow identity
  boundary into general site administration and weakens audit composition.
- **Create the site, membership, and selection in separate requests:** leaves
  partial authority and makes retry/recovery ambiguous.
- **Disable forced RLS to count tenant sites at runtime:** broadens visibility and
  undermines exact-site isolation.
- **Add a unique index directly to existing site origins:** can make a forward
  migration fail on historical duplicate or noncanonical data without a reviewed
  remediation policy.
- **Automatically mark the origin verified:** confuses submitted configuration
  with independent control proof.
- **Require consequential-change approval:** adds ceremony without protecting an
  external effect; approval belongs to later immutable provider/repository writes.

## Verification

Real PostgreSQL tests cover atomic creation, owner/non-owner authority, parent
session and recovery invalidation, exact and conflicting replay, stale and
concurrent versions, duplicate origin, tenant limits, generated-ID collision
rollback/retry, active-site hash-chain continuity, event validation/immutability,
function-only privilege, backfill, migration failure rollback, and malformed
input. API and dashboard tests cover strict bodies and responses, CSRF and
same-origin proof, exact-cookie forwarding, closed error mapping, owner-only
rendering, and bounded responsive form behavior. Origin ownership and all external
provider effects remain disabled.
