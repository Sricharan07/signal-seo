# Private Test-App Configuration

This operator-only tool stores dedicated test-app configuration in the private
OpenBao foundation from [0107](../implementation/0107-private-integration-secret-store.md).
It does not install apps, authorize accounts, bind sites or make callbacks live.
Use only the owner-approved test resources in
[0108](../implementation/0108-private-connector-configuration.md).

## Private Entry

First provide the protected nonsecret environment configuration described in
[the environment runbook](integration-environment.md) and set
`SIGNAL_INTEGRATION_CONFIG_FILE`. All example identifiers below are placeholders;
imports require the exact privately configured values, not those examples.

Keep the existing localhost TLS SSH tunnel and protected operator directory.
Download credentials through the provider's official UI only after the exact
creation/access approval. Move downloads into that mode-0700 directory, with
mode-0600 files, before importing. Never paste them in chat or put them in git,
environment dumps, process arguments or deployment manifests. Do not substitute
unrelated projects or repository credentials. A new credential entry/change in
the browser requires human handoff.

The Google clients must be separate Web applications in
`integration-test`, with exactly one authorized redirect each and no
JavaScript origins:

- Sign-in: `https://signal-test.example.invalid/identity/realms/signal/broker/google/endpoint`
- GSC: `https://signal-test.example.invalid/auth/gsc/callback`

Only sign-in and `webmasters.readonly` are intended. No Gmail API or mailbox
scope. Creating a client does not authorize Google account data. The GitHub App
is `1234567`, limited to the dedicated test repository. Slack app credentials
are for `Signal Dev` in `Test Workspace`, not a bot token or unrelated workspace.

```sh
.venv/bin/python scripts/integration_connector_secrets.py github --directory /absolute/private/operator-directory --download /absolute/private/operator-directory/github-app.pem
.venv/bin/python scripts/integration_connector_secrets.py google-login --directory /absolute/private/operator-directory --download /absolute/private/operator-directory/google-signin.json
.venv/bin/python scripts/integration_connector_secrets.py gsc --directory /absolute/private/operator-directory --download /absolute/private/operator-directory/google-gsc.json
.venv/bin/python scripts/integration_connector_secrets.py slack --directory /absolute/private/operator-directory
```

Slack uses two hidden TTY prompts. The helper checks audited TLS storage and
exact read-only ACLs before reporting storage success, then revokes its test
token. Runtime remains unbound. An existing secret or reader policy prevents
replacement. If an operation fails or times out, reconcile the exact state
privately before any repair; never assume it failed before storing material.
Existing mount configuration is not automatically amended. Run only one
operator import at a time. Capture a new encrypted backup through the 0107
runbook after real import; this slice does not rotate or qualify backups.

## Local Qualification

Set `SIGNAL_INTEGRATION_CONFIG_FILE` to a separate protected synthetic scope for
these local labs, not the real deployment configuration. The complete placeholder
schema is exercised in `tests/conftest.py`; no provider account is contacted.

The pinned image must already be available; no alternate image is used.
The qualification creates a fresh local TLS/Raft/audit container with bounded
memory, readonly root, no new privileges and only an ephemeral loopback port.
Its temporary generated keys and fixture values are not provider credentials.
Narrow config/TLS mounts and disposable anonymous data/audit volumes are the
only mounts. It is not the persistent deployment profile's egress qualification.
Exact run-label cleanup removes only the new container and its anonymous volumes.

```sh
.venv/bin/python -m pytest tests/tooling/test_integration_connector_secrets.py tests/tooling/test_integration_secrets.py -q
.venv/bin/python scripts/qualify_integration_connector_secrets.py
.venv/bin/python -m ruff check scripts/integration_connector_secrets.py scripts/qualify_integration_connector_secrets.py tests/tooling/test_integration_connector_secrets.py
npm test
```

Do not use the local pilot, synthetic owner accounts or a fake successful
callback for real provider installation. Real owner-bound authorization, exact
state/PKCE, current shared egress and recovery authority must be composed and
qualified before enabling the applications.
