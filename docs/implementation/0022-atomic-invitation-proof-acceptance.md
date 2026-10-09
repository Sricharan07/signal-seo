# Slice 0022: Atomic Invitation Proof Acceptance

Status: **SUPERSEDED FOR BROWSER COMPOSITION BY SLICE 0023; ATOMIC DATABASE CONTRACT REMAINS CURRENT**.

## Outcome

Invitation acceptance no longer trusts a caller-supplied OIDC identity projection.
It accepts a short-lived opaque identity proof and invitation bearer, hashes both,
and consumes them in the same transaction that creates the user, membership,
one-site grant, and chained tenant audit event.

| Boundary | Implemented behavior |
| --- | --- |
| Service | Validates UUID/name/token shapes, hashes both opaque credentials, and returns no proof data |
| Database | Locks one live proof, rechecks the invitation, creates authority/audit, then consumes both atomically |
| Legacy authority | Raw issuer/subject/email acceptance is no longer executable by `signal_identity` |
| Retention | Scheduler-only function deletes deterministic batches of no more than 1,000 expired proofs |

A failed invitation condition leaves the proof unconsumed, so a corrected request
can retry until the original proof expires. A successful request sets the proof and
invitation consumption times to the same transaction timestamp. Replay returns the
same generic denial and creates no duplicate authority or audit event.

## Isolation And Cleanup

Migration `0012` keeps forced RLS on identity proofs. The identity role still has
no direct update or delete privilege. Its only acceptance function requires the
proof hash and obtains issuer, subject, and verified email from the locked row;
the prior raw-projection function is migrator-internal.

The proof trigger allows only one null-to-transaction-time consumption update by
the migrator security-definer path. It permits deletion only after proof expiry.
The scheduler cleanup function uses deterministic expiry/UUID order and
`SKIP LOCKED`, validates batches from 1 through 1,000 in both service and SQL, and
returns only a deletion count. The scheduler cannot enumerate or mutate the table
directly.

Proofs have a ten-minute maximum life and become cleanup-eligible at expiry.
Running and monitoring cleanup is still a deployment requirement; this slice adds
the primitive, not a long-running scheduler deployment.

## Verification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
.venv/bin/python -m ruff check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m ruff format --check apps/api services/control_plane database/migrations scripts tests/api tests/control_plane tests/identity tests/tooling
.venv/bin/python -m pip check
```

Eight net-new PostgreSQL cases bring the real PostgreSQL 17.11 suite to 278.
Reviewed source-hashed evidence is
[0022-postgresql.json](../evidence/0022-postgresql.json); it records completed
cleanup of the disposable lab and no production authority. The unchanged API and
non-database Python suites remain at 106 and 271 cases.

## Explicit Limits

- Slice 0023 adds the invitation-purpose OIDC, proof cookie, CSRF, and acceptance
  HTTP contract, but its gateway is unconfigured and customer-disabled by default.
- Cleanup has no deployed schedule, alert, or retention SLO. Browser proof
  issuance must remain disabled until that operational path is configured.
- The API does not issue a login session after acceptance. A later browser flow
  must define an explicit post-acceptance login/tenant-selection transition.
- Invitation delivery, revocation, throttling, account lock policy, and Keycloak
  user creation remain absent.
- There is no dashboard, Telegram surface, workflow engine, reasoning agent,
  approval flow, undo operation, customer connector, or production write.

The successor [slice 0023](0023-invitation-browser-acceptance.md) adds the
unconfigured browser contract. Delivery, abuse controls, deployed cleanup, and
customer enablement remain absent.

See [ADR-0022](../adr/0022-atomic-invitation-proof-acceptance.md) for transaction,
least-privilege, retry, and retention decisions.
