# Slice 0160: Strategy and Results UI

This is a product slice and presentation only. It is the last phase of the Direction C dashboard redesign. It
covers Strategy, Search results, Pages evidence, the weekly report and change measurements, whose files changed in
merge train 4 (0140, 0141, 0144). Loaders, forms, command payloads and authority are unchanged.

## Implemented

- **Strategy:**
  - The method reads as a sentence with a "Deterministic fallback" label, and says plainly that it is not a ranking
    prediction.
  - Each proposal shows a readable state pill, then "Priority N · about N days of work".
  - The full priority formula, evidence links and inputs move under "Rationale and inputs".
  - Accept and Dismiss use the ink primary and secondary buttons.
- **Every SEO view:**
  - "Unavailable sources and capabilities" is no longer repeated in full on every page. It collapses into "Sources
    not available yet (N)", and every source and reason is still rendered.
  - Titles are in sentence case ("SEO baseline", "90-day strategy").
- **Weekly report:**
  - Skill outcomes use the shared outcome labels.
  - Stage evidence references, upcoming revisions and digests move under Evidence or Technical details.
  - Delivery stages and reasons read as words.
  - The upcoming week reads "Week of Oct 5".
  - The last "Business Brain" label now reads "Business facts".
- **Change measurements:**
  - Headings read "7-day observation", "Search Console, this page", "Bing, whole site (context)" and "Bing, this
    page".
  - Observation states and reasons read as words. Per-source, as-reported, incomplete-coverage semantics are
    unchanged.

## Verification

- Dashboard: 278 tests passed.
  - Four assertions were updated to the new wording with the same intent: separate Bing page rows, states as words
    never zeros, failed skills shown as failed, and the Business facts name.
- Repository: 46 checks passed, including the design guards.
- Typecheck and build pass in `npm test`.
