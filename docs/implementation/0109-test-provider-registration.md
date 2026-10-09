# Slice 0109: Test Provider Registration

Classification: **security-critical operational qualification**. This continues
[0108](0108-private-connector-configuration.md) using the unchanged private store,
operator importer and owner-approved test exception. Contract: Revision 4.0
sections 4, 12 and 19; Revision 3.2 sections 4 and 10.2. The existing
[ADR-0094](../adr/0094-owner-authorized-integration-testing.md) and
[ADR-0095](../adr/0095-private-persistent-integration-secrets.md) apply. No product
authority, accepted specification or runtime code changed.

**Registered: two Google clients, one owner-only test audience, and the exact
read-only GitHub installation. Stored: both Google app configurations, the
owner-generated GitHub App key and Slack client/signing configuration. Public
callbacks and authorized Signal-runtime provider calls remain NOT_EXECUTED.**

## Human Approval And Real Observations

After an ambiguous "done," Safari still showed a pending download prompt. The
owner explicitly approved all three concrete actions: downloading and privately
storing both Google client JSON files, adding only `owner@example.invalid` as
the test user, and installing the GitHub App read-only on only
`example-owner/integration-test`.

Google project `integration-test` now contains two Web applications:

| Client | Exact Sole Redirect | Nonsecret Client ID |
| --- | --- | --- |
| Signal Test Google Sign-In | `https://signal-test.example.invalid/identity/realms/signal/broker/google/endpoint` | `000000000000-placeholder.apps.googleusercontent.com` |
| Signal Test Search Console | `https://signal-test.example.invalid/auth/gsc/callback` | `000000000000-gsc-placeholder.apps.googleusercontent.com` |

Both client creations and the two entries were visibly confirmed in Safari.
Neither has a JavaScript origin or additional redirect. The downloaded provider
documents passed the importer's exact project/endpoint/callback/schema checks.
The audience visibly remains **Testing**, with **1 user (1 test, 0 other)** and
the exact approved email. The first audience Save click did not commit; a
fresh-state keyboard submission and final audience readback confirmed the result.
No Gmail API, mailbox permission or Google account data authorization occurred.
Search Console enablement was already confirmed in 0108.

GitHub visibly confirmed `Signal Test Placeholder` installed, installation ID
`123456789`. Post-install settings show only **Read access to code and metadata**,
**Only select repositories**, **Selected 1 repository**, and the exact test repo.
All-repositories remains unselected. GitHub's standard UI also notes read-only
public-repository access; no other repository was selected, and Signal's future
binding must still enforce exactly one repository. No PR/workflow/administration
permission, user OAuth, webhook, merge, deploy or default-branch write was enabled.

The owner generated the App private key in Safari and explicitly approved its
private import. GitHub's key-added readback was confirmed. The initial download
check found no PEM, so import waited; the owner subsequently supplied the actual
download path. The validated RSA key was moved into the protected Mac directory
and imported without displaying its contents. This is not live App API acceptance.

## Private Storage Qualification

Official downloads were checked as owner-owned regular, single-link files,
changed to mode 0600, and moved without replacement into the existing protected
Mac directory. They are absent from Downloads and git. Both dedicated Google
configurations, GitHub App key and owner-entered Slack configuration were imported
over the existing private-CA localhost SSH tunnel:

- `signal-identity/data/google/client`
- `signal-gsc/data/oauth-client`
- `signal-github/data/github/app`
- `signal-slack/data/client`

The unchanged 0108 importer verified exact values and version 1, five-minute
read-only readers with no default policy, denied writes/deletion/other-path/token
creation, revocation, and denied reads after revocation on the real persistent
OpenBao store. Repeating the sign-in import was an expected negative result:
the existing-policy guard rejected it before mutation. No credential values
were displayed, placed in arguments or committed. No model/provider API was called.

The older encrypted model-key, post-Google and post-GitHub snapshot pairs were
each preserved together in separate protected archives. A fresh AES-GCM encrypted
snapshot of the post-Slack store was captured on the independently held Mac.
This newer artifact has **not** been separately restored; the earlier 0107
disposable restore qualification is unchanged.

Runnable commands and private-entry constraints:
[0108 runbook](../runbooks/integration-connector-configuration.md) and
[0107 backup runbook](../runbooks/integration-secrets.md).
Evidence: [0109](../evidence/0109-test-provider-registration.json).

Local regression checks passed using the unchanged import tools:

```sh
.venv/bin/python -m pytest tests/tooling/test_integration_connector_secrets.py tests/tooling/test_integration_secrets.py -q
.venv/bin/ruff check scripts/integration_connector_secrets.py scripts/integration_secrets.py tests/tooling/test_integration_connector_secrets.py tests/tooling/test_integration_secrets.py
npm test
```

Results: 89 focused Python cases, Ruff, 28 repository cases, 224 Markdown files,
148 dashboard cases, dashboard typecheck and build all passed. Live callback,
provider grant and provider API checks were not substituted with these results.

## Remaining Boundaries

The owner separately approved private storage of Slack's existing client/signing
secrets. Safari was positioned at App Credentials for hidden local entry. An
initial relative command failed from the owner's home directory; the absolute
command was supplied instead. The resulting real stored configuration was
independently rechecked using the unchanged importer's strict credential validator,
audit check, CAS configuration readback and `verify_reader` denial/revocation
checks. Generation 1, exact fixed client ID and all checks passed without secret
output. This does not establish provider acceptance of either entered secret.

GitHub App API qualification and owner runtime binding remain pending. Slack
still has its exact saved redirect and only `chat:write`, but no owner-bound OAuth
installation, bot token, channel/person binding or interactivity configuration.
Google client credentials are not a Google account grant or durable GSC property
binding.

No persistent application/identity/database composition, independent recovery
authority, workload authentication, shared-egress runtime, GSC owner API/BFF,
DNS/HTTPS ingress or functioning public callback exists. Bootstrap root remains
operator-only, not a workload credential. Do not publish the disposable pilot or
synthetic owner accounts. Production and external-write gates remain closed.
The owner completed the retired temporary Mac IPv6 alias cleanup. Readback with
`/sbin/ifconfig en0 inet6` confirmed the retired address absent and the approved
secured address unchanged. A fresh source-bound SSH connection with pinned host
verification passed. No administrative firewall allowance was broadened.
