# Slice 0023: Invitation Browser Acceptance

Status: **IMPLEMENTED AS AN UNCONFIGURED HTTP CONTRACT; NOT CUSTOMER-ENABLED**.

## Outcome

The invitation flow now composes the purpose-bound OIDC callback, short-lived
identity proof, proof-bound browser security, and atomic invitation acceptance.
The normal login and invitation paths share provider verification but issue
different credentials and authority.

| Boundary | Implemented behavior |
| --- | --- |
| OIDC start | Persists `invitation_acceptance` before redirect; no invitation ID or bearer enters provider state |
| Callback | Reads immutable purpose, consumes state/browser binding and PKCE once, validates Keycloak, then issues only an invitation proof |
| Browser proof | Separate host-only secure HttpOnly cookie, CSRF bound to the opaque proof, maximum ten-minute lifetime |
| Acceptance | Strict body-only UUIDv4/bearer/name contract plus exact Origin, Fetch Metadata, CSRF, and proof-cookie checks |
| Authority | One existing database transaction consumes proof and invitation, creates exact account/site authority, and appends audit evidence |
| Failure | Generic denial preserves an unconsumed proof for correction; success alone clears it; unexpected failures disclose no body or credential detail |

Ordinary login still obtains the independent recovery generation before consuming
its callback so a recovery-authority outage remains retryable. Invitation-purpose
completion does not read that authority and cannot issue an identity session.
Missing verified email and proof-persistence failure append one typed, sanitized
event only after the OIDC attempt has been consumed.

## HTTP Contract

`GET /v1/invitations/verify` optionally accepts one validated local
`return_path`, defaulting to `/invitations/accept`. It never accepts an invitation
ID or bearer. It clears a stale invitation proof, sets the one-attempt OIDC browser
binding, and preserves any existing login or tenant cookies.

`GET /v1/session/callback` selects its output from the immutable attempt purpose.
An invitation result clears the OIDC binding and sets only
`__Host-signal_invitation_identity`. A normal login clears stale tenant and
invitation proof cookies and sets only the pre-tenant identity credential.

`GET /v1/invitations/csrf` requires exactly one valid invitation-proof cookie and
returns only its deterministic HMAC proof. `POST /v1/invitations/accept` accepts a
UTF-8 JSON object no larger than 2,048 bytes with exactly these fields:

```json
{
  "invitation_id": "10000000-0000-4000-8000-000000000001",
  "token": "vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv",
  "display_name": "Invitee User"
}
```

The UUID must be version 4, the bearer must be exactly one 256-bit unpadded
base64url-shaped value, and the name must be bounded, trimmed, and free of control
characters. Duplicate keys, unknown fields, malformed UTF-8, encoded bodies,
wrong media types, duplicate browser proofs, and cross-origin mutations fail
before the gateway. Responses never echo either opaque credential.

## Data And Audit

No new business table is added. Migration `0013` expands the existing strict
`identity.login.failed` reason set for invitation verification. The browser proof
continues to be stored only as SHA-256 in
`control.invitation_identity_proofs`; the invitation bearer remains hash-only in
`app.invitations`. Their existing atomic acceptance and expiry-cleanup behavior is
unchanged.

The API returns only accepted invitation, tenant, site, and user IDs, role, and
acceptance time. It does not return email, OIDC issuer/subject, internal membership
or audit IDs, hashes, tokens, or provider data.

## Verification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

Twenty-four net-new API cases bring that suite to 130 and the complete
non-database Python suite to 295. Eight net-new PostgreSQL cases bring the real
PostgreSQL 17.11 suite to 286. Reviewed source-hashed evidence is
[0023-postgresql.json](../evidence/0023-postgresql.json); it records the pinned
image, successful migration/tests, completed disposable cleanup, and no production
authority. The unchanged provider boundaries also pass five real Keycloak and
seven real OpenBao scenarios with separate source-hashed
[Keycloak](../evidence/0023-keycloak.json) and
[OpenBao](../evidence/0023-openbao.json) evidence.

## Explicit Limits

- The default API has no credential-bearing gateway or browser-security key, so
  the routes return fail-closed service errors and customer authentication remains
  disabled.
- No frontend acceptance screen, invitation delivery, resend/revoke operation,
  throttling, lockout policy, upstream body-log qualification, or deployed cleanup
  schedule exists. The journey must not be customer-enabled yet.
- Acceptance does not issue a login session. The invitee must complete a separate
  ordinary login once post-acceptance transition behavior is designed.
- A local return path is not a secret channel. Callers must never place invitation
  bearers, provider tokens, or personal data in it.
- There is no dashboard, Telegram surface, workflow engine, reasoning agent,
  human approval flow, undo operation, customer connector, or production write.

See [ADR-0023](../adr/0023-dedicated-invitation-browser-proof.md) for credential,
purpose, retry, and transport decisions.
