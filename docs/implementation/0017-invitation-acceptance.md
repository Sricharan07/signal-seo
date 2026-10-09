# Slice 0017: Verified Invitation Acceptance

Status: **INTERNAL ACCEPTANCE IS IMPLEMENTED; NO PUBLIC INVITATION ROUTE OR DELIVERY EXISTS**.

## Outcome

Migration `0008` and `accept_site_invitation` add the only path that can turn a
site invitation into authority. The caller must present both a valid opaque
invitation token and a fresh signed OIDC identity whose verified normalized email
matches the invitation. Successful acceptance atomically creates or reuses the
exact provider identity, creates one active organization membership and one site
grant, consumes the invitation, and appends `invitation.accepted`.

Ordinary login remains lookup-only. An unknown identity without a valid
invitation still cannot create a user, membership, site grant, or session.

## Identity Contract

Authorization requests now ask Keycloak for exactly `openid email`. ID-token
validation accepts `email_verified` only as a JSON boolean. A true value requires
a bounded restricted ASCII mailbox, normalized by the same code used for
invitation issuance. Unverified email produces no identity condition and the
verified email is excluded from dataclass representations.

User identity remains `(issuer, subject)`. Matching email from another issuer
creates a separate user. An enabled exact identity is reused without silently
overwriting its existing display name or contact email. Disabled users and users
who already have any membership in the target tenant cannot consume an
invitation.

## Storage And Locks

`control.invitation_routes` stores only invitation, tenant, site, and creation
identifiers. It has no runtime table grants. A security-definer insertion trigger
keeps it atomic with `app.invitations`; migration `0008` backfills any preexisting
rows inside one owner-only migration transaction.

The acceptance function discovers scope from that route and then sets
transaction-local RLS context. It locks the tenant, site, and invitation in that
order, then serializes the exact provider identity before changing account data.
The transaction checks active tenant/site state, token hash, verified email,
expiry, consumption, user state, and existing membership at action time.

`app.invitations.accepted_user_id` and `consumed_at` may change together exactly
once through the guarded migrator-owned function. All other invitation changes
remain rejected. Sequence two of `app.audit_events` records the accepted user,
role, database time, creation-event hash, and deterministic event hash without
email or bearer material.

## Tests And Evidence

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/keycloak_lab.py
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

Twenty new PostgreSQL cases bring the real isolated database suite to 217 passing
cases. Four protocol cases bring the non-database Python suite to 201 passing
cases. The real disposable Keycloak lab passes five signed authorization-code,
PKCE, replay, nonce, and verified-email checks. See
[0017-postgresql.json](../evidence/0017-postgresql.json) and
[0017-keycloak.json](../evidence/0017-keycloak.json).

## Explicit Limits

- This slice has no invitation browser proof or HTTP route. Slice 0021 later adds
  internal proof issuance, and slice 0022 atomically consumes it with the
  invitation while revoking direct runtime use of this slice's raw-identity
  function. Cookie/CSRF composition, delivery, notifications, abuse throttling,
  and account lock policy remain absent.
- Signal does not create, verify, or recover a Keycloak account. This slice only
  consumes a provider assertion after its external account flow succeeds.
- Invitation revocation and expiry cleanup are not implemented. Expired rows are
  denied and retained as evidence.
- Existing tenant membership is not upgraded or repaired through an invitation.
  Role changes require a separate current-authority operation.
- The site permission set remains the pilot's single snapshot-request grant.
- No owner invitation or ownership transfer is possible. Ownership transfer still
  requires explicit acceptance, current MFA, final-owner protection, and audit.
- The identity service credential is a production trust boundary and no
  production credential or public entry point is configured.

See [ADR-0017](../adr/0017-verified-invitation-acceptance.md) for the routing,
identity, locking, transaction, and privilege decisions.
