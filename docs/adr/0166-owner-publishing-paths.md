# ADR-0166: Owner Publishing Paths

Status: Accepted for local security-critical implementation; live CMS qualification pending.
Date: 2026-10-04.

## Context

Webflow has a bounded OAuth, mapping, sealing and revocation service, but its owner
API exposed only read and review. WordPress required internal identifiers that an
owner could not discover. This is a security-critical slice under Revision 4.0
section 19, not a new grant to publish.

## Decision

Reuse WebflowService behind an optional composed owner port. Expose only begin,
complete, seal and revoke, alongside the existing Inbox read/review. All mutations
require the existing browser mutation proof. PostgreSQL resolves current owner,
site and recovery authority and requires MFA authenticated within five minutes
before credential-bearing mutations. Callback state remains hashed, owner-bound,
single-use and ten-minute limited. The dashboard stores only site, attempt and
confirmed mapping in a short-lived Secure/HttpOnly host cookie; tokens stay in
OpenBao. Callback code/state never enter the redirect or error response.

Require a recorded exact article delivery approval with current owner/epochs and
recovery generation before Webflow sealing, including replays. CMS Inbox approval
remains separate. A bounded, function-only owner projection supplies readable
article titles, candidate digests and reviewable WordPress draft identifiers.
WordPress offers current binding and Article selects with unchanged command
payloads. Initial credentials and binding are still operator-provisioned.

Use the existing shared owner egress engine for Webflow onboarding: two exact
OAuth/revocation POST endpoints and four validation GET shapes, further restricted
to a consumed current owner attempt's site/collection. No item write is admitted
by this owner path. Existing Webflow draft-write profiles and the lab-only deliver
gate are unchanged. Only the exact owner-robots GET for api.webflow.com is added
to the global origin guard; queries and other non-profile routes remain denied.
Revocation remains locally effective before OpenBao/provider
cleanup and preserves AUTHORITY_DURABILITY_PENDING until independently acknowledged.

Migration 0095 follows 0094 (merge train 5; written as 0090 after 0089) and adds no tables or dependencies. Existing migrations
and accepted specifications remain byte-for-byte unchanged.

## Alternatives

- Generic CMS mutation endpoint or enabling deliver: rejected; this would expand
  external-write authority beyond the requested owner paths and qualification.
- Browser tokens or application-password forms: rejected; they defeat OpenBao-only
  storage and the operator provisioning boundary.
- Selecting every draft or inventing binding discovery from secret paths: rejected;
  selections must come from bounded current-owner projections, not a secret inventory.
- Frontend-only approval/MFA checks: rejected; direct API calls and replays must
  obey the same database authority checks.

## Consequences

An unconfigured optional Webflow port is unavailable, never connected by default.
An operator must supply the API-role factory, independently read recovery authority,
scoped OpenBao Webflow client, shared egress and encrypted artifact dependencies.
Dedicated-environment runtime deployment and live Webflow OAuth remain unqualified;
this slice does not extend that environment's resource authorization. Owners still
publish manually in their CMS. Lost responses remain unconfirmed, not success.

## Verification

See [0156 implementation](../implementation/0156-publishing-paths.md) and
[evidence](../evidence/0156-publishing-paths.json) for positive, negative and failure
checks, PostgreSQL/OpenBao qualification and explicit live exclusions. The full
gate is intentionally not run, and no Temporal process is started.
