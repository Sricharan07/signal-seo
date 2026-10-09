# Slice 0016: Site Invitation Issuance

Status: **INVITATION ISSUANCE IS INTERNAL; ACCEPTANCE AND DELIVERY ARE NOT IMPLEMENTED**.

## Outcome

Migration `0007` and `issue_site_invitation` add one bounded account-authority
operation. A current owner or admin can prepare a one-site invitation under the
role ceiling below. The operation returns the bearer token once, stores only its
SHA-256 hash, and atomically appends `invitation.created` to the first narrow
tenant audit stream.

| Inviter | Grantable roles |
| --- | --- |
| Owner | Viewer, analyst, editor, approver, admin |
| Admin | Viewer, analyst, editor, approver |
| All other roles | None |

Owner is never an invitation target. Ownership transfer remains a distinct future
flow with recipient acceptance and current MFA.

## Stored Contract

`app.invitations` stores tenant/site IDs, a restricted lower-case ASCII email
condition, target role, token hash, inviter, authority epochs, and database time.
The TTL is bounded from 15 minutes through seven days. Active duplicates are
serialized by recipient scope and rejected; expired records do not block a new
invitation.

`app.audit_events` currently supports only the first `invitation.created` event.
Its facts contain schema version and role only. It stores the actor, aggregate,
database timestamp, and a deterministic envelope hash; it contains neither email
nor token material. Invitation and audit insertion share one transaction, and
both tables reject ordinary update/delete operations.

## Authority And Concurrency

The application accepts only a server-derived `AuthorizedSite`. PostgreSQL then
rechecks that exact user, role, membership epoch, site-grant epoch, active tenant,
and non-archived site. A narrowly granted function locks tenant, site, membership,
and site grant in deterministic order so demotion or suspension cannot commit
across issuance. A separate advisory lock serializes one normalized recipient in
one site.

The API role gets column-level access only. It cannot read `token_hash`, read the
tenant audit stream, mutate either record, migrate the schema, or enumerate
another tenant/site through forced RLS.

## Tests And Evidence

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

Twenty-six new PostgreSQL cases verify the migration, token/data minimization,
exact privileges, actor and scope integrity, role ceilings, stale authority,
locking, concurrent duplicates, collision bounds, immutable audit evidence,
atomic rollback, and invalid input. All 197 database cases pass in the isolated
PostgreSQL 17.11 lab: [0016-postgresql.json](../evidence/0016-postgresql.json).

## Explicit Limits

- Slice 0017 adds internal verified-email acceptance, user creation, membership
  activation, and guarded token consumption. Revocation remains absent.
- No email delivery, notification outbox, public invitation route, browser form,
  rate limit, or account lock policy exists.
- The pilot contract grants one site only and rejects owner creation.
- Internationalized email addresses are not accepted by this deliberately narrow
  ASCII contract. Slice 0017 composes the same normalizer with provider verification.
- The tenant audit table has no query API, later aggregate events, hash chain,
  signed off-host checkpoint, export, retention/redaction workflow, or dispatcher.
- No production credentials, customer data, or external write authority are enabled.

See [ADR-0016](../adr/0016-authority-locked-site-invitations.md) for authority,
locking, token, role, audit, and privilege decisions.
