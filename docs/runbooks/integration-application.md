# Dedicated Integration Login

This is the exact owner-approved test deployment, not a public pilot or production
installer. See [0111](../implementation/0111-dedicated-test-login-ingress.md).
Do not enable providers or claim an owner session from a callback HTTP response.

## Private Preparation

Use the approved-source, pinned-host SSH transport and existing private OpenBao
tunnel. Application preparation is create-only; uncertain writes require inspection,
not blind retry. Export only to a separate empty mode-0700 Mac directory:

```sh
.venv/bin/python scripts/integration_application.py prepare --authority-directory /Users/example-owner/.codex/signal-dev-secrets-20260930 --directory /Users/example-owner/.codex/signal-application-20260930
```

Do not rerun `prepare` on this deployment. `render` uses the scoped Mac operator
after root retirement and creates fresh single-use workload IDs. Transfer only
reviewed source, narrow leaves and explicit protected application files. Never
transfer root/unseal material, CA signing keys or unrelated provider keys. Use the
application installer, explicit image build/binding, and separate migration job.
Revoke migrator LOGIN after migration; delete only invocation-owned uploads and
tmpfs migration material. Keep persistent volumes and encrypted Mac backups.

## Qualify And Retire Bootstrap

The three workload roles must pass positive, denied, replay, renewal and revocation
checks against real audited TLS OpenBao. Do this before root retirement:

```sh
.venv/bin/python scripts/qualify_integration_application_secrets.py --directory /Users/example-owner/.codex/signal-dev-secrets-20260930
```

Configure the required Google OTP flow before disabling identity administration.
Confirm actual mapper keys by a readonly metadata query, never credential columns.
The helpers are create-only, not idempotent recovery workflows:

```sh
.venv/bin/python scripts/integration_identity_mfa.py --directory /Users/example-owner/.codex/signal-identity-20260930
.venv/bin/python scripts/integration_retire_root.py --directory /Users/example-owner/.codex/signal-dev-secrets-20260930
.venv/bin/python scripts/integration_retire_identity_operator.py --directory /Users/example-owner/.codex/signal-identity-20260930
```

These operations have already been applied. Retired credentials must stay denied.
The identity helper expects its access token to fail immediately after disabling
its user; independently check the database `enabled` flag. Do not re-enable the
account for readback. Keep restoration/quorum procedures private and deliberate.

Forward Mac loopback 18380/18480 to VM loopback 8380/8480. Validate private TLS,
PKCE, secure binding cookies and same-origin web relay before public ingress.
Then run the exact public checks, without following authentication redirects:

```sh
.venv/bin/python scripts/qualify_integration_application.py --ca /Users/example-owner/.codex/signal-application-20260930/ca.pem --public
```

## Expiring Identity Admission

`integration_identity_egress.py` prepares a new empty protected pin directory for
only the four fixed Google identity peers: accounts, token, JWKS and the
`openidconnect.googleapis.com` user-info endpoint. The one-hour timer must be installed
and armed before enabling exact allow/NAT rules. Recreate identity with the pin
overlay, verify TLS and denied Internet/metadata, and keep general bridge drops.
Never reuse expired pins, permit redirects or open broad product-provider access.
At expiry, brokering fails closed until deliberate resolver/pin requalification.
The API's database/recovery readiness is not a Google availability claim.

The renewed test pin set expires at **2026-10-01 03:34:51 UTC**. Workload renewals are
independent of this deadline. Use the owner-local timezone when discussing time.

## Human And Recovery Gates

The owner must complete Google consent, missing profile fields and OTP enrollment
in Safari. Never collect QR seeds, passwords or OTPs in chat or operator scripts.
Then qualify the actual signed `amr`/ACR, exact issuer/subject, owner provisioning
and application session. Do not manufacture claims, provision from email alone,
skip MFA or bootstrap synthetic public memberships.

The incomplete first-broker record was removed only after explicit human approval,
protected authenticated backup, real PostgreSQL delete/restore rollback and changed
precondition rejection; see [0112](../implementation/0112-incomplete-test-identity-recovery.md).
Do not rerun its one-shot cleanup against a newly credentialed account. Its recovery
serialization rehearsal uses only temporary table clones and always rolls back.
Later Google linkage and OTP enrollment are confirmed, but an owner application
session is not. Provisioning must await fresh signed identity/MFA proof and the
specific human workspace authorization.

Slack needs an owner-bound site/session and the existing shared-egress gateway;
GSC still needs its owner HTTP/BFF and exact property confirmation; GitHub needs
real one-repository API acceptance through its qualified connector. No Gmail API
or mailbox access exists. None of these is ready merely because DNS/TLS is ready.

Host reboot loses tmpfs secrets and requires private unseal and deliberate
rehydration with fresh single-use IDs. Operator credentials expire after their
bounded lease; future privileged recovery requires the owner-held quorum/private
bootstrap procedure. Private TLS leaves expire after 30 days. Latest encrypted
snapshot and application/identity database disaster recovery are not qualified.
No automated recovery, standing authorization or production release is advertised.
