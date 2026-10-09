# Slice 0047: Full-Viewport Dashboard Polish

- Status: Implemented and locally qualified; product data workflows remain unavailable
- Date: 2026-09-12
- Milestone: M2/M4 partial
- Specification: Revision 3.2 sections 8.1-8.5, 24.1-24.5, 32, and 33
- Decision: [ADR-0047](../adr/0047-full-viewport-evidence-first-dashboard.md)

## Scope

This slice audits and corrects the Slice 0046 dashboard visual system using the
latest supplied dashboard reference. It changes presentation and responsive
composition only. It does not add a tenant data read model, connector, mutation,
database schema, provider call, agent operation, approval, recovery, or external
write.

## Visual Audit

| Finding | Correction |
| --- | --- |
| Desktop shell appeared inside a second rounded browser frame | Removed outer margin, maximum width, border, radius, and reduced-height behavior; the application now fills the viewport |
| Pale mint, blue, and amber washes dominated state | Moved to cool neutrals, solid blue actions, and semantic icon/border color without pastel fields |
| Repeated circular icon holders weakened domain meaning | Standardized compact squared holders and replaced ambiguous navigation glyphs with domain-specific Lucide icons |
| Overview, Analytics, and Usage had no graph structure | Added evidence-empty chart modules with grid, legend, source state, and prerequisite action |
| Header hierarchy did not match a mature dashboard | Added a full-width utility toolbar with disabled future search, real environment state, and account control |
| Sidebar footer looked like passive metadata | Recast it as a solid safety banner that states the real development write boundary and links to Policy |
| Local design tokens had drifted from the documented system | Consolidated all CSS colors into declared tokens and removed undocumented radii and side-tab accents |

The final Impeccable static audit reports no findings for the dashboard stylesheet
or component.

## Truth Boundary

```text
Validated server readiness and session/site projections
  -> full-viewport dashboard composition
  -> real status values and unavailable controls
  -> chart structure with no plotted observations
  -> connector or settings prerequisite link
  -> no synthetic metric, trend, date, cost, provider, or customer record
```

The reference determines visual hierarchy only. Its commerce records, totals,
charts, dates, customer counts, premium banner, and AI illustration are deliberately
absent.

## Responsive Contract

- Desktop fills the entire viewport and uses a 222-pixel navigation rail, flexible
  work surface, 338-pixel detail rail, and 64-pixel utility toolbar.
- At 1080 pixels the detail rail stacks below the working canvas.
- At 720 pixels desktop navigation and utility toolbar are replaced by a compact
  sticky mobile bar and disclosure navigation.
- At 520 pixels summary modules form one column, chart copy remains legible, and
  actionable controls use at least 44-pixel touch targets.
- All tested sizes preserve zero horizontal document overflow.

## Verification

Run:

```sh
npm test
npm audit --omit=dev
.venv/bin/python -m pip check
node <impeccable-skill>/scripts/detect.mjs \
  --json apps/dashboard/app/globals.css \
  apps/dashboard/components/dashboard-view.tsx
git diff --check
```

Exact test counts, source hashes, browser measurements, static-audit result, and
secret-scan result are recorded in:

- [0047 dashboard visual qualification](../evidence/0047-dashboard-visual-polish.json)

No database or provider behavior changed, so this slice does not claim a new
PostgreSQL, GSC, GitHub, Telegram, WordPress, or deployment qualification.

## Explicit Limits

- Empty chart modules are truthful placeholders, not analytics or cost features.
- Global search is disabled and has no query backend.
- The safety banner describes the current release boundary; it does not grant
  approval or execution authority.
- Non-Overview destinations remain readiness surfaces until their tested
  tenant/site-scoped read models exist.
- Production writes, chat, agents, approval, recovery, undo, and connectors remain
  unavailable.
