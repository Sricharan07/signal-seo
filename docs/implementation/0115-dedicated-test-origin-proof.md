# Slice 0115: Dedicated Test Origin Proof

Classification: **security-critical ownership and egress composition**.
Status: **REAL OWNER PUBLIC PROOF QUALIFIED; CONNECTORS STILL PENDING**.
[ADR 0100](../adr/0100-dedicated-test-origin-proof.md), Revision 4.0 sections 4,
12 and 19 and Revision 3.2 sections 10 and 11 apply.

## Implemented And Qualified

Compose only canonical UUIDv4 POST routes for the existing challenge and proof
operations. Wrong methods, extra paths, other origins and commands fail closed.
The enrolled owner issued a challenge in Safari; its UI and PostgreSQL identity
matched. Only its exact plaintext resource was published on the approved HTTPS
hostname, without changing root-domain, email or provider settings.

The existing public-address pinned HTTP fetcher retains exact TLS SNI, proof
bytes, bounds and redirect rejection. Its protected resolver admits only the
screened approved public IPv4 peer during a maximum one-hour window. The operator
preparation checks the complete live DNS answer set; changed or unsafe resolution
writes nothing. A separately armed persistent firewall timer drops both new and
established traffic when admission expires. No DNS/network or credential bypass
is added. Provider traffic remains disabled and does not use this proof pin.

Real API-bridge qualification reached the approved public peer with verified TLS;
unrelated Internet and cloud metadata were blocked. With that peer explicitly
expired, the owner's normal Verify request displayed an unavailable proof and
persisted `transport_unavailable`, with zero verification rows. After restoring
the still-current bounded pin, a fresh normal request persisted HTTP 200 at
`192.0.2.10`, `http_well_known`, and the exact verified origin. Verification was
recorded at **2026-10-01 11:26:29 UTC**, recheck due 30 days later. Zero standing
grants or dispatch authority was created.

The deployed API is
`sha256:48456b79daa10da356ba1a282b82f7b546cdab4b01b385573586b8fdd7ae1a2d`,
running with zero restarts. Existing limited operator issued only fresh one-use
login workload credentials; no root recovery or additional secret privileges was
needed. Uploads were removed. Administrative routes/ports remain private and the
public/private login qualification passed again after startup. An immediate
pre-start probe correctly failed rather than treating a starting process as ready.

## Verification

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/ruff check apps/api/src/signal_api/integration_runtime.py scripts/integration_origin_egress.py tests/api/test_integration_runtime.py tests/tooling/test_integration_origin_egress.py
.venv/bin/python scripts/qualify_integration_application.py --ca /Users/example-owner/.codex/signal-application-20260930/ca.pem --public
npm test
```

API/identity/tooling passed **1,365 cases**. The real isolated network lab passed
**14 cases**. [Evidence](../evidence/0115-dedicated-test-origin-proof.json) records
the remaining gates. These results do not qualify production readiness or claim
that Slack/GSC/GitHub runtime is connected. That work continues separately.
