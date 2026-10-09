# Self-Host Signal

**NOT_CERTIFIED: operator-managed deployment candidate, not a production release.**
This package implements the open-source installation boundary in Revision 4.0
section 18 and INV-032. No Signal-operated service, hosted license check, managed
identity, model proxy or telemetry endpoint is required. The owner supplies the
host, DNS, TLS, registry images and provider credentials. The existing dashboard
and identity foundations run locally; production commands, crawling, model work,
connector exchanges, autonomous work and publishing remain visibly unavailable.
Credential storage is not capability admission. See [0130](implementation/0130-self-host.md).

## Requirements And Topology

- A dedicated Linux host with a local rootful Docker Engine and Compose v2;
  Docker Desktop, remote Docker contexts and rootless volume hydration are rejected.
  Budget at least 8 GiB RAM and 40 GiB free persistent disk, plus separate backups.
  These are planning requirements, not measured sizing or availability promises.
- Python 3.12 with the repository's locked `requirements.txt`, an interactive
  non-echoed terminal, and administrative access to that host. No new Python
  dependency is introduced. GPG is required for recovery custodians and the lab.
- A DNS name, an externally obtained public certificate/key covering that exact
  name, and three independently held binary OpenPGP public encryption keys plus
  a separate bootstrap-root recipient. Supply no customer data for qualification.
- A root-owned 0700 runtime directory on **tmpfs**, with host swap disabled or
  encrypted, core dumps disabled, and protected SSH restricted to the operator.
  Runtime material cannot be inside any Git repository or behind symlinks.

