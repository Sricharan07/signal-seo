# ADR-0097: Dedicated Test Login Ingress

Status: Accepted 2026-09-30 for the exact owner-approved test deployment.
Supersedes only the public-admission checkpoint of
[ADR-0096](0096-private-persistent-test-identity.md), not its isolation or identity rules.

## Context

The owner approved the dedicated DNS-only hostname, free Let's Encrypt TLS and
necessary HTTPS callback exposure. Registered providers are not working runtime
integrations. The local pilot's synthetic identities and root-token lifecycle
remain unsuitable for public deployment.

## Decision

Compose the existing OIDC/PKCE/session pipeline with a separate persistent TLS
Signal database and a production Next server. Use three independent, exact-policy
OpenBao AppRoles: create-only PKCE writer, read/delete PKCE consumer and readonly
recovery-generation reader. Secret IDs are single-use; periodic workload tokens
renew in memory. Renewal failure disables new requests, without a root fallback.
Revoke the bootstrap OpenBao root and disable the private Keycloak bootstrap user.

Only the approved hostname publishes HTTP for ACME and HTTPS through nonroot,
capability-free Caddy. Administration, master realm, token exchange, API, database,
metrics and OpenBao stay private. Caddy verifies private upstream certificates.
Disable ingress/access query logging. Credential-bearing URLs suppress referrers;
other dashboard pages use same-origin referrers so HTML form Origin checks work.
Do not admit null/foreign Origin to repair a browser failure.

Google identity brokering has a separate trust-plane admission: exactly three
fixed provider hostnames, screened and pinned public IPv4 peers, bounded HTTP
timeouts/pools, no redirect/retry, exact-destination NAT and an enforced one-hour
expiry. Expiry blocks even established traffic. This is not a tenant provider
gateway: Slack/GSC/GitHub, models, crawlers and workers remain disabled. They must
use the existing shared-egress and current owner/workflow authority contracts.

Google post-login requires OTP. The signed completed-authenticator `amr` claim
must include `otp`, in addition to an admitted ACR, before application session
issuance. An email filter, enrolled credential, public callback or ACR alone
does not provision ownership or simulate MFA. Human enrollment and actual signed
owner session qualification remain separate gates.

## Alternatives And Consequences

- Public local pilot or root runtime: rejected.
- Broad outbound access or public identity administration: rejected.
- Fake callback success or unqualified provider enablement: rejected.
- Disable CSRF checks when a form fails: rejected; fix the response policy.
- Increase the VM or recurring cost: unnecessary; the same approved base is used.

This is an operator-managed test login surface, not a production installer or
qualified full connector stack. Host reboot loses tmpfs material and requires
deliberate unseal/rehydration. Pins and private TLS leaves expire. Scoped operator
credentials also expire; no automatic recovery or production availability is claimed.

## Verification

[0111](../implementation/0111-dedicated-test-login-ingress.md) records real TLS,
PKCE, private-port/route isolation, scoped AppRole/replay/revocation checks,
bootstrap denial, Google peer admission and denial of other Internet/metadata
destinations. Human MFA, owner provisioning and connector runtime acceptance
must be qualified independently.
