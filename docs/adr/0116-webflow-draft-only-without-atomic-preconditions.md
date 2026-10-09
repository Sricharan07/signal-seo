# ADR-0116: Webflow Draft Creation Without Atomic Preconditions

Status: Accepted for slice 0098's local boundary; live Webflow `NOT_EXECUTED`.

## Context And Research

Reviewed Webflow's official Data API v2 documentation on 2026-10-02 only:

- [Update Single Item](https://developers.webflow.com/data/reference/cms/collection-items/staged-items/update-item): PATCH accepts field data, draft/archive flags and locale, but documents no ETag/If-Match header, expected version or other atomic precondition. `lastUpdated` is response-only.
- [Update Items](https://developers.webflow.com/data/reference/cms/collection-items/staged-items/update-items): the staged bulk update contract likewise documents no atomic expected-revision condition.
- [Publish Items](https://developers.webflow.com/data/reference/cms/collection-items/staged-items/publish-item): POST takes item IDs and optional locales, not expected versions. Publication can also clear the draft flag. A documented 409 error is not an atomic concurrency contract.
- [Create Items](https://developers.webflow.com/data/reference/cms/collection-items/staged-items/create-items): current v2 stages items at `POST /v2/collections/{collection_id}/items/insert`. The one-item primary-locale subset explicitly sends `isDraft: true`, with no existing item ID, archive flag, locale variants or live endpoint.
- [List Items](https://developers.webflow.com/data/reference/cms/collection-items/staged-items/list-items): exact slug filtering and bounded pagination support read-only reconciliation.
- [OAuth](https://developers.webflow.com/data/reference/oauth-app), [Authorization Info](https://developers.webflow.com/data/reference/token/introspect), [Scopes](https://developers.webflow.com/data/reference/scopes), [Custom Domains](https://developers.webflow.com/data/reference/sites/get-custom-domain), [Collections](https://developers.webflow.com/data/reference/cms/collections/list) and [Collection Details](https://developers.webflow.com/data/reference/cms/collections/get) describe consent, actual grant inspection, published domain evidence and schema binding.

Conclusion: **no documented atomic precondition for CMS update or publish**.
This is a conservative capability decision, not an experimentally proven claim
about undocumented server behavior. No documentation or read-only timestamp
promotes a capability to CAS. New official support would require a superseding
decision, certification and new tests, not a runtime feature flag.

## Decision

Use `DRAFT_ONLY`, never read-then-write. Updates and publishing are unavailable
with `WEBFLOW_UPDATE_ATOMIC_PRECONDITION_UNAVAILABLE` and
`WEBFLOW_PUBLISH_ATOMIC_PRECONDITION_UNAVAILABLE`; the owner publishes in Webflow.
Even exact owner approval or fresh MFA cannot enable either operation. Standing
authorization cannot deliver articles.

A deterministic operation suffix in the slug is an identity marker, **not native
provider idempotency**. Commit one durable identity and independently journal its
minimal secret-free intent before dispatch. Permit at most one POST. After any
potential transmission, only reads may run: one exact match records the resource;
zero retains `OUTCOME_UNKNOWN` and quarantine; multiple matches escalate. A later
commit may resolve a previous zero-match read without a second POST. Provider
rejection, malformed success, timeouts and local receipt loss never grant retries.
No certified consistency window or live-delivery claim is invented.

This narrows Revision 4.0 sections 6.2/12 and EC-144 according to the slice brief
and Revision 3.2 sections 10.3/19.3/19.4. It does not amend protected revisions.

## Alternatives

Read-then-PATCH, comparing `lastUpdated` locally, publishing after an owner click,
and retrying after an empty search leave a race or ambiguous external effect.
They are rejected. Documented atomic revision preconditions and a superseding
decision are required for future updates or publishing.

## Consequences And Verification

Only one new primary-locale draft is supported per sealed candidate. Creation is
not deployment or publication. An unresolved independent intent blocks further
site writes, even after primary restore. The encoded absence path and forbidden
endpoints run in `scripts/run-webflow-tests.py`;
[slice 0098](../implementation/0098-webflow.md) records qualification and limits.
