# Slice 0121: Test Callback Metadata and Binding Failure

Classification: **security-critical callback identity and connector authority**.
Status: **REPAIR LOCALLY QUALIFIED; LIVE REPAIR PENDING**.

Historical repair checkpoint. Subsequent deployment and real provider results
are recorded in [0122](0122-dedicated-provider-callback-qualification.md).

This repairs the real failures recorded in
[0120](0120-live-test-connector-runtime.md), without removing callback origin,
state, correlation-cookie, MFA, protected-branch or shared-egress guards.
Revision 4.0 section 4/19 and Revision 3.2 identity/connector requirements remain
unchanged. Existing ADR 0094 and ADR 0101 apply; no new authority model is introduced.

## Repairs

Next's custom server uses its configured hostname/port for absolute request URL
metadata. The dedicated deployment previously supplied its private TLS listener
address, `dashboard:8443`, causing both connector callbacks to reject the real
public origin before provider exchange. The dedicated server now requires the
exact configured test origin and supplies its public hostname and port 443 to
Next. The independent HTTPS listener remains port 8443, with the same private
certificate, non-root image, resource limits and private network. Unknown,
absent, HTTP, private, path/query and alternate-port configurations fail startup.
The callback origin checks are unchanged; no arbitrary Host/forwarded-header
trust is introduced.

GitHub's read inspection can legitimately observe `protected: false`. A binding
previously reached the final snapshot conflict without recording its provider
failure, leaving an unexplained `prepared` binding. It now maps that observation
to the existing `GITHUB_REPOSITORY_STATE_REJECTED` failure inside the durable
failure path. It never creates an active binding. An exact failed idempotency
retry returns the recorded failure without another provider call. Changing the
real repository's protection is still an independent owner operation.

## Verification

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python -m pytest tests/api tests/identity tests/tooling -q
npm test
.venv/bin/python -m ruff check services/control_plane/src/signal_core/github_read_binding.py tests/control_plane/test_github_read_binding.py
.venv/bin/python -m ruff format --check services/control_plane/src/signal_core/github_read_binding.py tests/control_plane/test_github_read_binding.py
```

All 874 real PostgreSQL cases passed in the disposable isolated lab. The 1,442 API/identity/tooling
cases, 41 repository and 153 dashboard cases, TypeScript/build and changed
Python lint/format passed. The added PostgreSQL checks exercise failed initial
unprotected inspection, durable failure and replay, followed by protected
positive binding and later authority/provider failures. Static/pure custom-server
checks supplement, but do not replace, a real TLS/browser callback qualification.

Live images have not been replaced by this slice yet. Slack and GSC connections
and GitHub protected binding remain incomplete. Repository visibility, Google
ownership verification, new provider consent and signed Slack interactivity are
not implied by this repair. No production publishing authority or Gmail access.
See [evidence](../evidence/0121-test-callback-metadata-and-binding-failure.json).
