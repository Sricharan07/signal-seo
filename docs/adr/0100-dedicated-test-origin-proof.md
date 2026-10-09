# ADR 0100: Dedicated Test Origin Proof

Status: **Accepted 2026-10-01**.

## Decision

The real owner must issue and verify the existing exact public-origin challenge
before any test connector binding. Publish only that plaintext challenge on the
approved test hostname. Use the existing pinned public-address HTTP/TLS boundary,
not an operator-inserted verification or a private-host substitution.

The dedicated API bridge may reach only the approved VM's screened public IPv4
address on TCP 443 for a one-hour operator window. Arm a persistent expiry timer
before opening the pin; expiration drops established as well as new connections.
A protected runtime pin independently rejects wrong origin/address, stale/future
times, oversized windows or unsafe files. Preserve TLS hostname verification,
exact proof bytes, no redirects and current owner/CSRF/recovery checks.

This is ownership trust-plane traffic, not a connector egress exception. Provider
traffic still requires the existing shared robots/origin-admission boundary.
All administrative and other Internet destinations remain blocked.

## Qualification

[Slice 0115](../implementation/0115-dedicated-test-origin-proof.md) records actual
Safari proof, public TLS/numeric-peer readback, a denied-peer failure with no
verification, subsequent normal success, and negative network tests.
