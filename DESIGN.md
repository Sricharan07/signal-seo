---
name: Signal
description: An SEO and AI-search employee that reports to its owner. Direction C.
colors:
  ground: "#f2f3ef"
  canvas: "#ffffff"
  field: "#f7f8f4"
  field-strong: "#e9ebe5"
  ink: "#121412"
  ink-soft: "#3e433e"
  muted: "#5c615c"
  line: "#e3e5df"
  line-strong: "#cdd1c8"
  signal-blue: "#2d5bff"
  signal-blue-deep: "#1f47e0"
  signal-blue-text: "#2448d8"
  verified-green: "#17744b"
  caution-amber: "#8a5300"
  critical-red: "#b4321b"
  night: "#1b1e1b"
typography:
  headline:
    fontFamily: "Bricolage Grotesque Variable, Instrument Sans Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(28px, 2.6vw, 38px)"
    fontWeight: 600
    lineHeight: 1.05
    letterSpacing: "-0.035em"
  title:
    fontFamily: "Bricolage Grotesque Variable, Instrument Sans Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Instrument Sans Variable, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0"
  label:
    fontFamily: "Geist Mono Variable, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "11.5px"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.05em"
  number:
    fontFamily: "Bricolage Grotesque Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "40px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "-0.04em"
rounded:
  sm: "10px"
  md: "12px"
  lg: "16px"
  pill: "999px"
---

# Design System: Signal (Direction C)

## Overview

Signal is an employee, not a console. The owner checks in for a few minutes a week
and should be able to answer three questions at a glance: is search improving, what
did Signal do, and what does it need from me. Every screen leads with those answers
in plain words, keeps exact evidence one click away, and never shows a number it has
not observed.

The look is a porcelain ground with white cards, near-black ink and one Signal blue.
Blue marks what needs the owner and what Signal is doing; ink carries every primary
action. The weekly loop (observe, analyze, plan, prepare, check, hand off, verify,
measure, report) is the signature visual: a U-shaped line on Home with the real
outcome of each stage.

## Colors

- **Ground and canvas:** the porcelain ground holds the app; white cards hold content.
- **Ink:** primary text and primary buttons. Ink soft and muted carry supporting text
  and metadata; muted passes 4.5:1 on both ground and canvas.
- **Signal blue:** decisions waiting on the owner (Inbox badge, the accent tile, a
  stage that waits on you), the live dot, the "after" side of a change, focus rings.
  It is never decoration. Text in blue uses the darker blue-text tone.
- **Night:** the dark "Signal right now" panel and the autonomy card.
- **Green, amber, red:** verified, attention and failure. Each always appears with a
  word ("Done", "Waiting on you", "Failed"), never as colour alone.

## Typography

Bricolage Grotesque gives headings their voice. Instrument Sans carries body text at
15px. Geist Mono is reserved for small uppercase labels, numbers in tables, file
paths and exact identifiers. All three are self-hosted from pinned OFL font packages,
so the dashboard's `font-src 'self'` policy holds and no font is fetched from a third
party at runtime.

## Layout

The interface fills the full browser viewport with a navigation rail that shares
the page with the work surface; there is no outer frame or mockup margin.

