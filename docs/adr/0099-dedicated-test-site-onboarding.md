# ADR 0099: Dedicated Test Site Onboarding

Status: **Accepted 2026-10-01**.

## Decision

Compose the existing protected site directory, owner creation and active-site
selection only for `https://signal-dev.example.com`. Preserve normal MFA,
membership, tenancy, recovery, session-version and CSRF checks. Never insert a
site, session or ownership proof through an operator shortcut.

Provider registrations alone are not working site-bound connectors. This narrow
composition establishes real owner scope without reusing the disposable pilot.
It does not admit commands, standing grants or provider operations; each requires
its own qualified composition. No SQL privilege or accepted specification changes.

## Qualification

[Slice 0114](../implementation/0114-dedicated-test-site-onboarding.md) records
real Safari creation and database readback, unauthenticated denials, exact-origin
and failure tests, and unchanged administrative isolation.
