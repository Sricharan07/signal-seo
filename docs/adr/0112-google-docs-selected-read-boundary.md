# ADR-0112: Selected Google Docs Reads and Notion Qualification Deferral

Status: Accepted for slice 0100.

## Context

Revision 4.0 permits owner-selected Google Docs/Notion inputs to the Business
Brain, never provider writes or unselected reads. Secrets and embedded
instructions must stay inside the 0072 encrypted/screened source boundary.
Google's `drive.file` supports per-file consent, but is not itself read-only.
Notion introspection exposes an optional scope string without a documented
mapping to read/write capabilities. Trusting integration configuration alone
would not prove read-only token authority.

## Decision

Use redirect-based Google Picker code consent with server-side PKCE exchange,
exact `drive.file` response-scope verification at bind and refresh, OpenBao-only
refresh tokens, and an immutable owner-selected manifest. Reuse the existing
Google/OpenBao protocols; retain GSC defaults and the accepted GA4 scope.
Local closed profiles permit only exact bounded token exchange and selected-file
metadata/plain-text export reads. Signal never lists, creates or writes files.
Disconnect is durable local revocation plus OpenBao destruction, not an added
remote POST. Restrictions survive independent-journal replay after restore.

Atomically compose source-version registration with the existing encrypted
brand-document upload. Compare metadata around export, preserve immutable
versions, screen all text, and reuse proposed-only Business Brain extraction
with exact document/range provenance. Withdrawal flags retained derived facts
for owner review and removes them from approved grounding, never deleting them.

Defer Notion completely: no bindable OAuth/sync code or network profile. Expose
the exact authorized unavailable reason. Operator qualification must produce
sanitized active/scope evidence from a dedicated read-content-only integration;
a later small slice may encode that verified scope as the only accepted value.
No guessed mapping or trust-only bypass is permitted.

## Consequences

Google consent and token refresh fail closed on absent/ambiguous scope. Owner,
MFA, verified-origin and recovery checks remain current at authority boundaries.
Document sync can operate without a model; fact extraction is visibly unavailable
until the existing model composition is configured. Local provider doubles prove
the deterministic boundaries, not live Google/Notion behavior. Dedicated live
qualification remains `NOT_EXECUTED` and is required before production claims.
