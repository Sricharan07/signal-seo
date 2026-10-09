# ADR-0173: Shared Connector Lifecycle Mechanics

Status: Accepted for security-critical slice 0166.

## Context

Owner-approved track B is a behavior-preserving Python refactor based on
`a519a65`. Revision 4.0 section 12 and Revision 3.2 sections 2 and 10 require
site-bound, revocable connectors with OpenBao-only credential custody. Lifecycle
implementations repeated nonce consumption, PKCE, staged-secret cleanup, refresh
rotation, restriction handling and KV-v2 metadata checks.

## Decision

Use `connector_framework.py` for shared lifecycle mechanics and
`connector_secrets.py` for OAuth credential custody and active KV-v2 parsing.
Provider adapters retain scopes, callback ordering, resource validation, provider
calls, SQL authority ports, error types, cleanup policy and public projections.
GSC and Bing share an OAuth binding adapter; GA4 and Docs reuse PKCE mechanics.
GitHub read/PR adapters reuse fixed outcome handling. Slack and Telegram reuse
restriction and external-revocation mechanics. DataForSEO, WordPress and Webflow
reuse the database or KV-v2 mechanics appropriate to their non-OAuth/limited flows.

Do not normalize revocation outcomes: pending journal restrictions remain pending,
including `AUTHORITY_DURABILITY_PENDING`. SQL remains the policy/authority boundary.
No route, request/response field, dashboard, migration, grant, provider capability
or production availability is changed. Duplicate-key JSON parsing uses one hook
only at boundaries that already rejected duplicate keys.

## Alternatives And Consequences

A single universal provider state machine would obscure important differences:
Docs' retained withdrawal review, Bing's non-PKCE callback, Telegram's one-shot
webhook removal, and provider-specific ambiguous/error cleanup policies.
Independent copied lifecycles retain those differences but invite inconsistent
fixes. Small shared mechanics plus adapters keep both explicit.

The line-removal target is subordinate to preserving those contracts; adapters
are not flattened solely to reach a line count. No dependencies are added.
Existing tests remain unchanged; positive, negative and failure regressions and
real PostgreSQL/OpenBao labs qualify the shared implementation. Live provider
success remains NOT_EXECUTED. See the
[implementation record](../implementation/0166-connector-framework.md).
