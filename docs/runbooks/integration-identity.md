# Private Integration Identity

This operator-only test profile is not the disposable pilot or a production
installer. See [0110](../implementation/0110-private-test-identity.md). Keep every
public-admission gate closed until the actual application/provider journey is
qualified. Never publish master-realm administration, management or the database.

## Prepare And Deploy

Use the existing private OpenBao SSH tunnel and independently held authority
directory from [the secret-store runbook](integration-secrets.md). Export to a
separate absolute owner-only directory outside the repository:

```sh
.venv/bin/python scripts/integration_identity.py prepare --authority-directory /Users/example-owner/.codex/signal-dev-secrets-20260930 --directory /Users/example-owner/.codex/signal-identity-20260930
```

Preparation is create-only. Existing files/policies, unknown generations, invalid
Google credentials or CA and unknown external outcomes fail closed. Do not retry
an uncertain store write. After inspecting the exact real store generation and
policy, `render` exports existing generation-1 configuration to a new empty
protected directory without replacing provider or infrastructure credentials.

Transfer only the reviewed identity profile and nine generated files over the
existing approved-source SSH connection with pinned host verification. Preserve
mode 0600 in a mode-0700 temporary credential directory. Never transfer `ca-key.pem`,
OpenBao root/unseal material or other providers' credentials. On this test VM:

```sh
sudo sh /home/ubuntu/signal-identity-deploy-20260930/install.sh /home/ubuntu/signal-identity-bootstrap-20260930
```

The installer demands tmpfs `/run`, small nonsymlink single-link private files,
installs narrowly readable secrets/certificates and the egress rules, explicitly
pulls pinned PostgreSQL, builds optimized Keycloak and binds its image ID.
It does not publish HTTPS, establish MFA, provision a Signal owner or authorize
Google/Slack. Successful startup alone is not qualification. Remove only the
nine explicit temporary upload files after successful readback; preserve Mac
recovery material and persistent database/TLS files.

## Qualify And Diagnose

Forward Mac loopback port 18220 to VM loopback port 8280 using the same approved
source address and strict pinned host key. Then run:

```sh
.venv/bin/python scripts/qualify_integration_identity.py --directory /Users/example-owner/.codex/signal-identity-20260930
```

The helper prints fixed PASS labels and a nonsecret realm ID, never operator
tokens, provider secrets or raw failures. It logs out its temporary operator
session even if authenticated configuration checks fail. Wrong callback, missing
PKCE/state, unknown code/password grant, untrusted CA and wrong hostname must
remain denied. Email allowlisting does not replace exact verified subject
provisioning or MFA.

On the VM, inspect only selected container/network fields, listening ports and
database role/TLS metadata. Do not dump container environments, secret files,
full provider responses or realm export credentials. Retain the realm ID across
container replacement; never run `down --volumes`. An isolated no-network
container without runtime material must fail before startup. The identity bridge
must deny new outbound connections and retain no public management ports.

## Restart And Recovery

Container replacement retains PostgreSQL state and live tmpfs material. Host
reboot loses runtime passwords/vault files and leaves identity unavailable until
the operator deliberately rehydrates approved material. OpenBao independently
restarts sealed and requires its existing unseal procedure. No automatic startup
or production availability guarantee is provided.

Renew 30-day leaf certificates before expiry using a new protected `render`
directory and deliberate redeployment; do not disable TLS validation to recover.
Capture an independently encrypted OpenBao snapshot after credential changes,
preserving the preceding key/ciphertext pair together. The post-identity snapshot
and identity-database backup/restore remain unqualified. Do not restore the live
store/database or claim that persistence proves disaster recovery.

Retire temporary Keycloak/OpenBao bootstrap authority and provision separately
scoped workload authentication before public admission. Recovery generation,
restriction journal, required MFA, safe Google outbound access and persistent
Signal application composition remain required; opening a DNS record is not a
substitute for these controls.
