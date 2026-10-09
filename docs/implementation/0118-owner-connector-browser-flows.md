# Slice 0118: Owner Connector Browser Flows

Classification: **security-critical identity, connector secrets and external I/O**.
Status: **IMPLEMENTED; LOCAL QUALIFICATION PASSED; DASHBOARD SUBSEQUENTLY DEPLOYED
IN 0119; API AND LIVE CONNECTIONS NOT EXECUTED**.

The approved dedicated scope in [ADR 0094](../adr/0094-owner-authorized-integration-testing.md),
[ADR 0101](../adr/0101-current-owner-connector-egress.md), Revision 4.0 sections
4, 12 and 19, and retained Revision 3.2 connector requirements apply. This extends
[0117](0117-dedicated-connector-composition.md), not production publishing authority.

## Implemented

Two function-only PostgreSQL projections require the current selected-site
Owner/MFA session, recovery generation and verified origin. They expose bounded
binding status, never credentials, secret references or unrelated properties.
GSC revocation and reauthentication are restrictive regardless of event ordering.
GitHub epoch changes remain visibly stale; prepared or failed inspections never
become active bindings. Existing forced-RLS tables and write protocols are reused.

Optional HTTP gateways and same-origin BFF controls compose the existing GSC
OAuth/PKCE and GitHub read inspection services. Commands require exact browser
cookie/CSRF/Origin proof, a closed operation schema, bounded unencoded JSON and
current owner authority. Dynamic lifespan composition is resolved at request
time; an unconfigured gateway returns unavailable. Provider exceptions are not
rendered to the browser.

The dedicated test runtime admits only the actual test site's GET/POST Slack,
GSC and GitHub setup routes. GSC permits only the URL-prefix property
`https://signal-test.example.invalid/`, filters before storing candidates/refresh
credentials, and requires separate owner confirmation after consent. Its exact
callback has a ten-minute Secure/HttpOnly correlation cookie, one-use durable
state, S256 PKCE, fixed read-only scope, no incremental scopes, no-store and
no-referrer. Failed/duplicate callbacks never claim a bound property.

GitHub permits only installation `123456789`, `example-owner/integration-test`,
`main` and `README.md`. The existing protected-base and read-only permission
checks are unchanged. No PR, merge, deployment, default-branch push or repository
secret access is enabled. GSC/GitHub credentials use the private verified OpenBao
TLS context; synchronous GSC provider requests run outside the async event loop.
All provider I/O uses the owner egress boundary from 0116, not a fabricated crawl.

The existing quiet dashboard now renders actual GSC/GitHub states and owner
controls alongside Slack. The obsolete generic disconnected rows are removed;
unavailable, selecting, failed, stale and connected are distinct. No service is
made ready by rendering a button.

## Verification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
```

All **874** real PostgreSQL cases passed in 442.99 seconds, including sixteen
new owner-projection cases for current MFA, wrong site/generation/role,
revocation, bounded eligible property selection, no secret/table access and
prepared/active/stale/revoked GitHub status. **1,417** API/identity/tooling cases,
**39** repository and **153** dashboard cases, TypeScript and Next's production
build passed. Initial API/UI inventory expectations and a test-only TypeScript
union error were corrected to the new explicit surface; no authority gate was
weakened. A GSC supplement initially used an incomplete property fixture, then
passed all four TLS-forwarding/filtering/cleanup/transport-failure cases with the
actual property contract. Changed Python files pass Ruff; the wider formatting
check reports fifteen pre-existing unrelated files and they are left unchanged.

Desktop 1440px and mobile 390px layout fixtures covered setup, selection and
unavailability. All six renders had no horizontal/button overflow, 44px minimum
button targets and 16px connector headings. Screenshots were inspected; these
synthetic layout fixtures carry no authentication or provider qualification.
The Impeccable changed-interface detection returned no findings.

See [evidence](../evidence/0118-owner-connector-browser-flows.json).

## Live Gates

The current API/database still use 0115/0060. The dashboard was subsequently
deployed with negative callback checks in [0119](0119-private-connector-deployment-preparation.md).
Private connector-quorum provisioning approval and actual role ACL
qualification, backup/migration, fresh workload credentials, protected artifact
volume, expiring screened provider admission and deployment remain pending.
The limited deployment operator has expired; no root fallback was used.

The approved Google account has no eligible Search Console property yet.
Creating/verifying only the dedicated URL-prefix test property needs the owner's
pending confirmation. The private disposable GitHub repository's current plan
does not enforce branch protection; no visibility/billing change or protection
bypass has been made. Slack's Test Workspace channel selection and normal installation
consent/callback still require live qualification. Google sign-in/OTP and Signal
origin proof were previously qualified in 0113-0115; no Gmail access is included.

The complete integration request remains **in progress**, not complete.
