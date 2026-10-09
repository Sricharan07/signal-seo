# ADR-0117: Bound, Owner-Reviewed Webflow Draft Delivery

Status: Accepted local boundary for security-critical slice 0098; live Webflow `NOT_EXECUTED`.

## Context And Decision

Use existing owner authorization, shared egress, OpenBao transport, independent
intent/restriction journals and Content Writer candidates. No new dependency,
dormant service, production composition or live qualification is introduced.

Webflow Data Client OAuth requests exactly `cms:read cms:write sites:read`.
`sites:read` is necessary for the documented published custom-domain evidence.
Inspect the actual authorization: authorization-code grant, precisely these
scopes, exactly the owner-selected site and no workspace-wide grant. Reject
broader credentials rather than merely declining to use their extra scopes.
Persist only a one-use hashed state, fixed redirect, actor/site/recovery binding
and OpenBao reference. Official OAuth documentation does not specify PKCE or a
refresh-token contract; do not invent either. Token-exchange ambiguity burns the
attempt and requires fresh consent. Tokens and client secrets exist only in
OpenBao and transient credential-bearing transport memory, never database rows,
provider error messages, telemetry or approval payloads.

The owner confirms one collection and an exact mapping of name, description and
escaped article body to compatible editable plain/rich-text fields. Refuse
unmapped required fields, reference/media/component fields, localization and
schema drift. Binding requires the collection to belong to that site and the
site's published custom domain to equal the current verified HTTPS origin.
Existing-item refresh is unavailable under ADR-0116.

## Authority And Recovery

Create a separate immutable CMS-targeted revision from a sealed 0082 candidate.
The owner reviews the exact site, collection, mapping, slug and field payload in
the Inbox; a GitHub-shaped approval cannot silently authorize a CMS write.
Articles remain `autonomy_eligible=false`, A2 with threshold 0.95. No standing
grant or model recommendation is an execution path. Recheck the current owner,
approval expiry/epochs, recovery generation, site verification/pause, binding,
source revision and provider grant/schema before the one permitted transmission.

One-shot dispatch is recorded before network I/O and rechecked at the
credential-bearing fetcher. Independent intent acknowledgement precedes that
transition. A worker loss or ambiguous completion retains the creation identity
and blocks conflicting creates. Read-only reconciliation is allowed to observe a
late commit; it cannot reset dispatch state or mint another permit.

Disconnect first restricts locally and enqueues the stable restriction in the
existing independent journal. It reports `AUTHORITY_DURABILITY_PENDING` until
verified acknowledgement. Deny-only restore replay retains missing-target
tombstones, and reconnection never revives prior approvals. Upstream revocation
is best effort after local containment; local OpenBao access is destroyed even
when Webflow's revocation response is ambiguous. Unknown in-flight effects stay
visible. There is no delete, inverse overwrite, unpublish or archive recovery;
the owner inspects and publishes the draft in Webflow.

## Qualification

Closed profiles separate API v2 reads/draft creation from the fixed OAuth
exchange and revocation endpoints. Only the bound token, exact site/collection,
bounded one-item body and bounded responses/time are allowed. No DELETE, PATCH,
live, publish, design, hosting, users, pages or schema-mutation route exists.
Synthetic provider doubles must run behind shared egress. PostgreSQL and the
independent journal qualify persistence, role/tenant isolation, races, delayed
commit and failure handling. Real OAuth, provider semantics and production
writes remain `NOT_EXECUTED` until separately qualified.

## Alternatives And Consequences

Broad workspace OAuth, plaintext database tokens, generic credentialed HTTP,
standing grants for articles, and browser-driven CMS writes are rejected. A
separate CMS revision avoids reusing a repository approval for a different effect.
Unsupported field formats and real grants fail closed instead of requesting
design authority. The owner must publish manually. Local test switches grant no
production authority, and no public dispatch endpoint is installed.

## Verification

Run the complete gate in [0098](../implementation/0098-webflow.md). Real
PostgreSQL, independent journals and TLS OpenBao qualify local boundaries;
synthetic Webflow doubles use actual shared-egress admission. Live OAuth,
provider ambiguity windows and deployed composition remain `NOT_EXECUTED`.
