# ADR-0095: Private Persistent Integration Secrets

Status: Accepted 2026-09-30 for owner-authorized testing only.

## Context

[ADR-0094](0094-owner-authorized-integration-testing.md) permits dedicated
integration testing, but excludes publishing the disposable local pilot with
real credentials. Its development OpenBao is not persistent. The owner requested
using the supplied OpenAI and Jev keys temporarily and approved switching the
existing 4-GB/80-GB VM from the $20 IPv6-only plan to the $24 dual-stack plan
after a measured registry timeout.

## Decision

Deploy pinned non-development OpenBao 2.6.1 with single-node Raft persistence,
strict private CA TLS, HMAC-redacted declarative audit, nonroot containers and
loopback-only administration. Disable bridge masquerading and deny new forwarded
egress from this dedicated bridge, including after Docker/host restarts.
Keep the initial root token, CA key and three unseal shares only in an owner-only
directory on the separate FileVault-enabled Mac; two shares are required.
Restarts leave the store sealed until the operator unseals it. Never put those
materials in Signal runtime configuration or the VM.

The operator helper accepts hidden terminal input, creates existing Signal model
credential paths with CAS zero, and verifies exact read-only readers before
revoking temporary tokens. Recovery snapshots are AES-256-GCM encrypted on the
Mac. Restore qualification requires a distinct disposable cluster; no live-store
restore or automatic destructive retry is provided.

The owner knowingly deferred rotation of chat-exposed keys. This permits the
short operator test, not a claim that the keys are uncompromised or suitable for
production. Runtime workload authentication and bootstrap-root revocation remain
requirements before public admission. Provider validity remains unverified until
the provider-specific qualification runs.

## Alternatives And Consequences

- Reuse development mode: rejected; real keys need persistent protected storage.
- Keep all recovery keys on the VM: rejected; loss would also prevent recovery.
- Deploy a multi-host quorum now: deferred, outside approved base capacity.
- Keep IPv6-only: rejected after actual registry failure; the owner approved
  dual-stack on the same VM rather than a proxy or new host.

This is a manually operated single-node test foundation, not a private-pilot or
production topology, independent authority journal, or complete installation.
No product authority invariant or accepted specification is changed.

## Privacy Correction For Publication

Owner-authorized publication of 0105-0122 removes personal and operational
identifiers from source, tests and evidence. Nonsecret exact-scope identifiers
now live in a separately protected operator JSON file; provider credentials
remain in the existing OpenBao import paths. A strict, bounded loader rejects
missing, malformed, duplicate, extra or insecurely stored configuration and
snapshots it for runtime objects. API capabilities visibly remain unavailable
without it. Identity, ingress and dashboard manifests use privately rendered
values rather than image defaults.

Configuration is not a grant of authority: changing resources beyond ADR-0094
still requires explicit owner authorization. Exact-target checks, current human
Owner/MFA authority, forced RLS, shared egress, PKCE, CSRF, TLS, secret ACLs and
production-write prohibition remain unchanged. No runtime model can write or
enlarge this operator-owned configuration. See the
[configuration contract](../runbooks/integration-environment.md) and
[0122 follow-through](../implementation/0122-dedicated-provider-callback-qualification.md).

## Verification

[0107](../implementation/0107-private-integration-secret-store.md) records real
TLS, ACL, seal, reboot, encrypted snapshot/restore and external-port checks,
including initial failures and repairs. Public application and provider OAuth
workflows remain unavailable.
