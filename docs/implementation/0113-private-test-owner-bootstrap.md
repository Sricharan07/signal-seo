# Slice 0113: Private Test Owner Bootstrap

Classification: **security-critical identity, tenancy, secrets and recovery**.
Revision 4.0 sections 4 and 19 and Revision 3.2 sections 7 and 8 apply.
[ADR 0098](../adr/0098-private-test-owner-bootstrap.md) describes the authority
boundary. Accepted specifications, database migrations and privilege manifests
are unchanged.

## Implemented

- Optional verified-assertion observation after the existing full OIDC validation,
  with fail-closed observer failure and no invitation-path observation.
- Exact method/path admission for the existing protected organization directory,
  current session, tenant selection and logout routes. The previous prefix rule
  incorrectly excluded organization discovery and exact `/v1/session`. Unknown
  session prefixes, wrong methods, site mutation and product authority stay
  unavailable; normal authentication, CSRF, membership and recovery checks remain.
- Dedicated, exact-subject capture in protected container tmpfs, create-only,
  five-minute expiry and one-hour operator approval window. Default composition
  does not configure it; no public API can enable it or provision an owner.
  Only the protected operator approval file can renew a configured window;
  malformed, missing or unsafe material fails closed and purges retained proof.
  Completion prevents renewal from recapturing credentials. Renewal does not
  require restarting a healthy workload or recovering deployment credentials.
- [Private operator bootstrap](../../scripts/integration_test_owner.py) independently
  revalidates the signed assertion against current real signing keys and the
  actual consumed database nonce. Requires verified exact identity and fresh
  signed completed-OTP strength; never accepts account enrollment as MFA proof.
- Protected intent/outcome receipts without tokens; bounded, serialized,
  create-only user/tenant/directory/owner insertion, existing bootstrap-role RLS,
  rollback rehearsal, no upsert or regrant, and explicit unknown-outcome handling.
  No site, session, standing authority, connector binding or external operation
  is inserted.
- [Limited deployment recovery](../../scripts/integration_recover_deployment_operator.py)
  requires explicit human approval, two protected shares, an unchanged existing
  limited role, token-scope denials and immediate decoded-root retirement on both
  success and failure. An incomplete/unknown generation remains blocked and is
  recorded privately. Any known partially issued operator is revoked and denied
  if a scope probe, receipt write or root retirement fails. It does not edit
  listener settings or policies.
  Explicit render credentials never fall back to a root when missing.

## Live State

The new immutable API image was deployed on the existing VM:
`sha256:174a3fa7a0e517e7c5f3fc69dd3d6bc29bd6ec4aae284b6374c7b0437353b4d3`.
The approved owner's actual Google-linked identity completed OTP in Safari.
The private operator independently validated that signed assertion and actual
consumed nonce, rehearsed the persistent transaction with rollback, then created
exactly one user, Signal Test tenant and active owner membership. A second normal
Google/OTP login issued a real MFA identity session; the human-facing CSRF-protected
workspace selection issued a real owner/MFA tenant session. Safari showed that
session and private readback confirmed one live session of each kind. No session
was inserted by the private bootstrap.

The raw captured assertion was removed, capture completion checked, and its
operator approval file removed. The final API starts without assertion capture.
Reusing the private bootstrap audit directory and omitting human approval both
rejected before SQL; counts remained one owner/workspace with zero sites,
standing authorizations or dispatch authorizations. Anonymous/forged session and
directory requests returned 401, cross-origin tenant mutation returned 403, and
an unregistered session prefix returned 503. Slack/GSC/GitHub, site work and all
external writes remain unavailable.

The old scoped private deployment credential expired. The first approved
recovery attempt received OpenBao 2.6.1's 405 on the disabled legacy endpoint
before submitting shares. The owner subsequently explicitly approved the
temporary private-listener compatibility exception. With the API paused, two
protected shares generated a temporary root and recovered only the existing
limited deployment role. The root was revoked and denied (403). The original
secure listener configuration was restored byte-for-byte, OpenBao was restarted
and unsealed, and the legacy endpoint again rejected requests (405). Fresh
one-use workload credentials admitted the new API image. The temporary limited
operator was also revoked and denied (403). No public administration was exposed.

The four fixed Google identity-only peers were deliberately renewed, with old
admission expired first and the new timer armed before enabling pins. Their
current one-hour deadline is **2026-10-01 11:07:46 UTC**. Actual bridge TLS checks returned
302/404/200/401 on the four fixed hosts; unrelated Internet and cloud metadata
timed out. Existing public/private application qualification passed. These pins
are identity trust-plane traffic, not product connector access.

The earlier pause exceeded both one-hour windows; Google network admission and
owner-proof capture expired closed. On resumption the Mac had changed to
an iPhone hotspot, outside the independently pinned host SSH firewall. A cloud
source refresh did not restore access and was reverted to the original exact
source; no broad administrative allowance was opened. Reconnecting the same Mac
to the original Wi-Fi restored both independent SSH boundaries without changes.
The same approved private recovery was repeated with the API paused; original
listener bytes, 405 rejection, root/operator 403 retirement, fresh workload
credentials and running immutable API image were checked again. The same bounded
Google window was renewed timer-first and the four TLS/two negative peer checks
passed again. The later missing workspace admission rule was corrected, tested
and deployed through the same private, restored-and-retired recovery boundary.
The enrolled account was preserved throughout; owner/session qualification is
now complete. This dedicated test result does not qualify production onboarding.

## Verification

[Evidence](../evidence/0113-private-test-owner-bootstrap.json).

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/run-database-tests.py
.venv/bin/ruff check apps/api/src/signal_api/test_identity_proof.py scripts/integration_test_owner.py scripts/integration_recover_deployment_operator.py
.venv/bin/python scripts/qualify_integration_application.py --ca /Users/example-owner/.codex/signal-application-20260930/ca.pem --public
npm test
```

Positive/negative/failure tests cover exact signed identity, missing/wrong OTP,
ACR, nonce, audience, signature, stale time, consumed-attempt binding, protected
capture, protected renewal/read failure, expiry, completion, exact route admission,
recovery scope and root retirement. API/identity/tooling regression passed
**1,333 cases**. Real PostgreSQL
schema-clone/RLS checks cover rollback, committed exact owner, replay, changed
nonce, future approval and missing attempt. Real human owner/login qualification
is recorded separately from the credential-free application qualification script:
that script intentionally cannot claim a human session or connector journey.

The first full database run passed its 836 cases but was correctly rejected by
the stable-source guard because additional tests were added during execution.
The guard was not bypassed; the final stable-source run passed **837 cases** and
completed cleanup, most recently recorded at `2026-10-01T10:17:33.457454+00:00`.
The live operator also successfully rehearsed the actual persistent-schema
transaction before the owner insert. Current provider grants, consent and all
production or external writes remain unchanged/unavailable.
