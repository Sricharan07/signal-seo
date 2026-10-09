# Private Integration Secrets Runbook

This profile is only for the owner-approved environment in
[0106](../implementation/0106-owner-approved-integration-test-scope.md), not
the disposable pilot or production. Never expose OpenBao or use operator root
as a Signal runtime credential.

## Provisioning

Use pinned SSH host verification and an owner-only directory outside the repo
on a separate encrypted Mac. Generate private TLS material:

```sh
.venv/bin/python scripts/integration_secrets.py prepare-tls --directory /absolute/private/operator-directory
```

Pull the pinned image on the VM. Copy the six profile files from
`deploy/integration-test/secrets/`, plus `server.pem`, `server-key.pem` and
`ca.pem`, to the SSH user's mode-0700 `~/signal-secrets-staging/`. Never copy
`ca-key.pem` or `recovery.json` to the VM. Run the copied `install.sh` as the
operator. It installs only `/opt/signal-integration/secrets/` and the scoped
egress service, starts Compose and removes the staging key copy. A repeated
install requires recopying that server key; it never rotates existing recovery
material or deletes persistent volumes.

Open an authenticated SSH forward from Mac `127.0.0.1:18200` to VM
`127.0.0.1:8200` with `ExitOnForwardFailure=yes`, pinned host verification and
normal address-family selection. Helpers require localhost HTTPS with an
explicit port. Initialize only once:

```sh
.venv/bin/python scripts/integration_secrets.py init --directory /absolute/private/operator-directory
.venv/bin/python scripts/integration_secrets.py qualify --directory /absolute/private/operator-directory
```

Initialization has a 90-second operator deadline. A lost response can leave an
initialized store without returned recovery material. Stop, record the unknown
result and establish recovery. Do not automatically retry or delete storage.
Only an independently confirmed empty disposable store may be explicitly rebuilt
by the operator. No helper makes that destructive decision.

## Private Entry

Run in an interactive terminal from this repository:

```sh
.venv/bin/python scripts/integration_secrets.py put-model-keys --directory /absolute/private/operator-directory
```

The two prompts do not echo. Never put keys in arguments, shell history,
environment files, chat, Git or the disposable pilot. Optional `--stdin` accepts
bounded JSON over a separately protected non-echoed channel, not values embedded
in a shell command. Existing model paths use version-one CAS creation and exact
read-only policies. The helper refuses replacement, verifies reads and denied
writes/deletion/other paths, and revokes temporary reader tokens. Plan rotation
as an explicit credential generation compatible with the reader. Storage success
does not qualify provider calls or autonomous authority.

## Restart And Backup

Restarts leave the store sealed; its health check fails closed. Reopen the SSH
tunnel, run `status`, then `unseal` with the same operator directory. Root and
three shares remain only on the Mac; two shares unseal. Losing that directory
loses the currently demonstrated recovery path.

```sh
.venv/bin/python scripts/integration_secrets.py snapshot --directory /absolute/private/operator-directory
```

The helper writes mode-0600 `snapshot.enc` and `snapshot-key.bin` using
AES-256-GCM with authenticated context. The Mac is independent of the VM's
failure domain. Separately protect the encryption key when exporting the archive;
never export it with a publicly reachable object. Neither file is overwritten.
Archive an existing complete pair under a versioned private name before taking
another backup. Missing, corrupted or uncertain backups are not recovery evidence.

Restore testing requires a fresh initialized, unsealed disposable OpenBao 2.6.1
cluster with unique data/audit volumes and an egress-denied private bridge. Use
the same pinned image and narrow readonly TLS/config mounts. Forward its loopback
port `8202` to Mac `18201`. Its private listener may accept the bounded 64-MiB
snapshot body; keep primary configuration unchanged. Retain the bounded scratch
tmpfs. Initialize the clone with a different operator directory before running:

```sh
.venv/bin/python scripts/qualify_integration_secrets_restore.py --source-directory /absolute/private/source-directory --target-directory /absolute/private/clone-directory --confirm-disposable-target
```

The qualifier authenticates the archive, rejects equal IDs/URLs, restores only
to the explicitly confirmed clone, uses source shares, and proves the clone's
old root and post-backup canary no longer work. A timeout is an unknown outcome:
reconcile the clone before repeating restore. Remove only the disposable clone
and its volumes after confirming the original remains intact. This is backup
mechanics, not permission to restore live authority or resurrect revoked keys.
Full cross-store recovery still needs Revision 3.2 section 29.4 and independent
journal qualification.

## Checks And Limits

```sh
.venv/bin/python -m pytest tests/tooling/test_integration_secrets.py -q
.venv/bin/python -m ruff check scripts/integration_secrets.py scripts/qualify_integration_secrets_restore.py tests/tooling/test_integration_secrets.py
node --test tests/repository/integration-secrets.test.mjs
npm test
```

Inspect actual Docker bindings, health, uid/capabilities/mounts, TLS failures,
AWS/UFW SSH restrictions and `DOCKER-USER`. Reboot before adding ingress.
Private administration must not depend only on UFW because Docker uses its own
publication rules. This profile has no public routes. Root revocation, workload
authentication, audit rotation, automated backups/renewal/monitoring and quorum
remain unqualified; see [0107](../implementation/0107-private-integration-secret-store.md).
