# ADR-0096: Private Persistent Test Identity

Status: Accepted 2026-09-30 for owner-authorized testing only.

The public-admission checkpoint is superseded by
[ADR-0097](0097-dedicated-test-login-ingress.md); isolation and identity rules remain.

## Context

The dedicated provider registrations in [0109](../implementation/0109-test-provider-registration.md)
have no persistent callback runtime. The disposable pilot has public synthetic
credentials and is explicitly excluded by [ADR-0094](0094-owner-authorized-integration-testing.md).
The owner requested continuing deployment on the same 4-GB/80-GB VM.

## Decision

Deploy optimized, digest-pinned Keycloak 26.7.3 backed by a separate persistent,
digest-pinned PostgreSQL 17.11 database. Its application role is nonsuperuser;
database connections use private-CA TLS with hostname verification. Containers
are nonroot, readonly, resource bounded and cannot produce core dumps. Only
loopback identity TLS is published. Database, identity administration, master
realm and management endpoints remain private.

Use the existing dedicated Google sign-in client through Keycloak's file vault,
not a credential-bearing realm export. Infrastructure passwords are distinct,
create-only OpenBao generation-1 values. Operator export is protected on the Mac;
VM runtime password/vault files reside in tmpfs. The identity bridge cannot
masquerade and drops new forwarded and host-bound traffic. Google broker
networking is deliberately unavailable until its outbound path is qualified.

The realm accepts only the approved owner email upstream, stores no upstream
tokens, has no synthetic users, disables registration/password grants and
requires the exact dashboard callback with authorization-code S256 PKCE.
This allowlist does not grant Signal ownership: a verified issuer/subject,
required MFA and explicit application provisioning remain necessary.

## Alternatives And Consequences

- Publish the disposable pilot: rejected; inappropriate credentials and lifecycle.
- Publish identity administration or temporarily allow all outbound traffic:
  rejected; neither is needed for private qualification.
- Buy another VM or increase memory: deferred; the approved base supports this
  private foundation without additional recurring cost.
- Make every Docker network internal: tried; Docker suppressed the published
  loopback port. Retain the internal database network and use a nonmasquerading,
  firewall-restricted ingress bridge instead.

This is a private foundation, not a complete login deployment. Operator bootstrap
retirement, independent recovery/workload authentication, required MFA, qualified
Google outbound traffic, application composition, DNS/TLS ingress and full
provider journeys remain public-admission gates. Reboot loses tmpfs material and
requires deliberate operator rehydration; no automatic recovery is advertised.

## Verification

[0110](../implementation/0110-private-test-identity.md) records real private TLS,
Keycloak rejection/readback, PostgreSQL TLS/privilege denial, container replacement,
missing-secret startup failure and outbound denial. No Google consent or public
callback success is claimed.
