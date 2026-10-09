# Slice 0147: Activity and Pages Evidence

This is a product slice and presentation only. It continues the redesign on Activity and Pages, where delivery
and crawl records still showed every hash in the reading line. It changes no loader, form or authority. Based on
main `df1826f`.

## Implemented

- **Activity:** each technical change and owner-reviewed article is a card.
  - A status chip states only the recorded operation fact: "Pull request opened", "Outcome unknown;
    reconciliation required", "Blocked", or the preparing step.
  - Plain facts: the page, the file, your decision, how it was approved (including the decision channel) and the
    pull request.
  - The five-step delivery track is unchanged.
  - Revision, operation, authorization record, owner, write intent and expected tree move under Technical
    details.
  - Delivery observations show when they were last checked, the checks observed and the deployment. The reason
    code, merged commit, fetched page digest and receipt digest move under Delivery evidence.
  - The non-certification footnote is kept, in small type.
- **Pages:**
  - The crawl card reads "Latest site crawl" with readable coverage, and the manifest identity, digest and
    releases sit under Audit manifest.
  - The synthetic-evidence label stays visible as a pill.
  - "Verify origin first" now links to the site panel where verification happens, not to Connections.
  - The homepage observation keeps its verified-origin label and moves its evidence id and body digest under
    Technical details.

Every identifier is still rendered in the page, so the existing exactness tests are unchanged. Unknown outcomes
are still never shown as success.

## Verification

- Dashboard: 271 tests passed.
- Repository: 46 checks passed, including the design guards.
- Typecheck and build pass in `npm test`.
- Desktop and phone renders of Activity and Pages show no horizontal overflow.
