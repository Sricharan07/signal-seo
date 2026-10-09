# 0158 - Team invitations

## Classification

Security-critical: identity, tenancy and site authority, under Revision 4.0
section 19. [ADR-0168](../adr/0168-owner-team-invitations.md) records the decision.
[Evidence](../evidence/0158-team-invitations.json) distinguishes local qualification
from deployment and release authority.

## Implemented boundary

- `GET /v1/sites/{site_id}/team` derives the current selected site from the opaque
  session and returns owner-only members and the latest 100 invitations. Pending,
  accepted, expired and revoked are separate states. Truncation is explicit.
- `POST /v1/sites/{site_id}/invitations` requires the existing browser mutation
  proof and a current owner with MFA authenticated within five minutes, not in the
  future, on a verified site. The locked issuance transaction rechecks session,
  recovery generation, owner role, membership/site epochs and verified site.
- Issuance goes through `issue_site_invitation`, preserving normalized ASCII email,
  hash-only persistence, five grantable roles, 24-hour default expiry, 15-minute to
  seven-day lifetime bounds, duplicate-pending exclusion, bounded allocation retries
  and atomic immutable creation audit. Owner is not a grantable invitation role.
- Migration `0096`, following `0095` (merge train 5; written as 0090 after 0089), adds only guarded functions, not tables.
  The existing composed browser gateway uses `signal_identity`; it gets narrowly
  guarded function execution, not invitation or audit table write privileges or a
  new database credential. Existing internal invitation callers are unchanged.
- Settings includes an owner-only Team section. Exact identifiers stay under
  Technical details; viewers receive no team data or invitation controls. Failed
  reads are unavailable, never empty successful teams. Reload team refreshes
  history after an uncertain outcome without retrying issuance or recovering tokens.

## Fresh-browser acceptance

The one-time share link uses a fragment, not a query string. The browser removes
the fragment and posts it to a same-origin guarded route. The server stores it in
a host-only, HttpOnly, Secure, SameSite=Lax cookie for at most ten minutes. Neither
the invitation token nor the email enters OIDC return paths, callback URLs,
local/session storage, rendered server props or invitation history.

The page starts `/v1/invitations/verify` through the existing OIDC/PKCE flow. Sign-in,
registration and email verification remain at the configured identity provider;
the dashboard never collects passwords. The callback relays only the dedicated
invitation identity proof. The acceptance form obtains `/v1/invitations/csrf` and
calls `/v1/invitations/accept`; the existing database function atomically consumes
both proof and invitation. Mismatch, expiry, replay, revocation and wrong scope
remain generic denials and grant nothing; no recipient account lookup is exposed.

Acceptance does not turn an identity proof into a login session. A separate
ordinary OIDC sign-in follows, then current membership selection and exact site
selection, each with existing browser proofs. Only after both succeed does the
person reach Home with the invited site selected. The destination identifiers
are untrusted hints, never authority. An uncertain acceptance/issuance outcome is
not retried automatically or presented as success. Padded names are trimmed;
invalid names receive specific feedback before provider I/O and retain proofs.

## Delivery and revocation

**Owner decision, 2026-10-06:** invitations stay share-the-link only. There is
no invitation email. The one-time link is shown once to the owner; it is neither
emailed nor recoverable from history. This is the chosen product behavior, not
a missing SMTP capability or a planned invitation-email extension.

Slice 0093 is not an invitation transport: its recipient contract requires a
current IdP-verified, opted-in recipient and its closed messages are token-free
reports/alerts. Sending invitation bearer credentials to arbitrary invited
addresses would violate that boundary even if SMTP is configured. This slice
does not widen it. The owner sees the link once, with the explicit sentence
"It was not emailed." It cannot be recovered from storage; loss of the creation
response is unconfirmed and the owner checks history before retrying.

At the 0158 head the foundations had no revocation operation: `revoked_at` was
constrained null and the transition trigger permitted only acceptance. Its
projection therefore said `can_revoke=false`. This historical limitation is
superseded by [0159](0159-invitation-revocation.md), which adds a guarded,
audited pending-only operation, confirmation control and independent recovery
qualification through new migration 0091. The verification below remains the
original 0158 result, not evidence for the later operation.

## Verification

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
npm test
df -h .
.venv/bin/python scripts/run-team-invitation-tests.py
.venv/bin/python scripts/run-database-tests.py --shards 3
.venv/bin/python scripts/keycloak_lab.py
.venv/bin/python scripts/openbao_lab.py
```

Final local results on 2026-10-04:

| Command | Result |
| --- | --- |
| Fast API, identity, tooling and connector pytest | 2,893 passed; two dependency deprecation warnings |
| Ruff check / format check | Passed / 639 files already formatted |
| `npm test` | 46 repository and 294 dashboard tests; typecheck and production build passed; design guards unchanged |
| Focused invitation lab | 127 passed on PostgreSQL 17.11, including 34 new team cases |
| Database lab, three shards | 1,260 passed on PostgreSQL 17.11; cleanup completed |
| Keycloak lab | 5 real-provider checks passed; cleanup completed |
| OpenBao lab | 45 real-provider checks passed; cleanup completed |

Positive checks cover all supported roles, hash-free owner history, atomic
acceptance and resulting site membership, OIDC proof-cookie relay and the
post-acceptance Home transition. Negative checks cover nonowners, primary/stale/
future MFA, unverified sites, other tenants/sites, role escalation, forged/replayed
credentials, different verified email, expired audited invitations, CSRF/Origin
and cookie failures, strict payloads and non-enumeration. Failure checks cover
provider/storage unavailability, malformed responses, ambiguous delivery and
atomic rollback. Local checks do not enable production writes.

The final database run used stable source hashes. An earlier run had obsolete
privilege/fixture failures while sources were changing, plus an unrelated Telegram
install failure; another was interrupted to finish session/identity/user locking.
Neither is qualification evidence. The final complete run passed without weakening
any gate or test; the additional NOWAIT case verifies all three row locks.

Static design detection returned no findings. The design finish review scored the
reload and invalid-name fixes resolved, but its disposition remains `recapture`:
only a native desktop acceptance view was inspected, with no saved viewport
captures. Mobile and owner-Team visual inspection, and hydrated fragment removal,
remain unqualified. Existing DESIGN.md and its sidecar were preserved.

## Explicit limits

Not deployed or customer-enabled. Dedicated live fresh-browser/inbox qualification,
resend and a deployed proof-cleanup schedule are not supplied by this slice.
Invitation email is deliberately excluded by the 2026-10-06 owner decision.
Revocation is separately supplied by 0159, not 0158.
The default unconfigured API remains fail-closed.
No full gate or Temporal process is run. Existing accepted specifications and
migrations remain unchanged; production and publishing authority stay disabled.
