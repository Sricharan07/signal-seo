# Slice 0044: Server-Owned Active-Site Context

- Status: Implemented and locally qualified; site onboarding and production identity unavailable
- Date: 2026-09-09
- Milestone: M1/M2 partial
- Specification: Revision 3.2 sections 2 INV-002/INV-003, 8.1, 8.4, 8.5,
  9, 24.1, 24.2, and 24.5
- Decision: [ADR-0044](../adr/0044-server-owned-active-site-context.md)
- Runbook: [Active-site context](../runbooks/active-site-context.md)

## Scope

This slice turns the authenticated site directory into an explicit server-owned
current-site context. An authenticated owner can select any currently authorized
site from the Overview. PostgreSQL applies optimistic concurrency, records the
transition, and makes that selection a prerequisite for the existing harmless
snapshot command authority.

This slice does not create sites, verify origin ownership, enable the snapshot
button, connect GSC/GitHub/Telegram, run an SEO agent, approve a change, write to a
provider, or implement product undo. Production customer identity remains
disabled because the default identity gateway is still unconfigured.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Migration `0026` | Adds a nullable tenant/site foreign key to `app.sessions` and one immutable forced-RLS site-context event table |
| Atomic selection | Hash-scoped function locks one current session, validates expected version and live authority, updates context, and appends evidence in one transaction |
| Conflict control | A real transition increments `session_version`; stale or concurrent replacements return conflict without changing state |
| Evidence | Each transition records session/user/site/version and bounded authority epochs in a per-session SHA-256 chain; exact reselection is event-free |
| Current-session API | Schema version 2 adds strict `session_version` and nullable current-authority `active_site_id` |
| Selection API | Tenant-cookie CSRF plus strict `PUT /v1/session/site`; closed unauthorized, denied, conflict, unavailable, and failure outcomes |
| Dashboard BFF | Same-origin bounded form forwards only the exact tenant cookie, site UUID, version, and transient CSRF proof; success cannot set cookies |
| Dashboard view | Authorized rows expose stable Select/Current controls and the Overview shows the current site or a selection-required state |
| Command authority | Existing snapshot acceptance and status require the requested site to equal the selected active site before live authority checks |

## Trust And Data Flow

```text
Server-rendered current session (tenant, version, current active site)
  + independently validated authorized-site directory
  -> same-origin dashboard form (site ID, expected session version)
  -> dashboard BFF (one exact tenant cookie)
  -> tenant-bound CSRF read and strict selection API
  -> one PostgreSQL transaction under hash-derived tenant authority
  -> session context update + immutable hash-chained event
  -> redirect to a fresh server-rendered projection
```

The browser never supplies tenant or user identity. It proposes a site and the
version it rendered. PostgreSQL is the durable source of current context and
rechecks every authority row under forced RLS. The dashboard holds no persistent
copy; hidden form fields carry only bounded request data, not proof of authority.

Revoking a site grant does not rewrite historical evidence. Current inspection
projects `active_site_id: null`, directory reconciliation rejects impossible
combinations, and site-scoped commands fail closed. A subsequent valid selection
can replace the dormant stored UUID using the current session version.

## Verification

Run:

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
npm run test:repo
npm run check:docs
```

The evidence record captures the final cumulative counts, source hashes, browser
viewports, and cleanup state:

- [0044 active-site qualification](../evidence/0044-active-site-context.json)

Local qualification passed 508 cumulative real-PostgreSQL cases, 207 API cases,
660 API/identity/tooling cases, 55 dashboard cases, 20 repository cases, dashboard
typecheck and optimized build, Python lint/format for 155 files, dependency checks,
and desktop/mobile browser inspection without horizontal overflow. The
documentation graph validates 122 Markdown files after the evidence record is
present. All fixtures were synthetic and removed; no customer data, credentials,
or production authority were used.

## Explicit Limits

- A selected site is session navigation and command scoping only. It grants no
  new site membership, connector access, approval, or external write.
- The active-site event chain is auditable database evidence, not a customer audit
  export, telemetry backend, retention policy, or cryptographic signature service.
- The current-session HTTP contract intentionally advances from schema version 1
  to version 2; unupgraded clients fail closed.
- Site membership removal is modeled as a lifecycle transition because event
  evidence retains foreign-key relationships. Destructive audit deletion is not
  an operational recovery path.
- The default identity gateway remains unconfigured, and the complete deployed
  Keycloak/OpenBao/PostgreSQL/API/dashboard journey is not qualified.
- No irreversible change occurs here, so approval and undo controls are not
  invoked. Those gates remain mandatory for later GitHub/provider operations.

## Next Safe Dependency

Add owner-controlled site onboarding and exact public-origin ownership
verification without weakening selected-site authority. Keep GSC, GitHub App, and
Telegram bindings read-only until each real provider boundary is separately
qualified.
