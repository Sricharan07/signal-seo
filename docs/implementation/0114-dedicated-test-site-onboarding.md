# Slice 0114: Dedicated Test Site Onboarding

Classification: **security-critical tenancy and identity composition**.
Status: **IMPLEMENTED AND LIVE OWNER ONBOARDING QUALIFIED; CONNECTORS PENDING**.
[ADR 0099](../adr/0099-dedicated-test-site-onboarding.md), Revision 4.0 sections
4, 12 and 19 and Revision 3.2 sections 7, 8 and 10 apply.

## Qualification

The runtime admits exact methods for existing protected site discovery, creation
and selection. Its gateway rejects all but the exact approved test origin before
SQL. Normal owner/MFA, membership, recovery, CSRF, idempotency and session-version
checks are unchanged. Other methods/prefixes, commands, grants and providers remain
unavailable. No migration or database privilege change exists.

The actual enrolled owner created **Signal Integration Test** in Safari through
the normal form. The UI confirmed **Site added**, selected the origin and labelled
ownership unverified. PostgreSQL readback found one onboarding site with
`America/Phoenix` and zero standing grants. No operator inserted site/session
authority. Anonymous directory/current-session requests rejected with 401 and
unauthenticated creation/selection with 403. Unit tests prove exact-origin denial,
unchanged authority arguments and database failure without invented success.

Deployed immutable API:
`sha256:9b40dc8267bdb5c8f0f5fafa134d2f085548724b287ca4f2d60fb2fdfe384318`.
It is running with zero restarts. Approved two-share private recovery issued only
the existing limited deployment role; generated root revoked and denied. Original
OpenBao listener bytes were restored and legacy recovery again rejects with 405.
The limited one-hour operator remains only on the protected Mac during continuing
owner-authorized deployment, without provider-secret or policy-write access.
Transferred credential copies and recovery configuration were removed from the VM.

Identity-only Google pins were renewed timer-first without product connector
access. Public/private TLS, PKCE, invalid callbacks and administrative isolation
were requalified. All provider grants remain unchanged.

## Verification

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/run-database-tests.py
.venv/bin/ruff check apps/api/src/signal_api/integration_runtime.py tests/api/test_integration_runtime.py scripts/qualify_integration_application.py
.venv/bin/python scripts/qualify_integration_application.py --ca /Users/example-owner/.codex/signal-application-20260930/ca.pem --public
npm test
```

API/identity/tooling passed **1,343 cases**, including 40 focused runtime cases.
[Evidence](../evidence/0114-dedicated-test-site-onboarding.json) records the
other gates. The credential-free script intentionally cannot claim a human
session; the real Safari and database evidence above is separate.

## Remaining Work

The site is unverified. Origin proof, actual shared egress, Slack installation
and channel binding, GSC property selection and GitHub read inspection remain
pending. This is not completion of the owner's integration request or production
readiness. Providers, workflow commands and external writes stay disabled.
