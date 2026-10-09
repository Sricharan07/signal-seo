# Slice 0130: Self-Host Package

Classification: **security-critical** (deployment topology, secrets, exposure,
first-owner identity and recovery). Contract: Revision 4.0 sections 4, 18, 19,
INV-032; Revision 3.2 sections 2, 4, 10, 29, 30, 32, 35.
Migration: **none**, existing linear Alembic head reused unchanged.

## Implemented

- Generic 12-service `deploy/self-host` stack, exact HTTPS-only publication,
  digest-bound upstream/application images, private administration/API/metrics,
  non-root read-only bounded containers, private TLS and Temporal mutual TLS.
- Hidden-input CLI with outside-repository protected config, rootful local Docker,
  checked Linux tmpfs, no secret values in normal/error output, encrypted OpenPGP
  initialization envelope, 2-of-3 unseal and create-only audited OpenBao state.
- Locally generated per-install secrets, existing scoped PKCE/recovery AppRoles,
  private migration jobs and signed OTP/consumed-nonce first-owner transaction.
  It refuses foreign/disabled authority and inconsistent state rather than repair.
  Bootstrap administrator/operator retirement and non-secret acknowledged receipt.
- All nine owner-supplied provider key sets stored through CLI in existing namespaces;
  missing providers are unavailable and configured providers remain disabled.
  No provider reader or shared-egress execution authority is created. Exact configured
  callbacks return an explicit unavailable response; all other callbacks are denied.
- Reused encrypted Raft snapshot capture, explicit restart/image upgrade, documented
  backup/clone restore/upgrade and renewal limits in [self-host.md](../self-host.md).

The qualified integration deployment remains unchanged. Existing dashboard/ingress/
workflow image builds and domain validators are reused. Workload token constructor
defaults preserve integration behavior; only optional directory/policy prefix are
factored. [ADR-0129](../adr/0129-separate-generic-self-host-topology.md) and
[ADR-0130](../adr/0130-quorum-and-proof-bound-self-host-bootstrap.md) record choices.

## Verification

Evidence and complete gate counts: [0130](../evidence/0130-self-host.json).
Local gate: 874 PostgreSQL, 1,684 API/identity/tooling/connector tests, all ten
`run-*-tests.py` labs, real OpenBao/Keycloak, 46 repository and 154 dashboard
checks, Ruff (430 files), dependency and specification checks passed. The staged
secret scan passed; the actual commit-range result is recorded in the PR.
Runnable slice commands:

```sh
.venv/bin/python scripts/run-self-host-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q
node --test tests/repository/self-host.test.mjs
npm test
```

Dedicated real PostgreSQL tests prove first-owner exact retry, no sites/sessions/
grants, wrong subject/client/nonce, stale/no OTP, unconsumed attempts, expired
approval, disabled owner denial, runtime-role denial and atomic rollback after a
late SQL constraint failure. Disposable non-dev pinned OpenBao proves actual PGP
encrypted initialization/decryption/unseal, audit/CAS/policy/AppRole idempotence,
immutable installation denial, owner-key create-only storage, reader/operator/
provider ACL separation, spent one-use secret denial and encrypted snapshot capture.
Tooling/API tests cover actual Compose normalization, unsafe topology, symlinks,
non-tmpfs/non-root targets, TTY denial, suppressed exception/subprocess output,
unknown write/init acknowledgement, encrypted TLS archive/context and missing/
configured unavailable providers. Repository tests cover topology and runbook.
Each lab creates unique disposable resources and cleans only its own labels.

## Not Qualified

Full stack startup, public DNS/TLS, actual first human OTP/owner session, Keycloak
realm import/retirement in this generic topology, Temporal schema/namespace startup,
reboot and image upgrade, generic cross-store restore and every live provider are
**NOT_EXECUTED**. The shared development disk is constrained and the required
Linux rootful target, owner registry digests and DNS/TLS are not supplied.
Exact commands and owner inputs are in [the runbook](../self-host.md#first-run).

Production admission, autonomous/model/provider work, external writes, publishing,
browser sandbox, independent restriction-journal deployment, HA, automatic TLS
rotation/unseal/backups/monitoring and vulnerability certification remain closed.
Local component tests are not a production/self-host release certificate.

## Merge Train 2

Rebased above merged Train 1 (main `7754746`) in the accepted PR order.
No new migration; existing head `0079` is unchanged; 154 cumulative app tables.
Migrations 0001-0070 and the 1200-second aggregate database budget are unchanged.
Fast checks passed: 2234 API/identity/tooling/connectors, 1046 PostgreSQL,
46 repository and 227 dashboard cases; Ruff check and format passed.
The earlier qualification above is historical; final-stack full qualification is
recorded separately in the PR comments. No live or production authority is added.

Restacked above #24 measurement integration fix `381306f`.
The fast counts above qualify this restacked source; the original commits, authors
and messages are preserved. Final-tip full qualification is recorded in the PR comments.

Also includes #24 test-only generation-order correction `9a8a85f`: explicit
fixture timestamps and equal-timestamp UUID tie coverage; production selection unchanged.
