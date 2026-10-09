# Slice 0117: Dedicated Connector Composition

Classification: **security-critical credentials, current owner authority, egress
and external I/O**. Status: **IMPLEMENTED; LOCAL QUALIFICATION PASSED;
LIVE PROVISIONING AND PROVIDER CONNECTIONS NOT EXECUTED**.

[ADR 0101](../adr/0101-current-owner-connector-egress.md), the approved test scope
in [ADR 0094](../adr/0094-owner-authorized-integration-testing.md), Revision 4.0
sections 4, 12 and 19 and the retained Revision 3.2 connector requirements apply.

## Implemented

The dedicated runtime optionally composes existing Slack setup/callback services
only for the actual approved site and Test Workspace workspace. A current Owner/MFA
session and recovery generation produce the separate owner context from `0116`;
two dedicated function-only database connections are closed on success and
failure. No fabricated crawl, tenant, session or standing grant is created.
Other capability routes, publishing authority and autonomous work stay closed.

The existing closed Slack protocol runs outside the async request loop. Only
pre-dispatch shared-admission deferral may retry the exact operation within five
seconds; uncertainty, terminal replay, robots denial and other failures cannot
resend it. Lifespan composition is read at request time rather than capturing a
permanently unconfigured gateway. Duplicate JSON fields, encoded and oversize
commands are rejected before connector execution.

Separate short-lived, one-use-login AppRoles are prepared for the existing Slack
client/bot namespace, GSC client/verifier/refresh namespace, fixed GitHub App key
and an independently generated 256-bit owner-robots encryption key. Their own
renewal/revocation is allowed, but token creation, policy mutation and unrelated
secrets are not. The deployment operator gains only exact role-ID/one-use secret-ID
issuance, never provider secret reads. Existing configuration CAS and operator
policy are preflighted before mutation, with a protected encrypted Raft backup.
Quorum recovery is an explicit operator
action, not runtime behavior, and always attempts immediate temporary-root
revocation and a denial receipt before application resumption.

Robots objects use a dedicated private persistent Docker volume and encrypted
artifact key, separate from crawler orphan cleanup. A protected one-hour provider
pin document screens all DNS answers and admits only fixed provider hosts at TLS
port 443. Firewall admission targets the API container's numeric source address;
a persistent expiry timer drops both new and established connections. Existing
public ingress and private administrative isolation are unchanged.

## Verification

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/qualify_integration_connector_authority.py
npm test
```

API/identity/tooling passed 1,407 cases. The disposable real TLS/Raft OpenBao
qualification exercised actual 300-second AppRole tokens, one-use login,
client/key reads, synthetic bot/verifier/refresh create/read/CAS rotation/delete,
cross-provider and authority-enlargement denials, periodic renewal, seal/unseal,
workload revocation and root retirement. An initial post-unseal revocation met
HTTP 307 during Raft leadership recovery; the lab now waits for observed active
health rather than following the redirect or disabling TLS. The fresh rerun
passed and its invocation-owned container/volumes were removed.

Real numeric-peer public robots reads, without provider credentials, observed
Slack HTTP 200 text/plain allowing the exact OAuth API path, and GitHub/Google
HTTP 404 at robots endpoints. Those are transport/parser observations on this
Mac, not a VM connector, OAuth or binding qualification. All 858 PostgreSQL cases
passed, including real owner-factory authorization, revocation and connection
cleanup. Repository 39/dashboard 148 cases, TypeScript
and the Next production build passed. [Evidence](../evidence/0117-dedicated-connector-composition.json)
distinguishes these local checks from live work.

## Remaining Live Gates

The deployed API remains `0115` and the database remains `0060`; no provider
connection is claimed. Explicit private connector-quorum provisioning approval,
real workload ACL readback/denials, migration, artifact-volume ownership, fresh
screened firewall admission, API deployment and normal browser consent/callback
qualification are still required. The prior limited deployment operator has
expired and cannot issue new workload credentials. No root fallback was used.
GSC HTTP/BFF routes and property selection, and GitHub HTTP/BFF binding remain
separate unfinished work. GitHub branch enforcement on the private test repo,
the eligible GSC property and Slack channel/installation are not invented.

The owner's full integration request remains in progress. No production readiness,
Gmail access, additional provider scopes or repository publishing is authorized.
