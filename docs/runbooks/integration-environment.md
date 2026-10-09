# Protected Integration Environment Configuration

This is the security-critical privacy correction for slices 0105-0122. The
resource authorization remains [ADR-0094](../adr/0094-owner-authorized-integration-testing.md).
Examples and published evidence use placeholders, not operational identifiers.
No new resource, account, scope, public route or publishing authority is granted.

## Configuration Contract

Create one operator-provided JSON file outside the checkout, inside a mode-0700
directory. The file must be a regular, single-link, nonsymlink file with owner-only
permissions and at most 16 KiB. Never put its contents in git, terminal output,
chat, command arguments or public evidence. Set `SIGNAL_INTEGRATION_CONFIG_FILE`
to its absolute path for every operator command. The API defaults only to the
protected file path `/run/signal-application/environment.json`, not to any resource.

Every field below is mandatory. Extra, duplicate, malformed or missing fields
are rejected without including their values in diagnostics:

| Fields | Contract |
| --- | --- |
| `origin` | Exact canonical HTTPS hostname origin, no credentials, port, path, query or fragment |
| `owner_email`, `owner_subject`, `site_id` | Approved test identity and exact UUIDv4 subject/site |
| `slack_workspace_id`, `slack_channel_id`, `slack_client_id` | Exact approved workspace, channel and OAuth application |
| `google_project_id`, `google_login_client_id`, `google_gsc_client_id` | Exact project and two distinct OAuth clients |
| `github_app_id`, `github_installation_id` | Positive integer identifiers for the read-only App and installation |
| `github_owner`, `github_repository`, `github_base_branch`, `github_content_path` | Exact repository read target, never wildcard access |
| `public_ipv4`, `public_ipv6`, `ssh_source_ipv6` | Canonical deployment and operator network addresses; egress separately screens public destinations |
| `ssh_key_path`, `ssh_known_hosts_path` | Absolute protected operator paths; no traversal |
| `realm_id`, `incomplete_user_id`, `incomplete_created_timestamp` | Exact historical cleanup guard, not authority to clean up another identity |
| `tenant_name`, `home_region` | Operator-provided bootstrap metadata |

Loaders snapshot the validated configuration for each runtime object. Changing
the protected file does not silently enlarge an existing object's target. The
API visibly disables identity, connector and command capabilities and returns
`TEST_CONFIGURATION_UNAVAILABLE` when its configuration cannot be loaded.
The dashboard requires both private canonical origin JSON and the matching
runtime origin; absent repository configuration prevents connector dispatch.
No documentation address is exempted from crawler public-address screening.

## Render And Deploy

```sh
export SIGNAL_INTEGRATION_CONFIG_FILE="$HOME/.codex/integration-private/environment.json"
.venv/bin/python scripts/integration_environment.py --directory "$HOME/.codex/integration-private/rendered"
```

Use an empty protected output directory. The renderer creates owner-only
`environment.json`, canonical `dashboard-origin.json`, `environment.env`,
`realm.json` and `Caddyfile`. Provider secrets still enter through the
[0108 private connector import](integration-connector-configuration.md) and
OpenBao; the new file does not hold provider secret material.

On an existing deployment preserve its exact Signal-origin and Google
verification handlers when moving Caddy to the private mount. Do not replace
them with the unaugmented template. Install the API configuration as UID 10001
mode 0400, dashboard origin and ingress configuration as UID 1000 mode 0400,
and the identity realm as UID 1000 mode 0400. Compose reads the protected
`environment.env` alongside image bindings. Database, TLS, origin pins, existing
OAuth credentials, owner/session records and migrations are not recreated.

Workload SecretIDs remain one-use. Never restart with consumed credentials or
silently fall back to root. An expired/revoked operator requires a separately
approved private quorum recovery, unchanged narrow roles, explicit temporary
root retirement, restoration of the secure listener, and operator retirement.
Preserve normal session expiry, MFA, CSRF, PKCE and strict TLS throughout.
The OpenBao configuration must remain readable by its existing UID 100/GID 1000;
an owner-only deployed configuration can use mode 0400. Do not install a
root-owned mode-0600 file that the capability-free container cannot read.

## Restart Search Console Consent

The owner performs every sign-in and consent step. A vanished or expired Google
tab is not evidence of a successful callback:

1. Open the authorized Signal deployment and select **Connectors**.
2. Under **Google Search Console**, click **Connect Search Console**.
3. Select the approved Google test account, continue through the test-app notice,
   and approve only `webmasters.readonly` Search Console access.
4. Return to Signal and confirm only the exact authorized URL-prefix property.

Before starting, ensure the narrowly pinned provider network window is live.
If the owner session expired, the owner completes normal Google sign-in and MFA;
an operator must not clear cookies, extend sessions or bypass MFA to finish.
Google Auth Platform's Audience page must remain **Testing**, with only the
approved owner in Test users. This does not enable Gmail or production readiness.

## Qualification

Configuration tests cover every missing field, malformed values, duplicate/extra
JSON fields, permissions, symlinks, hardlinks, bounded reads, immutable snapshots,
exact rendered callbacks/owner filters, unavailable API states and documentation
IP rejection. Dashboard repository tests additionally reject duplicate, oversized,
noncanonical and untrusted origin configuration. Existing identity, egress,
callback, authority and database tests remain in force. Record post-deployment
positive, negative and failure results separately from historical 0105-0122
checkpoints and never claim new OAuth consent from a preserved binding alone.

See [0122](../implementation/0122-dedicated-provider-callback-qualification.md)
for historical provider results and the privacy correction qualification record.
