# Slice 0119: Private Connector Deployment Preparation

Classification: **security-critical credentials, recovery and deployment path**.
Status: **HELPER LOCALLY QUALIFIED; DASHBOARD DEPLOYED; PRIVATE PROVISIONING,
API DEPLOYMENT AND POSITIVE PROVIDER FLOWS PENDING OWNER CONFIRMATION**.

This is the historical 0119 checkpoint. Subsequent approved live provisioning,
API deployment, normal owner login and incomplete provider outcomes are recorded
in [0120](0120-live-test-connector-runtime.md); its remaining failures are explicit.

[ADR 0094](../adr/0094-owner-authorized-integration-testing.md),
[ADR 0101](../adr/0101-current-owner-connector-egress.md) and the unchanged
Revision 4.0/3.2 safety and qualification requirements apply. This advances
[0118](0118-owner-connector-browser-flows.md) without claiming complete integration.

## Prepared Recovery

The explicit private quorum helper now issues a fresh credential for the existing
limited deployment AppRole after its reviewed workload-issuance policy is
installed. It refuses changed role/policy preconditions, requires the existing
one-use login, one-hour token lease and four-hour maximum, and proves denials for
provider/artifact secrets, token creation and policy writes. No provider scope or
public listener is expanded by this helper.

The temporary root is revoked and denied immediately. A second protected AES-GCM
Raft snapshot, taken with the limited operator after root retirement, retains the
new artifact key without retaining an active root in that snapshot. An incomplete
probe, root retirement, receipt or post-retirement backup revokes the partial
operator and withholds application resumption. Partial credential-file write or
removal failures still attempt token revocation and denial; cleanup errors never
permit resumption. Successful preparation records
that provider connections remain NOT_EXECUTED and the API must stay paused until
the secure listener, credentials and deployment are qualified.

This operation is **not executed on the live VM**. The prior operator expired;
the new explicit owner confirmation is pending. The secure OpenBao listener has
not been weakened, and no root fallback was used.

## Local Verification

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/qualify_integration_connector_authority.py
.venv/bin/python scripts/qualify_integration_application.py --ca /Users/example-owner/.codex/signal-application-20260930/ca.pem --public
npm test
```

All **1,434** API/identity/tooling cases passed on 2026-10-02, including twelve
focused provisioning and twelve callback-qualification cases. All 39 repository
and 153 dashboard cases, TypeScript and the production build passed. The fresh
disposable real TLS/Raft OpenBao lab passed existing workload
ACL/lifecycle checks plus actual limited operator issuance, provider-secret and
authority-enlargement denials, encrypted snapshot after root retirement, and
operator revocation/403. The lab was cleaned up. It does not qualify live quorum
provisioning or a provider connection. The 874-case PostgreSQL regression from
0118 is unchanged; no database/core code changes occur in this slice.

## Actual Deployment

Both committed 0118 images were built on the existing VM with pinned bases,
allowlisted contexts and 1,536 MiB build limits. A disposable no-network,
read-only/non-root API import check passed without mounting credentials.

Only the dashboard was recreated. Its actual running image is
`sha256:cd5116f6bfdc9f8c970417f69fb13d96e02fbfe6eca059c2c1df6bfb41f8ef65`.
The API still runs 0115, database migration remains 0060, and the ingress image
and exact public ownership-proof handler are unchanged. API and ingress were not
restarted, and all three observed containers had zero restarts.

Credential-free real HTTPS checks observed:

- GSC and Slack denied-consent callbacks: 303 to their fixed unavailable state,
  no-store/no-referrer, and only an expired correlation cookie.
- Anonymous same-origin GSC/GitHub mutations: 403, JSON/no-store, no OAuth start
  or binding claim.
- Direct public connector API route: 404; administrative API exposure is not
  widened.

On 2026-10-02 the public/private TLS, PKCE rejection, CSRF and administrative
route/port qualification passed again. The old qualification script initially
rejected the deployed GSC callback because it expected the pre-0118 absent route
(404). It now requires the implemented fixed unavailable redirect (303),
no-store/no-referrer, no download disposition and only its expired correlation
cookie. Redirect, cache, download, active/extra cookie and wrong-status failures
are tested; this is not an acceptance of successful OAuth. Private readback still
confirmed the expired operator is denied (403) and the secure legacy recovery
endpoint is closed (405), with no mutation.

These are **negative callback and isolation checks**, not successful OAuth
exchange, consent, property binding or repository inspection. The newly built
API is not started without qualified new workload credentials and a protected
database migration. [Evidence](../evidence/0119-private-connector-deployment-preparation.json)
keeps the two states separate.

## Remaining Owner Gates

The consolidated pending questions request the exact private deployment recovery,
public visibility of only the disposable test repository to obtain enforceable
branch protection without a paid upgrade, and only the dedicated GSC URL-prefix
property/verification file. No repository visibility, billing, property or
verification-file change has been made. Test Workspace Slack installation/normal consent
and provider calls also remain pending. OTP/passkey steps remain human-only.

The full integration request is **in progress**. No Gmail access, additional OAuth
scopes, standing grant, production readiness or repository publishing authority
is granted.
