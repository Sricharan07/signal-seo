# ADR 0098: Private Test Owner Bootstrap

Status: Accepted; live owner/login qualification complete in the dedicated test boundary.

## Decision

The human approved one isolated Signal Test workspace and owner membership for
the existing verified Google-linked identity, requiring fresh signed completed-OTP
proof. This is not public signup, email-based account linking, a synthetic pilot
owner, a standing authorization, or an external-write grant.

An optional login assertion observer runs only after the normal signature,
issuer, audience, nonce, state/browser-binding and PKCE checks have succeeded.
It is absent from default composition and does not run for invitation identity
proofs. The dedicated profile can capture one exact approved identity's signed
ID token and short-lived access token in a create-only mode-0600 container-tmpfs
artifact. The access token is necessary to revalidate a signed `at_hash` when
present. Neither token is returned to the browser or logged. The artifact expires
after five minutes or the shorter one-hour approval window, and successful
operator completion stops recapture. The public API receives no provisioning
credential, SQL permission or bootstrap route.
For a profile configured at startup, the protected operator file can renew the
same bounded approval window without a workload restart. Every capture rereads
and validates that private file; read failure denies capture and expires retained
proof. No browser or API request can renew the window, change the hard-pinned
identity, or reenable capture after its completion marker.

A separate human-invoked private operator revalidates that original signed
assertion with the current real issuer's signing keys and the database's actual
consumed login attempt and nonce hash. Exact issuer/subject/client, verified
approved email, admitted ACR, signed `otp` method, token expiry and five-minute
authentication freshness are mandatory. Email alone and enrollment metadata
are insufficient. An approved, protected intent records proof hash and intended
UUIDs before any committing transaction; a separate create-only outcome records
readback. Tokens are not retained in these audit records.

The private database maintenance connection may insert the exact verified global
user only while the dedicated registry is empty. Tenant/directory/owner inserts
use `SET LOCAL ROLE signal_bootstrap` and the existing forced-RLS tenant context.
Bounded locks serialize the empty-registry and fresh-attempt checks with inserts.
The actual transaction is rehearsed with rollback first. Existing or changed
state is denied, never upserted or reactivated. An unknown outcome must be
inspected using the prepared UUIDs, not retried. No site, session, standing grant,
provider binding or operation is synthesized. Normal OIDC login subsequently
issues the actual application session under unchanged MFA and recovery policy.

## Recovery Boundary

The old private deployment token has expired. OpenBao 2.6.1 rejected the legacy
unauthenticated quorum endpoint with 405 before any recovery shares were
submitted. Its authenticated replacement requires a currently authorized
credential. The implementation does not silently switch endpoints, reenable
legacy recovery, revive a retired token or broaden a policy.

The human approved limited quorum recovery into the existing deployment role.
The human separately approved the temporary private-listener compatibility
exception. The live procedure paused the API, retained private TLS/network
isolation, recovered only the unchanged limited role with two shares, and
revoked/denied the temporary root. It restored the secure listener configuration
byte-for-byte and verified the legacy endpoint's rejection before resuming
login. The new API image was deployed with fresh one-use workload credentials;
the temporary limited operator was then also revoked/denied. This is not an
unattended recovery path or standing permission to leave compatibility enabled.

Primary references: [OpenBao endpoint deprecation](https://openbao.org/community/deprecation/unauthed-generate-root/)
and [exact 2.6.1 authenticated implementation](https://github.com/openbao/openbao/blob/v2.6.1/vault/logical_system_generate_root.go).

## Consequences

The isolated owner bootstrap and normal owner/MFA session are locally and live
qualified in the dedicated test environment. First
proof capture can deliberately leave the normal callback rejected while the
identity remains unregistered. The human then signs in again after private
provisioning; no session is manufactured to conceal that boundary. Google MFA
enrollment remains real and preserved. The temporary assertion capture was retired
after private provisioning; the final API uses normal OIDC/session checks only.
Slack/GSC/GitHub runtime qualification,
production onboarding and all external writes remain unavailable.
