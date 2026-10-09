# Slice 0111: Dedicated Test Login Ingress

Classification: **security-critical deployment and identity**. Contract: Revision
4.0 sections 4, 12 and 19, with unchanged Revision 3.2 identity, secrets and
operations requirements. [ADR-0097](../adr/0097-dedicated-test-login-ingress.md)
applies only within the approved dedicated-test exception. No accepted spec changed.

**Implemented and qualified: HTTPS login surface and persistent private application
composition. Human Google/OTP enrollment and a verified owner application session
remain pending. Slack installation, GSC binding and GitHub runtime calls are not
qualified; those capabilities and all external/production writes stay disabled.**

## Implemented Boundary

The [application profile](../../deploy/integration-test/application/compose.yaml)
adds persistent PostgreSQL 17.11, the existing FastAPI OIDC/session pipeline and
Next 16.3.8 to the same approved 4-GB/80-GB, $24/month VM. Migration to `0060` is
an explicit private operator job; the migrator is then `NOLOGIN`. The runtime
`signal_identity` role is not superuser and cannot bypass RLS. TLS uses verified
private CA/hostnames; signing keys stay on the Mac. API and dashboard ports bind
loopback only. All runtime containers are nonroot, readonly, capability-free,
core-dump-disabled, resource bounded and narrowly mounted. No local pilot,
synthetic owner, standing grant, root credential or startup migration is deployed.

The [runtime factory](../../apps/api/src/signal_api/integration_runtime.py) permits
only exact private identity discovery/JWKS/code exchange, preserving the public
issuer while dialing the private verified TLS peer. Unregistered paths, methods,
queries and peers are rejected. Application configuration and recovery generation
are audited, CAS-zero OpenBao generation 1. Three independent AppRoles have only
their exact PKCE/recovery policies and self-renew/revoke/lookup permissions, no
default policy, single-use 30-minute Secret IDs and renewable 300-second tokens.
New requests fail closed on renewal failure; no automatic reauthentication or
root fallback exists. Undeployed worker/provider operations cannot be queued.

Real OpenBao qualification checked all three policies, renewal, replay denial,
token-mint/provider-read/config-write denials, durable PKCE create/read/delete,
recovery read/write denial and revoked-token denial. A limited Mac-only private
operator can read current infrastructure/recovery and mint only these three
workload Secret IDs. Its lease is one hour, maximum four hours. Bootstrap root
revocation was followed by a denied root request. A new independently encrypted
post-retirement snapshot was captured without replacing older pairs. Latest
snapshot and application-database restore rehearsals are **NOT_EXECUTED**.

Keycloak's Google post-broker flow now requires OTP and emits completed-method
`amr` in signed ID tokens. The dedicated application policy requires `otp` plus
an admitted MFA ACR. The actual mapper keys were checked against Keycloak 26.7.3
source and independently read from its database: `default.reference.value=otp`
and `default.reference.maxAge=300`. Admin GET masks those keys, so a masked read
alone is not configuration proof. The private bootstrap operator was disabled;
its access token was denied, PostgreSQL independently showed `enabled=false`,
and a fresh password-grant attempt was denied. Bounded private provider events
are enabled for the later owner/MFA audit. No public application owner is seeded.

## Public And Network Qualification

The owner approved the exact DNS-only A record and free TLS agreement at action
time. Safari showed `signal-dev` pointing to `192.0.2.10`, with the existing root
Worker, parking record, five MX records and SPF untouched. DNS resolution and
public HTTPS returned the actual Signal page with a publicly trusted certificate.
Only IPv4 ports 80/443 were added; the exact-Mac IPv6 SSH rule was preserved.

[Ingress](../../deploy/integration-test/application/Caddyfile) exposes only the
necessary Signal/Google browser endpoints. Real public checks verified private
route 404s, closed database/admin/API/Bao/Temporal ports, exact public OIDC issuer,
S256 login start, null/foreign-Origin rejection, invalid login callback rejection,
and Slack's explicitly unavailable callback result. The absent GSC callback is
404, not a simulated ready endpoint. Safari reached the actual Google chooser,
accepted the owner-performed passkey step and reached Google-verified first-broker
profile setup. Required last-name/OTP steps and subsequent signed owner session
remain human-dependent and unqualified.