`deploy/self-host/compose.yaml` publishes only TCP 443 from Caddy. There is no
port 80 or automatic ACME dependency; renew public TLS externally, preferably by
DNS challenge. Dashboard, exact local identity authorization/logout/required-action
paths and static resources are public through HTTPS. Identity administration,
token exchange, account console, API, metrics, OpenBao, PostgreSQL and Temporal
have no published ports. Database/secrets/workflow networks are internal. All
12 containers use non-root users, read-only roots, dropped capabilities, no-new-
privileges, bounded resources, and narrow read-only configuration mounts. Private
peers use TLS with exact hostname validation; Temporal requires mutual TLS using
its [service configuration](https://docs.temporal.io/references/service-configuration).

Provider callbacks are emitted only for configured Google GSC, Slack, GitHub and
Telegram paths. These exact paths return 503 until runtime qualification;
unconfigured callbacks and every other webhook return 404. No wildcard identity
broker or public administration is enabled. Provider egress is not composed, so
no provider traffic bypasses the shared-egress boundary.

## Images And Private Configuration

Build from the reviewed release on a separate builder and push to an owner-owned
registry. Build/pull dependencies need Internet access on that builder/host; no
Signal service is needed. These are owner commands, not executed qualification:

```sh
docker build -f deploy/self-host/Dockerfile.api -t registry.example.invalid/signal-api:reviewed .
docker build -f deploy/self-host/Dockerfile.identity -t registry.example.invalid/signal-identity:reviewed deploy/self-host
docker build -f deploy/integration-test/application/Dockerfile.dashboard -t registry.example.invalid/signal-dashboard:reviewed .
docker build -f deploy/integration-test/application/Dockerfile.ingress -t registry.example.invalid/signal-ingress:reviewed deploy/integration-test/application
docker build -f deploy/workflow-consumer/Dockerfile -t registry.example.invalid/signal-workflow:reviewed .
```

Push each image and independently inspect its registry digest with
`docker buildx imagetools inspect`. Copy `deploy/self-host/config.example.json`
to `/etc/signal-self-host/config.json` outside the repository, mode 0600 owned
by root. Replace documented placeholders with the exact owner-selected origin,
local username, workspace, region and five **registry SHA-256 digest references**.
The example deliberately has invalid digest placeholders; tags alone are refused.
The remaining upstream image digests are fixed in Compose/Dockerfiles. Select
the dedicated `signal-self-host` project namespace, never another track's project.
Do not put secrets in this JSON, `.env`, Compose overrides, arguments or Git.

The dashboard/ingress/workflow images reuse their existing Dockerfiles unchanged.
This separate topology does not inherit the dedicated test environment's scope,
owner, provider configuration or qualification. Only workload-token directory/
policy prefix are parameterized; integration defaults remain unchanged.

## First Run

The following examples assume root runs the repository's installed virtualenv.
All paths are placeholders, not an installation identity. Create mode-0700
`/run/signal-self-host` on tmpfs and `/var/lib/signal-self-host-recovery` on an
encrypted filesystem outside Git. Export recovery archives off-host immediately;
keeping them only on the installation host is not disaster recovery.

```sh
sudo mount -t tmpfs -o size=256m,mode=0700,nosuid,nodev,noexec tmpfs /run/signal-self-host
sudo .venv/bin/python scripts/self_host.py prepare --config /etc/signal-self-host/config.json --runtime-directory /run/signal-self-host --recovery-directory /var/lib/signal-self-host-recovery
sudo .venv/bin/python scripts/self_host.py compose --config /etc/signal-self-host/config.json --runtime-directory /run/signal-self-host --recovery-directory /var/lib/signal-self-host-recovery up -d openbao
```

`prepare` asks for an independently held TLS recovery passphrase (20+ characters).
It keeps only an authenticated encrypted TLS archive (context `signal-self-host-tls-v1`)
and public CA on persistent
disk; service keys are in checked tmpfs and subsequently in OpenBao. It refuses
unexpected existing files/permissions, wrong passphrases and expiring leaves.
Install a reviewed host mount unit before relying on reboot recovery. Do not use
the Docker socket inside containers or broad host mounts.

Open private operator forwards on loopback **on the same Linux host as the CLI**.
The host can reach the private bridge addresses without publishing container
ports. Inspect only this project's OpenBao/identity container addresses. Use
pinned SSH host verification and `ExitOnForwardFailure=yes` with an authenticated
operator SSH session, for example `-L 127.0.0.1:18200:OPENBAO_PRIVATE_IP:8200` and,
after identity starts, `-L 127.0.0.1:18443:IDENTITY_PRIVATE_IP:8443`. Restrict
`PermitOpen` to these exact peers, never forward to a public listener, and close
forwards after use. Helpers require `https://localhost` with an explicit port
and the private CA. A bridge address can change when a container is recreated.

```sh
sudo .venv/bin/python scripts/self_host.py init --config /etc/signal-self-host/config.json --runtime-directory /run/signal-self-host --recovery-directory /var/lib/signal-self-host-recovery --pgp-key /private/custodian-one.pgp --pgp-key /private/custodian-two.pgp --pgp-key /private/custodian-three.pgp --root-pgp-key /private/bootstrap-root.pgp
sudo .venv/bin/python scripts/self_host.py unseal --config /etc/signal-self-host/config.json --runtime-directory /run/signal-self-host --recovery-directory /var/lib/signal-self-host-recovery
sudo .venv/bin/python scripts/self_host.py bootstrap --human-approved --config /etc/signal-self-host/config.json --runtime-directory /run/signal-self-host --recovery-directory /var/lib/signal-self-host-recovery --public-cert /private/public-chain.pem --public-key /private/public-key.pem
```

Public recipient files must be binary exports, owner-only regular files. OpenBao
encrypts each returned share/root to its supplied recipient before the response;
the CLI never receives or persists plaintext initialization shares/root. The
encrypted `openbao-init.pgp.json` and non-secret intent are mode 0600. This is
**Shamir unseal**, three shares with threshold two, not automatic-unseal recovery
shares. Distribute each encrypted share to its own independent custodian, keep
private PGP keys/passphrases separately off-host, and rehearse recovery privately.
The root recipient decrypts only for the hidden operator prompt. Two custodians
provide their shares through hidden unseal prompts, never shell history or chat.
See the [OpenBao initialization API](https://openbao.org/docs/api/system/init/).

Bootstrap generates database/application/workload secrets locally into audited
OpenBao, uses CAS creation and exact policies, hydrates tmpfs, runs the linear
Alembic chain, creates Temporal schemas/namespace and starts the private services.
The owner chooses a hidden initial password (20+ characters). A 60-minute human
authorization is recorded, not a standing grant. Keycloak imports exactly one
local subject and requires password replacement and OTP enrollment. **The human
owner**, not a browser agent, signs in through the dashboard and completes these
actions. The first callback cannot issue an application session before the owner
exists. After enrollment, start a fresh login and actually enter OTP; enrollment
alone is not a signed OTP authentication proof. A qualifying consumed proof is
captured privately with five-minute validity and periodic cleanup. The callback
can still report an unavailable login until the application owner is committed.
Run completion promptly, then sign in again normally:

```sh
sudo .venv/bin/python scripts/self_host.py complete-owner --human-approved --config /etc/signal-self-host/config.json --runtime-directory /run/signal-self-host --recovery-directory /var/lib/signal-self-host-recovery
```

Completion revalidates the signed subject/client/issuer/OTP/auth-time and consumed
durable nonce, then table-locks an empty database and atomically creates only one
owner/workspace. It grants no site, standing authorization, external write or
provider authority. An exact committed retry does not create new rows; existing
foreign, disabled or inconsistent owners are never repaired. It disables the
private Keycloak bootstrap administrator, proves token/credential denial, removes
capture/admin files, stops API/dashboard, revokes the supplied OpenBao operator
token and writes a non-secret retirement receipt only after acknowledgement.

Obtain a fresh short-lived operator under the owner's quorum ceremony (OpenBao
generate-root, then a restricted temporary operator token and immediate root
revocation). Run `rehydrate --restart-approved` with the same arguments and
public TLS pair, then close the forwards and revoke the temporary operator.

```sh
sudo .venv/bin/python scripts/self_host.py rehydrate --restart-approved --config /etc/signal-self-host/config.json --runtime-directory /run/signal-self-host --recovery-directory /var/lib/signal-self-host-recovery --public-cert /private/public-chain.pem --public-key /private/public-key.pem
```

The CLI's printed steps never include a credential. It cannot independently mint
root or obtain the custodians' authority. For an expired first-owner window use
`arm-owner --human-approved --restart-approved`, then repeat the human login.

Acknowledgement loss is **unknown**, not success: preserve intent, encrypted
archives, volume identity and audit records; stop and reconcile privately.
Never delete volumes, reset realm import, repair foreign owners, or repeat an
initialization that has no acknowledged encrypted bundle. Non-secret receipt
loss after retirement needs operator reconciliation, not automatic account repair.

## Owner-Supplied Providers

Use `provider --provider NAME` with the same config/runtime/recovery arguments.
Each value and operator token is prompted on a hidden TTY. Supported names:
`openai`, `jev`, `dataforseo`, `google`, `github`, `slack`, `telegram`, `smtp`, `bing`.
GitHub's private-key prompt expects base64 of the PEM, never a file inside Git.
The exact existing OpenBao namespaces are reused; provider credential writes are
create-only and conflicting rotations are refused. No environment fallback or
Signal-operated key is used. Google here is the optional **GSC OAuth client**;
first-owner login is local Keycloak and does not require Google or SMTP.

Run `status` for all nine configured/not-configured projections. Every missing
provider is explicitly unavailable, including optional DataForSEO. Configured
providers remain disabled until their runtime/egress/authority is qualified;
the dashboard's capability projection agrees. Jev absence must never imply a
model may grant autonomy; any later admitted fallback must be labelled ask-owner.
Rehydrate after storage changes to refresh the safe projection/exact callback
allowlist. No provider key is mounted in API/dashboard/worker containers here.
Revoke the temporary operator after importing or checking configuration.

## Backups And Restore

Use the existing [secret-store backup/recovery procedure](runbooks/integration-secrets.md),
[identity recovery limits](runbooks/integration-identity.md), and
[workflow rollout/recovery rules](runbooks/workflow-consumer-deployment.md).
The generic helper reuses `integration_secrets.snapshot` with an explicitly hidden
operator token; it does **not** require that runbook's plaintext legacy root file:

```sh
sudo .venv/bin/python scripts/self_host.py backup --config /etc/signal-self-host/config.json --runtime-directory /run/signal-self-host --recovery-directory /var/lib/signal-self-host-recovery --backup-directory /private/versioned-self-host-backup
```

The bounded Raft snapshot is AES-GCM encrypted with the established authenticated
context. It refuses an existing archive/key pair. Export `snapshot.enc` and its
mode-0600 `snapshot-key.bin` **separately** to independent encrypted custody;
never keep the encryption key only beside its archive. Archive the PGP envelope,
public CA, encrypted TLS archive, independently held passphrase/PGP keys, reviewed
image digests, non-secret config and retirement receipt. Preserve audit volumes
and rotate them under a reviewed retention plan. A snapshot is not a complete
application/identity/Temporal backup.

Quiesce/drain workers and ingress, take consistent encrypted PostgreSQL backups
of all three databases and roles, and capture a matching OpenBao snapshot under
the same recorded checkpoint. Use private local sockets/password input, not
credential-bearing arguments or query logs. Protect dumps as secrets and record
restore compatibility with the exact migrations and Temporal schema versions.
Named volumes survive container recreation; they are not backups. Never copy a
live PostgreSQL data directory as a purported consistent backup.

Rehearse in a **distinct, egress-denied disposable clone**, never against the
primary: authenticate/decrypt snapshots with the existing
`decrypt_snapshot`/`distinct_clusters` helpers, restore through the private
Raft snapshot endpoint, unseal using source custodians, and prove clone-old
tokens/post-checkpoint canaries are denied. Restore PostgreSQL/identity/Temporal
to matching versions, hydrate new volatile credentials, verify roles/RLS/TLS and
owner identity, and discard only the confirmed clone. The integration qualifier
expects legacy plaintext recovery files; do not fabricate those for this package.
A generic cross-store restore driver is not implemented; use a reviewed private
operator ceremony with these existing primitives. Snapshot restore can resurrect
old keys/authority: before any production admission, Revision 3.2 section 29.4
requires an independent restriction journal/recovery anchor and fail-closed
session invalidation. This topology does not qualify that independent deployment.
Cross-store restore, backup scheduling, monitoring and audit rotation are
NOT_EXECUTED/NOT_CERTIFIED, not silently automated.

## Restart And Upgrades

On reboot the store remains sealed and tmpfs credentials disappear. Run `prepare`
with the existing encrypted TLS archive/passphrase, start only OpenBao, reopen
private forwards, `unseal`, obtain a temporary owner-approved operator, then
`rehydrate --restart-approved` with the same public TLS pair. Rehydration stops
API/dashboard/worker before replacing one-use workload credentials; expired or
spent workload credentials do not become a fallback root token.

Before `upgrade --restart-approved`, capture and rehearse the matching backups,
drain workflow leases per the existing rollout runbook, review migration/worker
compatibility, and select five newly reviewed immutable registry digests in the
external JSON. The CLI rejects installation-identity changes, runs `alembic
upgrade head` through the existing migrator role and versioned Temporal schema
tools, and records selected image digests only after startup acknowledgements.

```sh
sudo .venv/bin/python scripts/self_host.py upgrade --restart-approved --config /etc/signal-self-host/config.json --runtime-directory /run/signal-self-host --recovery-directory /var/lib/signal-self-host-recovery --public-cert /private/public-chain.pem --public-key /private/public-key.pem
```

No migration is introduced by slice 0130. Unknown migration/deployment outcomes
must be reconciled; never downgrade Alembic or schema as an image rollback shortcut.
Rollback only to a reviewed compatible image with restored/verified authority.
Private leaves last 30 days: automatic renewal/rotation is **not implemented**;
the CLI refuses an invalid/expiring archive. Plan deliberate, quiesced rotation
before expiry. Do not remove hostname validation to keep an installation running.

## Qualification And Limits

```sh
.venv/bin/python scripts/run-self-host-tests.py
.venv/bin/python -m pytest tests/tooling/test_self_host.py tests/api/test_self_host_runtime.py -q
node --test tests/repository/self-host.test.mjs
npm test
```

Full `docker compose up`/first human owner journey is **NOT_EXECUTED** locally:
shared disk is limited, this development host is not the required Linux rootful
target, and owner DNS/TLS/private registry digests are not supplied. The exact
owner sequence above (`prepare`, `compose ... up -d openbao`, `init`, `unseal`,
`bootstrap`, human password/OTP, `complete-owner`, `rehydrate`) is the first-live-
run checklist, not evidence. Qualify actual IPv4/IPv6 bindings, TLS failures,
host firewall/DOCKER-USER policy, private administrator/token/metrics paths,
reboot/unseal, owner session, storage and schema upgrades on that dedicated host.

No provider calls, paid managed service, autonomous model work, external writes,
publication, browser sandbox, independent-journal deployment, multi-host HA,
complete restore, public TLS renewal or vulnerability certification are claimed.
These gates stay visibly closed. Never expose private ports to make a failed
probe pass or transfer the integration environment's qualification to this stack.
