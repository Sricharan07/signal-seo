# ADR-0047: Use a Full-Viewport Evidence-First Dashboard Frame

- Status: Accepted
- Date: 2026-09-12
- Scope: Dashboard layout, color, iconography, and empty data visualization

## Context

Slice 0046 exposed all fourteen product destinations truthfully, but its wide-screen
application shell retained an outer margin, border, and radius. In a browser this
read as a desktop mockup placed inside another surface instead of a full product.
The interface also relied on pale semantic fills and repeated circular icon holders,
while Overview, Analytics, and Usage had no chart structure until their future read
models exist.

The supplied commerce-dashboard reference establishes a stronger composition:
full-canvas application chrome, a white navigation rail and utility bar, a cool-gray
work surface, white operational modules, compact solid actions, and legible chart
areas. Its brand, example metrics, premium offer, customer records, AI decoration,
and provider content are visual reference material only and are not Signal runtime
inputs.

## Decision

Render Signal edge to edge at desktop and mobile sizes. The shell has no outer
margin, border, radius, shadow, or simulated-device backdrop. Use a white navigation
rail and top utility bar, a cool-gray work surface, a white detail rail, and solid
Signal Blue for active navigation and primary actions. Reserve green, amber, and red
for explicit semantic state on neutral surfaces; do not use pastel semantic washes
or gradients.

Use domain-specific Lucide icons throughout. Replace generic agent, wallet, radar,
and settings-like navigation symbols with workforce, cost, target, shield, cable,
and chart symbols that match their destinations. Keep the Signal bar mark as the
small code-native brand asset.

Add an evidence-empty chart contract to Overview, Analytics, and Usage. Each module
contains a title, expected-series legend, neutral grid, source state, explicit empty
copy, and prerequisite link. Until a validated tenant/site-scoped read model exists,
it renders no line, bar, axis value, tooltip, trend, date, or customer metric.

Use the bottom navigation banner for the real development write boundary and a
Policy link, not an upgrade promotion. Global search remains visibly disabled until
its server contract exists.

## Consequences

- The product fills the browser and visually follows a mature dashboard hierarchy
  without copying unrelated sample content or fabricating SEO outcomes.
- Charts now have stable, responsive modules that future evidence projections can
  populate without changing page hierarchy.
- Semantic state is carried by words, Lucide icons, and restrained borders rather
  than pale color fields.
- The utility search affordance is intentionally present but unavailable; future
  activation requires a tenant-scoped search contract and tests.
- This decision changes no database, provider, session, or external-write contract.

## Alternatives Rejected

- **Retain the framed desktop mockup:** wastes viewport area and makes the running
  product look like an embedded prototype.
- **Populate charts with reference values:** would misrepresent customer traffic,
  spend, dates, trends, and connector state.
- **Use decorative AI imagery or custom glyphs:** weakens domain meaning and creates
  an inconsistent icon language when Lucide already covers the required controls.
- **Use gradients or pale status panels:** conflicts with the requested visual
  direction and makes state feel decorative rather than operational.
- **Hide charts until data exists:** removes important information architecture and
  gives users no visible path to the required connector.

## Verification

Component tests require all three chart modules, their no-synthetic-data marker,
expected series labels, and explicit empty copy while rejecting known reference
metrics. Repository tests require the full-viewport shell, evidence-empty design
contract, absence of pale token names, and absence of CSS gradients. Browser checks
cover desktop, stacked-detail, and mobile layouts, overflow, chart dimensions,
touch targets, and console diagnostics.
