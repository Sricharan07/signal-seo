# Slice 0161: Prototype Parity

This is a product slice. The owner asked for the dashboard to match the approved Direction C prototype, without
settling. This slice rebuilds every prototype screen from real data.

Prototype numbers are samples and are never used. Where data is missing, the screen says so in words.

The top ticker counts real waiting decisions and links to the Inbox. "Ask Signal" is added by 0163 on top of the
0162 assistant API.

Based on 0160 (`b3dec49`, on the merge-train-4 tip).

## Implemented

- **Data:**
  - New server reads (`lib/owner-insights.ts`) cover strategy, AI answers, articles and facts. They reuse each
    browser route's exact relay: the same API call, size bound and strict validation.
  - They run in parallel, only for owners, and only on the pages that show them.
  - Results also merges measurements from the previous twelve weekly reports.
  - Pure derivations live in `lib/insights.ts`: daily series, 28-day comparisons (two complete, gap-free windows or
    none), chart geometry, change markers, the citations grid, work items, jobs, activity entries and change
    results. Search-result previews live in `lib/serp.ts`, which reads only the title and description and never
    renders HTML.
- **Home:**
  - The mono week label and a two-line headline ("Clicks up 22% in 28 days." when a full comparison exists, then
    the decisions line).
  - Four metric tiles with deltas and sparklines.
  - The six-stage loop (Research, Plan, Write, Ship, Verify, Measure) filtering a work table.
  - The 90-day clicks chart with markers for changes confirmed live.
  - "Waiting on you" across fixes, articles and facts.
  - "Signal, right now" with five job tabs and a "Needs attention" line for health problems and unknown checks.
  - The AI citations grid.
- **Inbox:**
  - One list of fixes, articles and facts with filters, and the dark empty state.
  - Title and description fixes show a search-result preview, Now and After.
  - The existing fix decision forms are unchanged.
  - Articles show their outline, quality, originality and flagged claims, and keep both steps (approve the article,
    then approve the pull request with acknowledgements).
  - Facts ask "Can Signal say …?" with approve, correct and remove.
- **Activity:** a day-grouped log of prepared fixes, decisions, pull requests, live confirmations, drafts,
  measurements, held-back weekly stages and weekly reports. Each entry says what allowed it, with kind filters. The
  delivery cards and weekly report stay below.
- **Results:**
  - The metric chart (clicks, impressions, position) with change markers.
  - "What each change did", from measured horizons.
  - The citations grid with its legend. Search tables stay below.
- **Content:** the board (Ideas, Brief ready, Writing, Your review, Published), with briefs and drafts tools below.
- **Navigation:**
  - The prototype's seven destinations.
  - Tabs under Content (Strategy, Keyword ideas, Business facts) and Results (AI answers, Pages).
  - A top-bar site switcher posting the existing site-selection form.
  - The sites panel in Settings.
- **Connections and Autonomy:**
  - Connections are tiles in Essential, Recommended and Optional groups, keeping each connector's controls inside.
  - Autonomy shows three level cards over an unchanged grant: an unsupported level cannot be chosen, raising goes
    through verify-and-change, and the limits feed the same fields.
  - The grant and revoke request code is byte-identical to the base.

## Honesty and authority

- No new API, migration, permission or request shape.
- Every decision still posts the existing command.
- Level 2's text says what the current grant does: drafts and sandbox patches, with pull requests still needing
  approval.
- Unknown health checks are listed as "could not be checked".

## Verification

- `npm test`: repository checks, docs, dashboard tests, typecheck and build.
- New tests:
  - `tests/insights.test.ts`: 8 pure-logic tests, including gap-free windows, unknown-never-cited, markers only for
    live changes, and activity without raw codes.
  - `tests/serp.test.ts`: 4.
  - `tests/parity.test.tsx`: 10 render tests across Inbox, Activity, Results, Content, navigation, site switcher
    and Autonomy.
- Earlier Home tests were restated for the new layout with the same intents.
- Desktop and phone renders of every page show no horizontal overflow.
