# Slice 0107: Private Integration Secret Store

Classification: **security-critical operational foundation**. Contract: Revision
4.0 sections 4, 12 and 19; Revision 3.2 sections 4, 10.2, 29 and 30.

**Implemented and tested: private persistent secret store and model-key storage.
Not implemented here: public application or provider OAuth composition.**

## Scope

The owner approved changing `integration-test-vm` to dual-stack at $24/month instead
of $20/month. Its 2-vCPU/4-GB/80-GB capacity and restricted SSH rule remain.
This supersedes only the initial networking/price observation in [0105](0105-closed-development-base-vm.md).
No domain, public web firewall or unrelated provider resource was changed.

[ADR-0095](../adr/0095-private-persistent-integration-secrets.md) selects the
non-development profile under `deploy/integration-test/secrets/`. Pinned Ubuntu
Docker 29.1.3 and Compose 2.40.3 run OpenBao 2.6.1 at the existing lab's immutable
digest. Named Raft/audit volumes replace disposable storage. The container uses
UID 100, readonly root, dropped capabilities, no new privileges, zero core dumps,
384-MiB memory/no-swap limit, bounded scratch tmpfs and bounded Docker logs.

Only `127.0.0.1:8200` is published. The bridge has no masquerading; a dedicated
`DOCKER-USER` rule denies new forwarded egress and survives reboot. TLS 1.2 or
newer uses a fresh private CA and 30-day server certificate. Declarative audit
uses HMAC redaction and mode 0600; unsafe API audit creation remains disabled.

`scripts/integration_secrets.py` provides operator-only bootstrap, status, unseal,
ACL/seal tests, encrypted snapshot and hidden input. Root/recovery material is
exclusively in a mode-0600 file in a mode-0700 directory outside the repository
on the separate Mac, with FileVault verified enabled. Neither root token nor
unseal shares is on the VM or in runtime configuration. Supplied OpenAI/Jev keys
were imported through non-echoed prompts to existing `signal-model` and
`signal-decision` paths. CAS-zero refuses replacement. Exact read-only reader
tests passed and temporary tokens were revoked. Values are absent from git,
process arguments and command outputs. Neither model provider was called.

## Executed Qualification

- Positive: strict private TLS, real 2-of-3 Raft bootstrap, persistence across
  container recreation and VM reboot, audited reads, both imported model paths,
  container health and independently held encrypted snapshot download.
- Negative: untrusted CA and wrong hostname fail; unauthenticated reads, reader
  writes/deletion/other paths/token creation and revoked tokens are denied.
  Readonly root rejects mutation. IPv4/IPv6 probes find ports 80, 443, 3000,
  5432, 7233, 8080 and 8200 unreachable. AWS/UFW retain only exact single-Mac
  IPv6 SSH. Container egress times out and the scoped deny counter increments.
- Failure/recovery: seal denies reads; reboot stays sealed; owner unseal restores
  access. AES-GCM tampering fails in tests. A real encrypted snapshot restored to
  a different disposable cluster: source seal/root authority worked, clone old
  root was denied and its post-backup canary disappeared. The original remained
  intact and the clone was removed.

Commands/private entry: [runbook](../runbooks/integration-secrets.md).
Evidence: [0107](../evidence/0107-private-integration-secret-store.json).
Checks: 39 Python tests, four Compose contract tests, Ruff and `npm test`.
The restored artifact was the pre-model-key snapshot; the newer post-import
encrypted backup was captured but not separately restored.

## Initial Failures And Repairs

The IPv6-only VM could not reach `ghcr.io`; the approved same-VM dual-stack switch
enabled the pinned pull. The Mac image cache separately rejected overwriting a
multi-platform manifest; no image pin or registry verification was weakened.

Docker 29 retained an internal-network port request without publishing it. The
final bridge uses loopback publication, no masquerade and scoped egress denial.
Forcing IPv6 in SSH prevented Mac IPv4 loopback binding; normal address-family
selection fixed it without broadening server `PermitOpen`.

The first empty-store initialization exceeded five seconds and initialized a
sealed store without returned recovery keys. No business data or provider key
had been stored. Only that newly created empty volume was explicitly discarded;
the helper never retries initialization destructively. Initialization/unseal now
have bounded 90-second operator deadlines; ordinary requests remain five seconds.
API audit creation correctly failed; declarative audit replaced it, not an
unsafe API-creation exception.

Disposable restore initially failed because readonly root lacked scratch space.
Bounded noexec/nosuid/nodev tmpfs fixed it. A post-restore read timed out while
the clone sealed; actual state was reconciled instead of resending restore.
Bounded readiness polling was fixed and a fresh clone completed the full command.
No failure was counted as provider or public-application readiness.

A final `npm test` rerun found duplicate ignored Next.js generated type files
with ` 2.ts` suffixes. Those copies were preserved outside the repository rather
than changing source or relaxing type checks. The complete rerun then passed.

## Limits

This test store is not HA, automatic unseal, a complete installer, production
recovery, workload authentication or an independent authority journal. Revoke
bootstrap root after runtime authentication is provisioned. Audit-file rotation,
automated backups/monitoring, vulnerability qualification and certificate renewal
are unconfigured. Recovery has one independently held encrypted Mac copy, not
multi-operator custody. Chat-exposed keys need rotation; storage does not prove
provider acceptance. No DNS, HTTPS ingress, Google project/client, GitHub App
installation, Slack binding, GSC authorization, public identity or connector
callback was enabled. Production and external-write gates stay closed.
