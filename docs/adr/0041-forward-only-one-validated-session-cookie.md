# ADR-0041: Forward Only One Validated Tenant Session Cookie

- Status: Accepted
- Date: 2026-09-09
- Scope: Dashboard current-session read boundary

## Context

The dashboard shell needs to evolve from public system status toward authenticated
owner context. Signal already exposes `GET /v1/session`, which revalidates an
opaque tenant-session cookie against current server-side authority and returns a
bounded tenant/user/role/authentication projection. The dashboard must not copy
session validation into browser JavaScript or forward the browser's entire cookie
header to another process.

A missing cookie is ordinary signed-out state. Duplicate, malformed, expired, or
unexpected session data must not be displayed as authority. Transport and service
failure must remain distinguishable from a rejected response without exposing a
token, raw response, or internal error.

## Decision

Read the host-only tenant cookie inside the Next.js server component. Forward only
one exact 43-character base64url token to the configured Signal API as one
purpose-built `Cookie` header. Missing tokens return signed-out state without a
network request; duplicate or malformed values are rejected locally without
dispatch.

Fetch only `GET /v1/session` with no-store caching, rejected redirects, and a
2.5-second timeout. Accept only HTTP 200 with JSON content and an exact version-one
schema containing canonical UUIDs, a closed role, primary or MFA authentication,
and a future expiry no more than 24 hours away. Bound the body to 16 KiB and reject
invalid declared lengths. Map 401 to signed out, 503 or transport failure to
unavailable, and every other response to rejected state.

Discard the validated user UUID before passing the projection to the view. Show
role and MFA status, a short tenant identifier, and explicit session authority.
Never render the user UUID, opaque token, raw provider content, or implied site or
production authority.

## Consequences

- The browser never calls the internal session API or receives its configured
  origin through client code.
- Unrelated browser cookies cannot cross the dashboard-to-API boundary.
- The account badge and workspace summary reflect current server-verified context
  when it exists, while signed-out, unavailable, and rejected states remain
  explicit.
- A 401 response cannot clear the host-only cookie during server-component render;
  a later same-origin session-management route must perform bounded cookie cleanup.
- The first live authenticated browser journey still depends on an enabled
  invitation/login gateway and dedicated Keycloak environment. This slice does not
  activate either.
- No organization name or site identity is available in the current session
  contract, so the UI uses only a shortened tenant identifier and keeps site state
  unconnected.

## Alternatives Rejected

- **Forward the incoming `Cookie` header:** can disclose unrelated or future
  privileged cookies to the API process.
- **Decode or trust the opaque token in Next.js:** duplicates authorization logic
  and turns a bearer credential into browser-tier authority.
- **Call `/v1/session` from client JavaScript:** exposes topology, creates another
  browser trust boundary, and encourages client-side authority caching.
- **Treat API reachability as authentication:** confuses process health with
  current identity and tenant authority.
- **Render raw tenant and user identifiers:** exposes unnecessary identity detail
  without improving an owner workflow.

## Verification

Dashboard tests cover exact cookie forwarding, absent/duplicate/malformed tokens,
valid role and MFA projection, 401, 503, transport failure, unsafe API origins,
invalid content type/length/schema/role/expiry, and UI authority labels. Repository
tests assert server-only cookie extraction and fail-closed forwarding. Responsive
browser checks verify the signed-out badge at desktop and 390-pixel mobile widths
without horizontal overflow.
