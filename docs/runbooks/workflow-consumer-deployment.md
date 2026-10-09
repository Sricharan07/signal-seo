# Workflow Consumer Deployment Runbook

Status: **QUALIFIED PACKAGE CONTRACT; NO APPROVED PRODUCTION DEPLOYMENT**.

## Purpose

Build, inspect, configure, start, drain, and replace the workflow command consumer
without granting it crawler, repository, CMS, deployment, or other external-write
authority. This runbook does not authorize a pilot deployment. Production release
still requires signed image provenance, SBOM/license inventory, vulnerability
triage, real topology validation, monitoring, backups, and release approval.

## Required Inputs

- Reviewed repository state and a passing complete verification run.
- An image repository plus immutable registry digest. Tags alone are rejected by
  the Compose shape.
- A private pre-created Docker network for PostgreSQL and Temporal traffic.
- Separate `signal_scheduler` and `signal_workflow` DSN files, mode 0600 or 0400,
  each specifying `sslmode=verify-full`, the correct role, database name, server
  identity, and `/run/secrets/database_root_ca` where required by the driver.
- Database CA, Temporal CA, Temporal client certificate, and owner-only client key
  files.
- Exact Temporal address, namespace, TLS server name, and a unique lowercase
  consumer key.
- A compatible, independently deployed `CrawlSite` task-queue worker. This
  repository does not yet provide that deployable worker.

Do not place passwords, DSNs, private keys, or tokens in a `.env` file, shell
history, Compose environment value, image layer, build argument, log, or support
message. The Compose interpolation variables ending in `_FILE` are local source
paths to secret files, not the secret contents.

## Build Qualification

First run the invocation-owned authority-free lab:

```sh
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
```

For a release build, derive the reviewed source-bundle identity using the same
`image_source_hashes()` and `release_identity()` functions as the lab. Pass that
non-secret identity as `SIGNAL_RELEASE` to the Dockerfile. Build for every intended
platform with provenance and SBOM enabled, publish to the approved private
registry, sign the resulting digest, scan it, and record license/vulnerability
triage before setting deployment variables. Those publication steps are not yet
automated or qualified in this repository.

Never substitute `latest`, a branch tag, a locally cached name, or a digest copied
from unreviewed output. Verify that the published image's
`org.opencontainers.image.revision` label equals the approved source identity.

## Preflight

1. Keep production command ingress and external writes disabled unless separately
   approved. Confirm migrations through 0019 and the function-only scheduler and
   workflow grants.
2. Verify PostgreSQL and Temporal TLS names from the deployment host. Do not enable
   `SIGNAL_WORKFLOW_CONSUMER_INSECURE_LOOPBACK` in the Compose manifest.
3. Verify each secret source is an absolute regular file with correct ownership and
   no group/world permission on DSNs or the Temporal client key.
4. Verify the control network is private, is not shared with untrusted build/crawl
   sandboxes, and provides no database/Temporal administrative endpoint.
5. Resolve the selected image by digest and verify signature, SBOM, provenance,
   vulnerability disposition, and source release. This gate is currently blocked
   because those release artifacts have not been produced.
6. Render and review the Compose configuration without printing secret contents.
   Confirm there are no ports, host/Docker-socket mounts, added capabilities,
   privileged mode, or insecure environment settings.

## Start And Observe

Start only after every preflight gate is recorded. Expected lifecycle output is:

```text
consumer_started
consumer_draining
consumer_stopped
```

Normal envelope progress remains `claimed`, `workflow_admitted`,
`temporal_start_confirmed`, `workflow_start_recorded`, and `delivered`. Route JSON
lines only to approved access-controlled storage. A container health result of
`healthy` means Temporal startup and a recent clean scheduler cycle; it does not
prove the separate crawl worker, artifact store, or production crawl path.

Readiness reasons:

| Reason | Meaning | First safe action |
| --- | --- | --- |
| `starting` | Runtime has not completed Temporal startup | Wait only within the bounded startup window; then inspect sanitized lifecycle output |
| `awaiting_database` | Temporal connected but no scheduler cycle completed | Verify scheduler database reachability, role, TLS, and migrations |
| `dependency_unavailable` | Latest cycle had storage failure or ambiguous publication | Keep writes disabled; inspect durable lease, PostgreSQL, and Temporal state before intervention |
| `stale` | No successful cycle inside the configured health interval | Treat as degraded; verify event-loop, database, and host resource health |
| `draining` | Shutdown was requested and no new claim should start | Allow the active envelope to finish within the configured grace period |
| `stopped` | Process state closed; endpoint normally disappears immediately | Inspect exit and supervisor history; do not infer envelope failure |

## Rolling Replacement

The following is the required future rollout order; it has not been qualified on a
private-pilot host:

1. Preserve the old digest and its compatible rollback window. Never roll back
   forward-only database migrations blindly.
2. Start one new consumer with a distinct worker key and immutable image digest.
3. Wait for its ready probe and sanitized lifecycle event. Do not use liveness as
   readiness.
4. Send `SIGTERM` to the old consumer. Readiness must close before drain.
5. Allow the full 90-second grace period. If it expires, treat the active envelope
   as potentially accepted and rely on its lease plus stable workflow identity;
   never invent a new event or workflow ID.
6. Confirm the old process exited and that no prolonged unknown outcome or lease
   age alert remains before removing its image.

Temporal workflow worker build-ID rollout is separate and still unimplemented.
Do not infer worker compatibility from successful consumer replacement.

## Rollback And Failure

- A startup configuration error exits 2 without a traceback. Correct the missing
  input; never inject fallback credentials or disable TLS.
- An unclassified startup/runtime error exits 1. The supervisor may restart the
  same immutable release, but repeated failure requires pausing dispatch and
  investigation rather than an unbounded restart loop.
- If termination exceeds the grace period, preserve the outbox lease and reconcile
  PostgreSQL/Temporal state. Do not force acknowledgement.
- Application code may return to a previously qualified digest only when it is
  compatible with the current forward-only schema and workflow histories. Data
  migrations are never downgraded as an image rollback shortcut.
- Keep the previous image and evidence until no in-flight consumer operation needs
  it and the retention policy permits removal.

The current package lab proves local container mechanics only. Any real deployment
must create a new evidence record with exact host profile, image digest, release
artifacts, configuration checks, dependency health, restart/drain results, and
cleanup or ongoing ownership.
