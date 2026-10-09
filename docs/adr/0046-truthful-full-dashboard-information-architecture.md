# ADR-0046: Expose the Full Dashboard Information Architecture Without Synthetic State

- Status: Accepted
- Date: 2026-09-12
- Scope: Owner-facing dashboard navigation and visual system

## Context

The operational dashboard implemented through Slice 0045 exposed real readiness,
identity, organization, site, and onboarding state on one Overview route. The Core
V1 product contract also names chat, work, strategy, pages, changes, approvals,
analytics, recipes, policy, connectors, usage, settings, and help. Design references
show how those destinations should compose, but their example metrics, provider
health, tasks, approvals, pull requests, and agent activity are not repository
evidence and cannot be presented as live product state.

Leaving those destinations disabled obscures the intended product architecture.
Populating them with fixture data would be more visually complete but would violate
the requirement that Signal distinguish implemented, unavailable, and unknown state.

## Decision

Expose all fourteen dashboard destinations through an explicit server-side route
allowlist and one shared page loader. Preserve Overview's existing real reads and
mutations. Every destination receives the same strictly validated readiness,
session, organization, and site projections; no browser-side API origin or authority
is introduced.

Render each unimplemented product destination as a domain-specific readiness ledger.
The ledger names the required source, its current state, and the absence of a live
tenant-scoped read model. Controls remain visible but disabled, external writes are
stated as blocked, and each surface explicitly says that no customer data is being
simulated. Settings may display already validated site metadata in read-only fields.

Adopt the reference-led visual frame as a light navigation rail, flexible working
canvas, and contextual detail rail. Use Lucide icons, compact controls, flat sections,
pale mint selection, restrained semantic blue/amber/red, and a maximum eight-pixel
radius. At narrower widths the detail rail stacks below the canvas and the navigation
becomes a disclosure panel.

## Consequences

- Users can discover the intended product architecture and current prerequisites
  without mistaking mock examples for operational data.
- Future slices can replace one readiness ledger at a time with a qualified read
  model while preserving routes, page identity, and responsive behavior.
- All existing identity and site mutations retain their original same-origin,
  server-authorized boundaries; this slice grants no new authority.
- The non-Overview pages are useful architecture and status surfaces, not completed
  chat, orchestration, analytics, connector, approval, change, policy, or undo flows.
- A shared renderer intentionally gives unavailable destinations a consistent
  structure. Domain-specific operational layouts should be introduced only with
  the corresponding tested data contracts.

## Alternatives Rejected

- **Copy the reference data into fixtures:** would fabricate customer metrics,
  provider bindings, approvals, pull requests, and active specialists.
- **Keep navigation disabled:** hides the product topology and gives users no
  explanation of the prerequisites for each destination.
- **Build all missing backend contracts in the redesign:** combines many authority,
  provider, persistence, and recovery boundaries into an unreviewable UI slice.
- **Fetch page state in browser JavaScript:** duplicates the established server BFF
  boundary and risks leaking topology or future credentials into client state.

## Verification

Component tests render every destination and reject known synthetic claims.
Repository tests enforce the route allowlist, shared loader, server-only reads, and
truthful unavailable-state marker. TypeScript, optimized build, documentation, and
aggregate repository gates remain required. Browser checks cover all fourteen pages
at 1440 and 390 pixels, the 980-pixel stacked-detail layout, mobile navigation, mobile
settings tabs, horizontal overflow, and console diagnostics using a temporary
credential-free loopback fixture that is removed after inspection.
