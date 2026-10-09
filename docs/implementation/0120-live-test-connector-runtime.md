# Slice 0120: Live Test Connector Runtime

Historical deployment checkpoint. Later operator retirement, repaired live
callbacks and actual provider results are recorded in
[0122](0122-dedicated-provider-callback-qualification.md).

Classification: **security-critical secrets, recovery, identity and deployment**.
Status: **RUNTIME DEPLOYED AND ISOLATION QUALIFIED; PROVIDER CONNECTIONS INCOMPLETE**.

This advances [0119](0119-private-connector-deployment-preparation.md) under the
unchanged [ADR 0094](../adr/0094-owner-authorized-integration-testing.md) exception.
No product invariant, provider scope, public administrative exposure or cost changes.

## Actual Deployment

The owner approved the existing private recovery and API restart. With the API
paused, two protected recovery shares provisioned only the existing seven test
workloads through the reviewed 0119 helper. The temporary root was revoked and
denied, an encrypted Raft snapshot retained the new artifact key, and the original
secure listener was restored byte-for-byte before resumption. Its SHA-256 is
`e18e7713446fc88f1a34eb3033c167ad3fbf01ed09ce0a9a0771e91dc65b67f4`;
the legacy recovery endpoint again returns 405. The narrow deployment operator
has a one-hour lease; its explicit retirement is a subsequent operational gate.

Actual private configuration reads, cross-role/authority denials, single-use
SecretID replay rejection, 300-second renewal and token revocation/403 passed
for Slack, GSC, GitHub and owner-artifact roles. Seven fresh runtime SecretIDs
were installed privately. No root or provider secret was logged or committed.

The persistent Signal database was backed up as bounded AES-256-GCM-encrypted
custom pg_dump bytes, with in-memory decrypt equality. Restore was NOT_EXECUTED.
The pinned, non-root/read-only migration container upgraded 0060 to 0062, then
the migrator returned to NOLOGIN. No database reset, bootstrap, forged session,
standing authorization or ownership record was created by the operator.

The actual API image is
`sha256:60877f8b74e276e749ac1f7928fe2309e21221c1c56cde6814e44578dbd863c9`.
The 0118 dashboard and existing ingress/proof handler are unchanged. The separate
owner-robots volume is mode 0700, owned by runtime UID/GID 10001. Readiness passed.

Fresh screened, expiring, API-source-only TLS pins permit only api.github.com,
oauth2.googleapis.com, www.googleapis.com and slack.com. A persistent expiry timer
was armed before admission. Actual TLS/SNI passed for each fixed numeric peer;
unrelated Internet and metadata peers were rejected. This is network preflight,
not provider connection qualification. The independent Google identity window
retains its existing four peers and unchanged identity/MFA/session policy.

## Qualification

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
.venv/bin/python scripts/qualify_integration_application.py --ca /Users/example-owner/.codex/signal-application-20260930/ca.pem --public --owner-connectors
npm test
```

The 1,442 API/identity/tooling cases passed. The explicit `--owner-connectors`
profile accepts only the three configured test providers as `internal_only`;
the default still requires disabled providers. Both profiles require disabled
command acceptance and production writes. Positive, negative and failure cases
reject broadened providers, worker/write enablement and duplicate/missing denials.
Neither profile claims an OAuth exchange or connected provider.
All 39 repository and 153 dashboard cases, documentation checks, TypeScript and
the production dashboard build passed. Changed Python lint/format checks passed.

Real private/public TLS, PKCE and invalid callback rejection, protected site
routes, same-origin BFF, wrong CA/SNI, and public administrative route/port
isolation passed. Separately, the human completed normal Google/OTP login in
Safari, selected the existing owner workspace/site, and reached Owner/MFA.
Read-only database evidence confirms one normal session, expiring
2026-10-03 06:25:09 UTC, and zero standing authorizations. Cookies, account, MFA
and the normal eight-hour session policy were not cleared or weakened.

## Remaining Failures

The actual GitHub flow obtained a scoped installation token and fetched the
exact repository and main branch through audited shared egress with real robots
retrieval. All three provider responses succeeded, but binding did not complete:
the unprotected branch is correctly unacceptable and the binding remains
`prepared`, requiring a bounded failure-recording repair. GitHub's actual settings
show no protection rule and warn enforcement is unavailable for this private
repository on the existing plan. Visibility/billing were not changed.

The owner approved Test Workspace's final chat:write-only Slack installation at A0. Safari
returned through the exact live callback, but no provider exchange or binding
occurred. Next's custom-server URL metadata uses `dashboard:8443`, while the
callback requires the public origin. This deployment mismatch requires repair;
the guard must not simply be removed. GSC uses the same guard and remains
unconnected, with its exact eligible property/verification still pending.

[Evidence](../evidence/0120-live-test-connector-runtime.json) distinguishes deployed
runtime, successful normal login, actual negative provider outcomes and unfinished
connections. Slack signed interactivity is not deployed. No Gmail access, new
provider permission, public repository, production readiness or publishing
authority is claimed. The user's full request remains **IN_PROGRESS**.
