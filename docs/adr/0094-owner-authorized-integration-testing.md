# ADR-0094: Owner-Authorized Dedicated Integration Testing

Status: Accepted 2026-09-30 for operator-managed testing only.

## Context

The owner requested complete integration wiring on the closed base VM from
[0105](../implementation/0105-closed-development-base-vm.md). After an explicit
question identifying the dedicated hostname, private administration, no customer
data and exact test repository, the owner answered: "i approve complete testing
and ammend that rule". This is direct human authorization, not source content or
a model-generated grant.

The repository's default ban on public test services prevented server-to-server
provider callbacks. It is an operational working rule, not an exemption from the
accepted product invariants. No accepted specification revision is modified.

## Decision

Amend the working agreement to permit complete, operator-managed integration
testing at `signal-dev.example.com`, using the existing `signal-dev-base` VM,
`example-owner/signal-integration-test`, the disposable Slack `Sample` workspace,
dedicated test identities and provider credentials. Only necessary HTTPS
application/login/callback routes may be publicly reachable. Authentication,
CSRF, exact callback state, provider signature verification, least privilege,
current authority and shared connector egress remain mandatory.

OpenBao, database, Temporal, identity administration, metrics and other
administration remain private. The disposable local pilot and its synthetic
credentials must never be exposed or reused with live provider credentials.
Existing root-domain website/mail and unrelated repositories/projects are not
part of this authorization. Unimplemented routes stay unavailable, not placeholder
success pages. Broader targets, additional paid cloud capacity, production use,
or changed access need a separate explicit authorization.

The exception authorizes the human operator's provisioning and qualification;
it does not create a Signal standing grant, mint model authority, admit a release,
or authorize autonomous external writes. Each enabled boundary still needs its
security-critical implementation record and real-provider positive, negative
and failure tests. Missing credentials, owner input or required topology remains
an explicit blocker.

## Alternatives and Consequences

- Keep all testing private: retains the default rule, but Slack server callbacks
  cannot reach a Mac's loopback endpoint.
- Publish the disposable pilot: rejected; synthetic identities and development
  composition are not acceptable live-provider boundaries.
- Expose the whole VM: rejected; administrative service exposure is unnecessary.

The selected exception enables realistic callback qualification without claiming
that a single VM meets private-pilot or production deployment requirements.

## Verification

`npm test` protects accepted specification hashes and checks documentation and the
existing application gate. Scope and executed results are recorded in
[0106](../implementation/0106-owner-approved-integration-test-scope.md); no runtime
or provider permission is qualified by this policy-only decision.
