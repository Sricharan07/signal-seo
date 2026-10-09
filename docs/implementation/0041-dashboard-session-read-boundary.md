# Slice 0041: Dashboard Session Read Boundary

- Status: Implemented and locally qualified; live customer login unavailable
- Date: 2026-09-09
- Milestone: M2 partial
- Specification: Revision 3.2 sections 1.4, 2 INV-002/INV-003, 3.1, 10,
  24.1, 24.2 Sign in/account security, and 24.5
- Decision: [ADR-0041](../adr/0041-forward-only-one-validated-session-cookie.md)

## Scope

This slice adds a server-only current-session read to the dashboard. One exact
host-only tenant cookie can now be revalidated through the existing Signal API,
and the Overview renders a bounded role/MFA/workspace projection or an explicit
signed-out, unavailable, or rejected state.

It does not enable invitation delivery, OIDC login, tenant selection, session
cleanup/logout, organization names, site selection, or any mutation. The default
local browser remains signed out because customer authentication is intentionally
disabled.

## Implementation

| Component | Implemented behavior |
| --- | --- |
| Shared API-origin validator | Both public status and session reads reject credentials, paths, queries, fragments, and non-HTTP(S) schemes |
| Cookie extraction | Next.js reads all values for the exact host-only tenant-cookie name; missing is signed out and duplicate/malformed values fail before I/O |
| Server session reader | Forwards only the one validated 43-character token, never the original browser cookie header |
| Response boundary | Requires HTTP 200 JSON, exact fields, schema version one, canonical UUIDs, closed roles/authentication levels, future at-most-24-hour expiry, 16 KiB body, and numeric declared length |
| Failure mapping | Maps 401 to signed out, 503/transport to unavailable, and malformed/unexpected responses to rejected without raw errors |
| Account projection | Shows a session badge, role, MFA state, shortened tenant identity, and explicit absence of production authority; user UUID and bearer are not rendered |
| Capability hardening | Rejects a malformed declared content length on the existing public capability response |

## Data Flow And Storage

The browser's secure host-only cookie remains the credential. The dashboard reads
it only on the server and transmits one exact value to the API over the configured
server connection. The API remains authoritative: its existing gateway hashes the
token for PostgreSQL lookup and rechecks current session/membership/recovery state.

The dashboard does not persist, log, decode, hash, cache, or return the credential.
It retains no session database. It validates both server-returned UUIDs, then
discards the user ID before producing the render projection. The view receives
only tenant ID, role, authentication level, and expiry after exact validation and
renders only a shortened tenant ID. Neither session state nor the public capability
response confers site or production-write authority.

## Verification

Run:

```sh
npm run test:dashboard
npm run typecheck:dashboard
npm run build:dashboard
npm run test:repo
npm run check:docs
```

Seventeen dashboard tests cover valid, absent, duplicate, malformed, expired,
over-lifetime, unavailable, unauthorized, unsafe-origin, invalid-content, and
rendered account states in addition to the existing public status boundary.
Nineteen repository tests enforce the server-only cookie and API boundary. The
unchanged API, identity, and tooling regression remains 624 passing cases.

Manual production-browser checks against the real local API cover signed-out state
at 1440 by 900 and 390 by 844. The account badge remains visible and the mobile
body has no horizontal overflow. Authenticated rendering is contract-tested with a
bounded projection but not provider-qualified because the default API does not
compose customer identity credentials.

- [0041 dashboard session qualification](../evidence/0041-dashboard-session.json)

GitHub Actions remains externally unavailable because the account's payments or
Actions spending limit blocks jobs before checkout. No remote Slice 0041 runtime
claim is made.

## Explicit Limits

- No live OIDC, MFA, invitation, or customer credential is configured or tested by
  this slice.
- A server-component read cannot clear an invalid host-only cookie; bounded
  same-origin session management remains required.
- The current API session response does not include organization or site names.
- The public Overview still contains no tenant/site business data and does not
  become an authenticated application solely because an account badge exists.
- There is no cross-tenant browser qualification or fresh-browser owner journey in
  this slice.

## Next Safe Dependency

Add same-origin dashboard identity initiation/callback/session cleanup and the
owner's invitation-to-login transition using the existing purpose-bound API
contracts, then derive organization and site selection from current authority.
