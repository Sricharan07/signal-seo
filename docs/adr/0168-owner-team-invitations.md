# ADR-0168 - Owner team invitations

Status: Accepted for the locally qualified product boundary; no release authority.

## Context

R1 requires sign-up or invitation acceptance from a fresh browser. Slices 0016,
0017 and 0021-0023 implemented hashed invitations and dedicated identity proofs,
but owners had no issuance route and the dashboard called no acceptance route.
This is a security-critical identity/tenancy change, not presentation-only work.

## Decision

Owner reaffirmation, 2026-10-06: invitations remain share-the-link only, with
no invitation email. Show the one-time link once to the owner. This delivery
choice is intentional and does not depend on SMTP availability.

Use the existing `issue_site_invitation` operation and atomic acceptance functions.
Only a current owner with five-minute-fresh MFA, browser proof and a verified,
selected site can issue through the product route. Grant only viewer, analyst,
editor, approver or admin. Recheck authority inside the issuance transaction under
the same tenant/site/membership locks as the foundations. Migration 0096 (merge train 5; written as 0090) exposes
guarded function-only composition to the existing identity database role; it does
not grant that role direct invitation or audit table writes.

Keep Team under Settings and owner-only. Return bounded, token-free history and
current members; never infer an empty team from a failed read. Do not expose
recipient account existence or another tenant's identity.

Carry the share credential in a URL fragment only until a guarded same-origin
POST puts it in a short-lived secure HttpOnly cookie. OIDC receives a fixed,
non-secret acceptance return path. Use the dedicated invitation-purpose identity
proof and CSRF route; a login cookie is not an invitation proof. After acceptance,
ordinary OIDC login, membership selection and site selection establish the Home
context. Browser-owned destination IDs never create access.

0093's email transport is restricted to token-free reports/alerts for verified,
opted-in recipients, so it cannot deliver this invitation. Show the one-time link
and explicitly say it was not emailed, regardless of SMTP configuration. Do not
pretend to resend or revoke: at the 0158 head the foundations had no guarded
revocation operation. [ADR-0169](0169-invitation-revocation.md) supersedes that
revocation limitation with a separately qualified pending-only operation.

## Consequences

The product journey exists without adding providers, dependencies, new secrets or
password handling. Uncertain outcomes remain explicit and no bearer is persisted
in recoverable form. Link loss and expiry require a new invitation after checking
history; revocation is separately qualified in ADR-0169. Invitation email is
not planned under the owner's 2026-10-06 decision.
Local PostgreSQL, API, dashboard, Keycloak and OpenBao evidence is recorded in
[0158](../implementation/0158-team-invitations.md). Live customer/deployment
qualification and all release gates remain separate.