The real profile submission exposed two omitted Keycloak broker-completion
routes. Their exact paths are now admitted; unauthenticated requests reach
Keycloak and return 400 instead of ingress 404. Other provider paths stay 404.
The interrupted attempt left one unverified, uncredentialed, unlinked test user;
account collision was unresolved at this slice's checkpoint. The later approved,
guarded cleanup is recorded in [0112](0112-incomplete-test-identity-recovery.md).
No automatic email linking, identity fabrication or MFA bypass was introduced.

Google broker network admission reuses the bounded resolver/public-address
classifier and pins only `accounts.google.com`, `oauth2.googleapis.com` and
`www.googleapis.com` over TLS/443. The bridge's general NEW drops remain. A timer
expires the exact admission after one hour; expiry also denies established
connections. Actual container-network TLS succeeded to all three peers while
unregistered Internet HTTPS and instance metadata timed out. Product connector
traffic has no route through this exception. Reboot does not reopen the pins.

## Failures And Repairs

- The build surfaced a Next advisory; patched 16.3.4 to 16.3.8 before publication.
  `npm audit` reported zero vulnerabilities. See the
  [upstream advisory](https://github.com/advisories/GHSA-vcvr-r3jv-pc5j).
- Legacy Docker ignored per-Dockerfile context files. The private VM build now
  applies explicit allowlists; repository `.dockerignore` remains unchanged.
  AppleDouble/generated runtime duplicates are excluded.
- Inline tmpfs YAML needed quoting; malformed options were corrected, not bypassed.
- Caddy's upstream binary capability prevented capability-free startup. The
  derived image removes that file capability and uses only unprivileged ports;
  runtime capability protections were not weakened. The isolated initialization
  job needed only CHOWN for the two new certificate/config volumes.
- Global `no-referrer` made real HTML form Origin null. Ingress now overrides
  upstream headers after response generation: same-origin on ordinary pages,
  no-referrer on identity/callback URLs. The CSRF gate is unchanged. See the
  [Fetch standard](https://fetch.spec.whatwg.org/#append-a-request-origin-header).
- Qualification initially expected Slack's rejection on the login notice path;
  corrected it to assert the existing exact `/connectors?slack=unavailable` contract.
- Disabling the bootstrap identity user immediately denied its own admin readback.
  The expected 401, independent database flag and fresh credential denial were
  qualified; no authority was re-enabled to obtain a passing read.
- Expired human login actions were restarted with fresh state, not replayed.
  Missing Google profile last name is a human field, not invented by the agent.
- `/broker/after-first-broker-login` and `/broker/after-post-broker-login` were
  omitted from ingress. Added only those exact paths and checked missing-state
  rejection plus unrelated-provider isolation. The resulting incomplete-account
  collision remains explicit; successful owner/MFA login is not claimed.

## Runnable Checks And Limits

[Operator runbook](../runbooks/integration-application.md).
[Nonsecret evidence](../evidence/0111-dedicated-test-login-ingress.json).

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/run-database-tests.py
.venv/bin/ruff check apps/api/src/signal_api/integration_runtime.py scripts/integration_application.py scripts/integration_identity_mfa.py scripts/integration_identity_egress.py scripts/integration_retire_root.py scripts/integration_retire_identity_operator.py scripts/qualify_integration_application.py scripts/qualify_integration_application_secrets.py tests/api/test_integration_runtime.py tests/tooling/test_integration_application.py
.venv/bin/python scripts/qualify_integration_application.py --ca /Users/example-owner/.codex/signal-application-20260930/ca.pem --public
npm test
```

1246 non-database cases, 832 real PostgreSQL 17.11 cases, Ruff and `npm test`
(38 repository cases, 230 Markdown files, 148 dashboard cases, typecheck/build)
passed, alongside private/public qualification. The focused 42 session cases
also passed independently on real PostgreSQL 17.11. Tests
reject absent/incorrect completed MFA methods and accept real-policy OTP claims
only on an existing exact issuer/subject identity. Fixtures do not prove a human
provider journey. Public login exposure does not qualify owner provisioning,
Slack/GSC/GitHub, licensed providers, crawling, workflows or deployment.
