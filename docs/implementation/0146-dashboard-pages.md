# Slice 0146: Dashboard Pages, Phase 2a

This is a product slice and presentation only. It extends the 0145 redesign to the pages that still read like a
console. It uses the existing loaders, forms and request bodies unchanged and adds no API, migration, egress
profile or permission. Based on main `e9e3fd7`.

Strategy, Search results, Pages and the weekly report are deferred to phase 2b. The open keyword-ideas and
internal-links slices are editing those files, and phase 2b lands after they merge.

## Implemented

- **Home setup checklist** for owners:
  - Required steps: verify the site, connect Search Console, connect GitHub.
  - Optional steps: phone approvals and standing autonomy.
  - Each step reads a connection state the page already loads. A state that could not be read says "Could not
    check", never "Done" or "To do".
  - The checklist hides once the required steps are done. Viewers never see it.
- **Phone tab bar** under 900px: Home, Inbox with its count, Activity and Results.
  - Opaque, so the three-glass-surface rule is unchanged.
  - The Inbox decision bar sits above it.
- **Connections:**
  - Grouped by purpose into what Signal needs, approve from your phone, more data, and content sources and
    publishing, in a two-column grid.
  - A shared worded status pill whose tone follows the words.
  - Ink primary buttons.
  - Approval limits read as "Pull requests (A2)".
  - DataForSEO spend is shown as money.
  - The Google Docs document id moves to a tooltip.
  - IndexNow gets a status pill.
- **Autonomy:**
  - The grant reads as "Signal may do / Each week, at most / Ends", with a plain grant state.
  - The grant id and work-type codes move to Technical details.
  - The grant and revoke request bodies are byte-identical.
- **Business facts:**
  - Categories and statuses in sentence case.
  - Worded Approve, Correct and Remove buttons.
  - Decision ids moved off the reading line into the provenance link's tooltip.
- **Articles:** drafts are titled by their brief's topic instead of a UUID, and brief and draft states are
  readable.
- **AI answers:**
  - Provider names instead of keys, readable dates, and question, observation and crawl ids under Technical
    details.
  - The fact-row flex layout no longer applies to nested lists.
- **Settings:**
  - Site details are a definition list instead of read-only inputs.
  - Brand documents explain what they are for and have a styled file picker.
- **Chat:** the readiness list no longer says "Not paired" when Telegram is paired. The row now describes the
  unimplemented conversation handoff.
- **Copy and records:**
  - Removed `text-transform: capitalize` that turned sentence case into title case.
  - `DESIGN.md` and `.impeccable/design.json` document the setup checklist, connection status pill, phone tab bar
    and Technical details convention.

## Found, not fixed here

The AI-answers projection in `services/control_plane/src/signal_core/ai_visibility_agent.py` still reports
"no AI-visibility scheduler yet (slice 0131)", although 0131 implemented the schedule and the structured-data
recipe. That is product-code behavior, not presentation, so it is queued for the product-code pass.

## Verification

- Dashboard: 271 tests passed.
  - New tests: the setup checklist reads real states (could not check, to do, hidden when ready, hidden for
    viewers), and Chat never contradicts the Telegram pairing.
  - Updated: the Telegram pairing wording.
- Repository: 46 checks passed, including the design guards (no gradients, exactly three glass surfaces).
- Typecheck and production build pass in `npm test`.
- Desktop (1440px) and phone (390px) renders of the changed pages show no horizontal overflow.
