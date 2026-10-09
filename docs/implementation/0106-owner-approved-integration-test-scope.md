# Slice 0106: Owner-Approved Integration Test Scope

Classification: **security-critical operational policy**. Contract: Revision 4.0
sections 4, 12 and 19; Revision 3.2 sections 2, 29 and 30.

**Implemented: recorded human exception only. No integration was enabled.**

The owner approved complete testing and amendment of the repository-specific
default prohibition after a question naming `signal-test.example.invalid`, private
administration, no customer data and the exact test repository.
[ADR-0094](../adr/0094-owner-authorized-integration-testing.md) records that
authorization and its limits. `AGENTS.md` retains private testing by default and
adds the dedicated exception, without changing any accepted specification,
runtime authority, provider permission or cloud firewall.

The exception permits necessary HTTPS test application/login/callback exposure,
not administrative ports, customer data, public synthetic accounts, reuse of the
disposable pilot with live credentials, or production readiness. Existing
website/mail and unrelated provider projects remain untouched. Each subsequent
runtime boundary still needs its own real-provider qualification.

Verification: `npm test` and the staged secret scan. Repository checks protect
all four accepted specification hashes, structured examples and documentation
links. No application, database or provider behavior changed, so their runtime
suites are not claimed. The [evidence](../evidence/0106-owner-approved-integration-test-scope.json)
records the policy-only scope and explicit unavailable integrations.
