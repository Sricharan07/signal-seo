# Slice 0108: Private Connector Configuration

Classification: **security-critical operational foundation**. Contract: Revision
4.0 sections 4, 12 and 19; Revision 3.2 sections 4 and 10.2. This uses the private
store selected by [ADR-0095](../adr/0095-private-persistent-integration-secrets.md)
and the test scope in [ADR-0094](../adr/0094-owner-authorized-integration-testing.md).
Neither accepted specification nor product authority is changed.

**Implemented and qualified: operator-only app-configuration import. Provider
registration is partial; live authorization and callbacks are NOT_EXECUTED.**

## Import Boundary

`scripts/integration_connector_secrets.py` accepts the dedicated GitHub App's
unencrypted RSA key, separately downloaded Google Web-client JSON, or hidden
interactive Slack app secrets. Downloads must be immediate, nonsymlink,
owner-only, single-link, bounded files inside the protected operator directory
outside the repository. Google project, standard endpoints, exact separate
callback, document keys and credentials are validated; duplicate JSON fields
and broader redirects/origins are rejected. No API token is put in arguments,
output or git.

The helper uses existing strict localhost/private-CA transport and audited
OpenBao. It never changes an existing mount's configuration or replaces an
existing reader policy. Both secret and new reader policy use CAS-zero; the
policy itself requires CAS for subsequent mutation. The exact path has read-only
access, no default policy and a five-minute verification token. Reads, denied
writes/deletion/other-path/token creation and revocation are checked. Tokens are
revoked even when verification fails. Unknown write outcomes are not retried.

Fresh KV-v2 mounts can briefly report their documented upgrade state. Only
read-only readiness polling of that exact response is allowed, with a ten-second
deadline. No other error is retried and no secret write occurs before readiness.
This behavior follows the [pinned provider source](https://github.com/openbao/openbao/blob/v2.6.1/builtin/logical/kv/upgrade.go).

No workload token, site binding, owner authorization, Google refresh token, Slack
bot token or repository write authority is created by this helper. Stored app
configuration does not mean a provider is connected.

## Observed Registration State

These are real Safari observations, not successful Signal-runtime journeys.
This is the 0108 checkpoint; [0109](0109-test-provider-registration.md) records
the subsequent owner approval, real Google imports and GitHub installation/key
import.

- GitHub `Signal Test Placeholder` (App ID `1234567`) has only contents/metadata
  read access. User authorization and webhooks are disabled. Installation is
  prepared for only `example-owner/integration-test`, but not submitted;
  action-time owner confirmation and a private App key are still required.
- Slack `Signal Dev` in `Test Workspace` has only bot `chat:write`, no user scopes, and
  its approved exact redirect is saved as
  `https://signal-test.example.invalid/auth/slack/callback`. It is not installed and
  no bot token or owner/channel binding exists.
- Google project `integration-test` has Search Console API visibly
  **Enabled**. OAuth branding is created with external audience in **Testing**,
  with no test users added. The owner approved both Google policies, read-only
  Search Console, and two separate Web clients with exact callbacks. The
  sign-in client was created for
  `https://signal-test.example.invalid/identity/realms/signal/broker/google/endpoint`.
  Its download is waiting on Safari's download permission and private-storage
  confirmation. The separate GSC client at `/auth/gsc/callback` is not created
  yet. No Gmail API or mailbox scope was enabled.

At this checkpoint none of these new app configurations has been imported into
the actual test store. The Google project was created and billing linked by the
owner; no paid Google resource was created by this slice.

## Qualification

- Positive: 50 Python cases and a fresh real OpenBao 2.6.1 container using the
  existing persistent Raft/TLS/audit profile. Four synthetic configuration paths
  are stored and independently verified under exact reader tokens.
- Negative: invalid/ambiguous/private-file formats, wrong Google project,
  callback, endpoints and broader origins; incompatible or non-CAS existing
  mounts; existing policies; reader mutations, deletion, other-path reads and
  token creation; revoked reads and repeated secret/policy CAS-zero writes.
- Failure: parser/provider errors suppress values, unknown writes are not
  repeated, failed verification revokes the reader, exact mount readiness has a
  deadline, and sealing the disposable store denies reads. Container and its
  anonymous data/audit volumes are removed with exact run-label verification.

The real qualification uses synthetic material only and an ephemeral loopback
port; it does not call GitHub, Google or Slack. It neither restores nor modifies
the persistent test store or its supplied model keys. Initial real checks caught
an incorrect policy-list HTTP verb and the KV-v2 mount upgrade window; both were
fixed rather than masking failed requests. One initial HTTP 500 remained an
explicit failed attempt, not a successful import or a retried write.

Commands: [runbook](../runbooks/integration-connector-configuration.md).
Evidence: [0108](../evidence/0108-private-connector-configuration.json).
The combined operator regression passed 89 Python cases. Ruff and the complete
`npm test` gate passed: 28 repository tests, 223 documentation files, 148 dashboard
cases, TypeScript and production build. The staged secret scan passed.

## Administrative Recovery And Remaining Work

The Mac's previous IPv6 source disappeared and locked out the exact SSH rule.
After the owner temporarily restored that address, the new current source was
allowed in UFW, AWS was replaced with only that new `/128`, a new-source bound
SSH connection succeeded, and the old UFW rule was removed. IPv4 SSH and public
web ports remain closed. The intended private `18200` TLS tunnel was restored;
the temporary `18203` tunnel was removed. The old Mac alias still needs the
owner's sudo removal; it has no VM access.

Private app import is not the missing persistent application composition.
PostgreSQL, real owner/identity and recovery authority, shared egress, GSC owner
API/BFF, DNS/HTTPS ingress, workload authentication and real OAuth qualification
remain absent. The disposable local pilot must not be published or used with
real keys. Production and external-write gates remain closed.
