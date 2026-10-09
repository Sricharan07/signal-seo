# Slice 0110: Private Test Identity

Classification: **security-critical deployment foundation**. Contract: Revision
4.0 sections 4, 12 and 19; unchanged Revision 3.2 sections 8, 10.2 and 29.
[ADR-0096](../adr/0096-private-persistent-test-identity.md) applies within the
existing owner-approved test exception. No accepted specification changed.

**Implemented and real-VM-qualified: private persistent identity and its database.
Public callbacks, owner login, MFA, Google consent and application composition
remain NOT_EXECUTED. Production and external writes remain disabled.**

This is the historical 0110 checkpoint. Later HTTPS/application/workload/bootstrap
qualification is recorded in [0111](0111-dedicated-test-login-ingress.md), without
claiming a completed human owner or connector journey.

## Implemented Boundary

The [identity profile](../../deploy/integration-test/identity/compose.yaml) runs
optimized Keycloak 26.7.3 and PostgreSQL 17.11 on the existing approved VM.
Both upstream images are digest pinned. The locally built identity image is
resolved to its immutable image ID and retained in an operator-only `.env`.
Only `127.0.0.1:8280` publishes identity TLS; no database or management port is
published. The database has its own internal network and persistent volume.

PostgreSQL uses SCRAM, a separate nonsuperuser `signal_keycloak` role and TLS.
Keycloak verifies the database certificate hostname and private CA. Both services
are nonroot, readonly, capability-free, core-dump-disabled and memory/CPU/PID
bounded; only narrow readonly configuration, certificate and tmpfs secret mounts
are admitted. Logs are bounded and credentials are absent from Compose/image
configuration and build context. The CA signing key stays on the Mac.

The owner-only [preparation helper](../../scripts/integration_identity.py) creates
distinct infrastructure passwords at `signal-identity/data/platform/callback-stack`
with CAS zero, audited generation 1 and a separate exact-path readonly policy.
It reuses the unchanged reader denial/revocation checks on real persistent
OpenBao. The existing Google sign-in configuration is strictly checked before
export. Protected Mac files contain the exported material; VM password/vault
files are under `/run` tmpfs. Persistent narrow TLS leaves expire after 30 days.
The temporary upload directory was emptied after successful qualification.

The imported realm has no users, public registration, reset-password flow,
wildcard callbacks or direct password grant. It admits the exact dashboard
callback with S256 PKCE. Google's broker uses signature/nonce/PKCE validation,
the exact approved client and email filter, file-vault secret and no upstream
token persistence. Upstream email is not application ownership or a subject
binding. The temporary private master-realm operator remains a bootstrap gate,
not a public user or workload credential; each qualification session was logged out.

The ingress bridge disables masquerading and drops new forwarded/host-bound
traffic using an enabled Docker-associated systemd rule installer. The database
network stays internal. Actual outbound HTTPS was denied. Google broker calls
remain disabled rather than bypassing the unqualified outbound boundary.

## Real Qualification And Failures

The [qualification helper](../../scripts/qualify_integration_identity.py), using
only the protected localhost SSH tunnel and private CA, verified:

- Exact public issuer and endpoints, public-only RS256 JWKS and configured realm.
- Unauthenticated administration denied; wrong redirect rejected with 400;
  missing PKCE returns `invalid_request` only to the exact registered callback.
- Missing broker state and unissued code rejected; dashboard password grant denied.
- Wrong TLS hostname and untrusted CA rejected; no raw errors or tokens printed.
- Exact client, no JavaScript origins, disabled unsafe grants, Google nonce,
  signature, email filter, file vault and zero signal-realm users read back.
- Operator session logout, including failure-safe cleanup after authenticated reads.

Real PostgreSQL showed the application role has no superuser/create-database/
create-role/replication flags and every observed Keycloak connection uses TLS 1.3.
An application-role `CREATE ROLE` inside a transaction failed with permission
denied; a wrong-hostname verify-full connection failed. The realm ID stayed
`00000000-0000-4000-8000-000000000001` across database/identity container replacements.
A no-network identity container without mounted credentials exited 1 before
startup. No public 8280/5432/9000 port was added. The VM reported about 2.6 GiB
available memory after deployment, with no swap or capacity change.

Initial attempts are not passed off as successful: `pull_policy: never` skipped
the missing PostgreSQL image, so the installer now explicitly pulls its pinned
image. Optimized Keycloak ignored runtime build-time path settings and restarted;
the paths/health/metrics options are now baked into the build. Making both
networks internal suppressed loopback publication; the corrected ingress bridge
uses no masquerading and explicit drops. An initial negative startup invocation
used the build config digest instead of Docker's manifest image ID and failed
before running; the repeated test used the actual running image ID and exited 1.
The protocol test was corrected to assert Keycloak's legitimate error redirect,
not incorrectly demand HTTP 400 for missing PKCE.

Older encrypted backup material was preserved together in a new protected
archive, and a fresh post-infrastructure encrypted OpenBao snapshot was captured.
This newest snapshot and identity-database recovery have **not** been separately
restored. Container persistence is not disaster-recovery qualification.

## Runnable Checks And Remaining Gates

[Operator runbook](../runbooks/integration-identity.md).
[Nonsecret evidence](../evidence/0110-private-test-identity.json).

```sh
.venv/bin/python -m pytest tests/tooling/test_integration_identity.py tests/tooling/test_qualify_integration_identity.py tests/tooling/test_integration_connector_secrets.py tests/tooling/test_integration_secrets.py -q
.venv/bin/ruff check scripts/integration_identity.py scripts/qualify_integration_identity.py tests/tooling/test_integration_identity.py tests/tooling/test_qualify_integration_identity.py
.venv/bin/python scripts/qualify_integration_identity.py --directory /Users/example-owner/.codex/signal-identity-20260930
npm test
```

118 focused Python cases and Ruff passed. `npm test` passed 33 repository cases,
227 Markdown files, 148 dashboard cases, dashboard typecheck and build. These
checks do not substitute for public provider journeys.

The hostname still does not resolve. Persistent Signal application database/API,
independent recovery authority and journal, root/bootstrap retirement, workload
authentication, required owner MFA, broker outbound qualification, GSC owner
API/BFF, DNS/public TLS and owner-bound Slack installation remain unqualified.
GitHub's runtime binding/API acceptance and authorized GSC/Slack success are not
established by this slice. No Gmail API or mailbox access is configured.