- A sticky top bar: the waveform brand, a site switcher (the current site's host with a
  status dot, a menu of the owner's sites and "Add a site"), one honest environment
  badge ("Development · External writes off"), a Working/Idle state that reflects real
  work, and an account menu with Settings, Help and an explicit Sign out.
- A sidebar of seven destinations, as in the approved prototype: Work (Home, Inbox,
  Activity, Content), Growth (Results), Setup (Connections, Autonomy). Content and
  Results carry tabs to the pages they contain (Strategy, Keyword ideas, Business
  facts; AI answers, Pages), so every page stays one click away. Pilot-only surfaces
  appear only in the local pilot.
- Every page opens with a mono label (for example "Inbox · 3 waiting"), a two-line
  headline whose second line is softer, and the page's own controls on the right.
- No right-hand rail. Context lives in the page it belongs to.
- Under 900px the sidebar becomes a menu in the top bar, an opaque bottom tab bar
  (Home, Inbox with its count, Activity, Results) gives one-thumb access, and tiles
  and grids collapse. The Inbox decision bar rides above the tab bar.

## Elevation

Cards sit one step above the ground with a hairline and a soft shadow. Menus, the
account panel and the mobile navigation sit one step higher. The top bar, the Inbox
decision bar and the mobile navigation panel are the only glass surfaces, each with
an opaque fallback where blur is unsupported.

**The Depth Means Layering Rule.** A shadow reports that a surface is in front of
another. Nothing gets a shadow for emphasis.

**The Glass Needs Something Behind It Rule.** If nothing scrolls under a surface, it
is opaque. Blur is only for the three surfaces content passes beneath.

## Components

- **Cards:** every top-level section is a white card with a 16px radius, a hairline
  border and a soft shadow. There is one card language, not two.
- **Buttons:** ink primary (with a blue focus halo on hover), white secondary with a
  hairline, and a quiet red danger button. Destructive actions are never primary.
- **Pills:** words with a tint; blue only for things waiting on the owner.
- **Home tiles:** Clicks, Impressions and Average position over 28 days from Search
  Console, and AI answers citing you. Each shows the change against the previous 28
  days only when both windows are complete and gap-free, plus a sparkline. A source
  that is missing says "No data yet" and why; there is never a sample number.
- **Weekly loop:** the U-shaped track with six work stages (Research, Plan, Write,
  Ship, Verify, Measure). Each tag counts the recorded work in that stage, turns blue
  when something in it waits on the owner, and filters the work table below. The
  centre states the recorded weekly cycle.
- **Search chart:** a dark panel with the 90-day daily series, three labelled
  gridlines and a marker on every date a change was confirmed live; a marker's
  tooltip names the change and its measured result. The area fill is an SVG fade;
  there are no CSS gradients.
- **Signal right now:** five job tabs (Research, Writing, Technical, AI answers,
  Report) whose rows show only recorded counts, a "Needs attention" line for health
  problems and checks that could not run, and the live dot only while work runs.
- **AI citations grid:** questions by assistant; a dot cites you, a cross cites a
  rival, a small dot cites nobody, a dashed ring was not checked.
- **Activity log:** grouped by day; each entry has a time, a kind (Shipped, Drafted,
  Decided, Measured, Held back), a sentence, what allowed it and its status.
- **Content board:** Ideas, Brief ready, Writing, Your review and Published, from
  recorded topics, briefs, drafts, articles and pull requests. Search volumes appear
  only where a licensed provider reported them.
- **Autonomy levels:** three cards (Ask me first, Small fixes on its own, Fixes and
  refreshes). A level the grant cannot express is shown but cannot be chosen. Raising
  needs a verify-and-change step; limits feed the same grant fields.
- **Application Frame:** top bar, grouped navigation and one work column, edge to
  edge at every width.
- **Unavailable Product Ledger:** Settings → System status lists every capability
  with its real state; unimplemented work stays visibly unavailable.
- **Evidence-Empty Chart:** a chart without observed data shows an empty frame and
  says which connection would supply it, never a sample line.
- **Setup checklist:** on Home for owners until the essentials are done (verified
  site, Search Console, GitHub), with phone approvals and autonomy as optional steps.
  Each step reads a loaded connection state; one that could not be read says "Could
  not check", never "Done" or "To do".
- **Connection tile:** grouped Essential, Recommended and Optional. Each tile has a
  mono badge, the tool's name, what it is for, the connected detail and a worded
  status pill (green ready, amber needs attention, grey off); its controls open from a
  Connect or Manage button. Approval limits read in words, for example "Pull requests
  (A2)".
- **Technical details:** exact identifiers (grant, observation, evidence and
  revision ids) sit in a collapsed "Technical details" element or a tooltip, never in
  the reading line.
- **Inbox:** one list of fixes, articles and facts with All, Fixes, Articles and Facts
  filters. A title or description fix shows what people will see in search, Now and
  After; every fix keeps what changes, what to expect, checks passed, how it ships and
  technical details. An article shows its outline, quality, originality and each
  claim that needs an answer, and keeps its two steps (approve the article, then
  approve the pull request). A fact asks "Can Signal say …?" with where it was found.
  The decision bar keeps Approve, Request changes and Reject together; asking in Slack
  or Telegram sits in a small menu.

## Motion

Short and purposeful: sections rise in on page load, the loop line draws once, the
new text in a change is highlighted once, and the live dot beats only while Signal is
actually working. With reduced motion there is no movement and no entrance delay.

## Do's and Don'ts

Do say what happened in a sentence, show the evidence on request, and state missing
data in words. Don't build black-box AI interfaces that hide evidence, decisions,
costs or approval scope. Don't show raw codes, keys or hashes in the main reading path, repeat
safety notices on every page, colour state without words, or draw a chart, number or
"healthy" state that Signal has not observed.
