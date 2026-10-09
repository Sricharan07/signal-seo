# ADR-0049: Complete Dashboard Verification Behind a Same-Origin BFF

- Status: Accepted
- Date: 2026-09-12
- Scope: Dashboard ownership-proof interaction, browser authority, and operational UI system

## Context

Slice 0048 created a strict exact-origin challenge and verification API, but the
dashboard could not use it. Sending the tenant session or transient CSRF token to
client JavaScript would widen the credential boundary. Treating an
`unverified`-only site projection as permanent also caused the dashboard to reject
the new `verified` and `reverification_required` states.

The completed frontend redesign additionally introduced an in-repository icon
family while the established product direction and control vocabulary use Lucide.
During responsive browser review, the mobile disclosure panel was found to be
constrained to the height of its filtered sticky ancestor.

The visual takeover also exposed a truth problem: empty chart grids and legends,
repeated category glyphs, and a permanently disabled search field looked like
partially working product controls. The interface needs the information
architecture of a mature dashboard without implying data or capability that does
not exist.

## Decision

Expose two same-origin Next.js route handlers for challenge issuance and exact
proof verification. Each handler requires exact `Origin` and `Sec-Fetch-Site`
evidence, one exact host-only tenant cookie, a strict bounded JSON body, and the
configured dashboard origin. The server obtains a transient tenant CSRF token and
forwards only the minimum request to the Signal API with no-store caching, manual
redirect handling, fixed timeouts, bounded exact response schemas, and no accepted
cookie mutation.

Render the verification control only for a server-projected owner with a selected
site whose closed ownership state is `unverified` or
`reverification_required`. Keep the public challenge URL and value only in the
component's memory, provide explicit copy/open controls, and refresh server truth
after exact success. A failed or ambiguous response grants no visible or durable
authority.

Accept all three ownership states in the site directory and require canonical
HTTPS origins. Use the pinned `lucide-react` package directly for standard
navigation, status, and command symbols; remove the parallel custom glyph family.
Position mobile navigation relative to the sticky toolbar with explicit remaining
viewport height so backdrop filtering cannot shrink the overlay.

Consolidate visual hierarchy into one cool ground, white elevated operational
modules, compact fact bands, and solid-blue commands. Keep chart modules as stable
destinations but render one explicit empty evidence field rather than axes,
legends, or trends before observations exist. Remove permanently inert global
search and decorative row glyphs. Render every unavailable destination as a short
unboxed prerequisite ledger rather than a grid of pseudo-data.

## Consequences

- Browser JavaScript can operate the proof workflow without receiving the tenant
  session, CSRF token, API origin, provider details, or credential mutation.
- Challenge values are intentionally public and transient; they are not persisted
  in browser storage or rendered until the owner requests one.
- The dashboard now reflects verified and recheck-required server truth instead of
  invalidating the entire directory.
- Standard Lucide symbols remain familiar and independently maintained, while
  semantic labels and accessible names carry meaning.
- The mobile navigation exposes all fourteen destinations at 390 pixels rather
  than collapsing inside the filtered header.
- Empty analytics surfaces remain visually intentional without impersonating a
  populated graph, and inactive functionality no longer masquerades as a control.
- This decision adds no resolver, production identity composition, automatic
  recheck, connector, crawl, agent, approval, provider write, or undo authority.

## Alternatives Rejected

- **Call the Signal API from the browser:** exposes a broader credential and CSRF
  boundary and would require cross-origin policy.
- **Persist challenge state in local storage:** creates stale, replay-prone browser
  state when the authoritative challenge is already durable in PostgreSQL.
- **Accept arbitrary success payloads:** allows API drift or injected fields to
  create misleading owner-visible authority.
- **Keep a custom icon fork:** duplicates a mature standard library and makes
  familiar commands less recognizable.
- **Keep the mobile panel fixed inside a filtered ancestor:** Chromium establishes
  that ancestor as the containing block, constraining the panel to the toolbar.

## Verification

Dashboard tests cover exact challenge and verification success, strict CSRF and
cookie forwarding, malformed authority and payloads, cookie mutation, external
failure classes, route-level same-origin proof, ownership projections, and owner-
only rendering. Repository tests enforce the BFF and pinned Lucide boundary.
Production builds include both routes. Browser checks cover every destination at
1440 pixels, the initial and rejected proof control at desktop and mobile widths,
the 390-pixel navigation overlay, horizontal overflow, gradients, and diagnostics.
