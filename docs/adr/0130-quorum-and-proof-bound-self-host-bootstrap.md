# ADR-0130: Quorum And Proof-Bound Self-Host Bootstrap

Status: Accepted for the unqualified deployment candidate. Date: 2026-10-03.

## Context

Initialization can succeed without an acknowledgement. Persisting plaintext root
or recovery shares on an installation host makes recovery and secrets exposure
unsafe. Creating an application owner from an email/configuration string would
bypass the existing signed identity and OTP path.

## Decision

Use OpenBao's 2-of-3 Shamir ceremony with independently keyed OpenPGP encryption
of initialization shares/root before return. Record create-only intent and an
encrypted acknowledgement outside Git. Require hidden human input, Linux root
and checked tmpfs for plaintext runtime material. Reuse HMAC audit, CAS, scoped
one-use AppRoles and the existing encrypted snapshot implementation.

Import one local identity with temporary password and required OTP enrollment.
Capture a fresh signed consumed login proof privately; reuse existing OIDC/session
validators, recheck durable nonce and human approval, and atomically table-lock
first-owner creation. Exact committed retries do not repair authority. Unknown
outcomes stop. Verify disabled bootstrap identity credentials and revoked operator
authority before a non-secret retirement receipt. No standing or external grants.

## Alternatives

Saving plaintext shares/root, embedding provider keys in Compose, assigning an
owner before identity verification, silently retrying unknown init, or repairing
an existing owner would weaken the existing security contract and were rejected.

## Consequences And Verification

Custody, recovery and private operator issuance remain human ceremonies. There
is no automatic unseal, production cross-store recovery or certificate rotation.
No migration is added. Existing event schemas remain unchanged: OpenBao intent/
outcome and HMAC audit bind operator bootstrap; normal sessions keep their
existing database audit. See [0130](../implementation/0130-self-host.md) for real
PostgreSQL and non-dev OpenBao positive/negative/failure evidence and live limits.
