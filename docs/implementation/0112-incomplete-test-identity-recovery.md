# Slice 0112: Incomplete Test Identity Recovery

Classification: **security-critical identity and recovery**. Revision 4.0 sections
4, 12 and 19 apply. This is a one-record operator repair under the existing
[dedicated-test boundary](../adr/0097-dedicated-test-login-ingress.md), not a
product account-linking mechanism. No accepted specification or identity policy
changed.

## Cause And Boundary

The initial real Google first-broker attempt created a local test user before the
ingress rejected Keycloak's `after-first-broker-login` return route. A subsequent
attempt collided with that record. Slice [0111](0111-dedicated-test-login-ingress.md)
fixed the two exact first/post-broker completion routes; missing-state requests
now reach Keycloak and fail with 400, while unrelated provider routes remain 404.

The human explicitly approved cleanup and restart. Private metadata proved the
target was the exact newly created test record, unverified, with no password,
OTP, federated identity, federation source, service-account association, consent,
group, required action, issued credential, offline session or login failure.
Its only role was this test realm's default role. The Signal application user
registry was independently empty. No email-only linking or user impersonation
was used.

## Qualified Repair

[Operator helper](../../scripts/integration_cleanup_incomplete_identity.py):

- Pins the one observed realm, user ID, creation/modification timestamps and
  approved email. Rejects changed identity, verified accounts, any credential or
  link, other dependencies, additional or nondefault roles, missing approval,
  and existing recovery files.
- Stops only Keycloak, preserving its persistent database and private network.
  Copies the exact empty user and default-role mapping to an authenticated AES-GCM
  recovery pair in a new protected Mac directory, outside the repository.
- Decrypts and validates that copy, rehearses deletion and SQL restoration inside
  one real PostgreSQL transaction, compares the restored record, and rolls back.
- Exercises a changed-precondition rejection against the real database and
  checks that no data changed. Actual removal is an exact-snapshot guarded
  transaction with bounded locks on the user and dependent tables. Any unknown
  result remains explicit; no automatic retry occurs.
- Restarts Keycloak even on maintenance failure. Does not reactivate bootstrap
  administration, change any credential, insert a provider link, grant application
  membership, alter MFA, or touch the human's Google/Slack/GitHub accounts.

Real repair completed. The removed user count is zero, the bootstrap operator
remains disabled, and public realm discovery returned 200 after restart.
No provider link was fabricated.

## Real Callback Repair

The next real Google callback timed out after exactly ten seconds. Safari saved
an empty `endpoint` file; download metadata identified the exact approved Google
callback, without opening the file or replaying its code. Credential-free request
status/duration logs confirmed 504. These logs delete the entire request and
response-header objects and have a qualified two-file, 1-MiB bound. Error responses
now carry generic HTML, no-store and no-referrer instead of an empty download.

Keycloak 26.7.3's actual
[Google provider source](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/social/google/GoogleIdentityProvider.java)
uses `openidconnect.googleapis.com/v1/userinfo`, omitted from the first three-peer
pin set. The exact fourth identity-only peer is now screened, pinned and qualified;
the old admission was expired first, a new one-hour timer armed before enabling
the replacement, and identity recreated with its overlay. A bounded five-second
connection-acquisition timeout was added and the ingress response deadline raised
to 35 seconds for sequential bounded broker calls. No redirects, retries, general
Internet route or product connector access was enabled.

Real network checks on the identity bridge verified all four TLS peers, JWKS 200
and unauthenticated user-info 401, while unrelated Internet and metadata timed
out. A fresh real Google callback returned the profile form in approximately three
seconds. The human's previously entered last name was restored with verified
readback; submission reached Mobile Authenticator Setup. The human completed it.
Independent private metadata now shows one verified, enabled Google-linked user
with an OTP credential, plus real REGISTER, UPDATE_TOTP and LOGIN events.
No QR seed or OTP value was collected. A signed Signal owner application session
is still **PENDING**; its user registry and identity-session table remain empty.
Connector runtime qualification and all external/production writes remain disabled.

## Checks

[Evidence](../evidence/0112-incomplete-test-identity-recovery.json).

```sh
.venv/bin/python -m pytest tests/tooling/test_integration_identity_cleanup.py -q
.venv/bin/ruff check scripts/integration_cleanup_incomplete_identity.py tests/tooling/test_integration_identity_cleanup.py
.venv/bin/python scripts/qualify_integration_application.py --ca /Users/example-owner/.codex/signal-application-20260930/ca.pem --public
.venv/bin/python scripts/integration_cleanup_incomplete_identity.py --directory /Users/example-owner/.codex/signal-incomplete-identity-recovery-20260930 --rehearse-recovery-copy
npm test
```

27 policy/failure cases and 1273 API/identity/tooling regression cases passed.
Real persistent Keycloak PostgreSQL delete/restore
rollback, changed-precondition rejection and actual exact-record removal passed.
The final structured SQL literal serializer also passed untrusted profile-string
delete/restore rollback against session-local clones of the real schema, leaving
all live identities unchanged. Ruff and private/public runtime qualification passed.
`npm test` passed 38 repository and 148 dashboard cases, 231 Markdown checks,
typecheck and build. Its first typecheck found sync-created duplicate generated
Next type files; the generated cache was preserved outside the repository and
rebuilt. No source or test safety gate was changed to hide the failure.
The one-shot command is deliberately not rerunnable after success: the old user
is gone and the recovery directory is nonempty. It must not become a generic
cleanup job or production account recovery feature.
