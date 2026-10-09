# Slice 0033: Workflow Consumer Packaging And Health

Status: **IMPLEMENTED AND CONTAINER-QUALIFIED; IMAGE PUBLICATION, PRODUCTION
DEPLOYMENT, AND THE CRAWL WORKER REMAIN UNAVAILABLE**.

## Outcome

Signal now has a release-bound OCI package and private operational health contract
for the durable workflow command consumer. The package is qualified as a real
container with no network or credentials, but it has not been published, signed,
or deployed to a customer or private-pilot environment.

| Boundary | Implemented behavior |
| --- | --- |
| Release identity | Exact `sha256:<64>` source-bundle identity in runtime config, OCI label, and health payload |
| Liveness | Loopback-only fixed-schema probe while the process has not stopped |
| Readiness | Temporal connected, running, recent successful scheduler cycle, no latest storage/ambiguity failure |
| Drain | Readiness closes before the active envelope drains; `SIGTERM` uses the existing cooperative stop |
| Secrets | Production DSNs load only from bounded owner-only regular files opened without following symlinks |
| Image | Digest-pinned Python base, runtime-only dependencies, non-root user, exec-form entrypoint and health check |
| Manifest | Digest-only image shape, no published ports, private network, read-only root, dropped capabilities, bounded resources/logs/restarts |
| Qualification | Real Docker build and six authority-free image, Compose, negative, restart, health, and shutdown scenarios |

This implements a bounded part of Revision 3.2 sections 6.4, 27.2 through 27.4,
28.1, 28.3, 29.1, 29.2, 29.5, 30.1, 30.2, and 30.5. It advances the first item
in the Core V1 M1 closure sequence. It does not complete private-pilot deployment,
full observability, the production crawl worker, or any external SEO operation.

## Health Semantics

`WorkflowConsumerHealth` owns a four-phase state machine:

```text
starting -> running -> draining -> stopped
```

The liveness probe is independent of provider health so a dependency outage does
not induce a restart loop by itself. The readiness probe remains closed until a
real scheduler database scan completes after Temporal connection. A cycle with a
storage failure or ambiguous publication closes readiness; a later clean cycle
restores it. A successful cycle expires after the configured staleness interval.

The process serves only `GET /health/live` and `GET /health/ready` on literal
loopback. Responses contain the release, phase, closed reason, total cycles, and
consecutive failed cycles. They contain no DSN, event payload, URL, tenant/site/
command identifier, exception, or provider response. Unsupported methods, routes,
oversized requests, slow requests, and malformed request lines fail with fixed
responses and `Cache-Control: no-store`.

The image health check uses readiness, not liveness. The Compose manifest does not
publish the listener. Docker or another supervisor can inspect it inside the
container network namespace.

## Package And Deployment Contract

`deploy/workflow-consumer/Dockerfile` uses Python 3.12.14 slim Bookworm at an exact
multi-platform manifest digest. The image installs only seven exact consumer
runtime packages rather than the development/API dependency graph. A build fails
unless it receives the exact source-bundle release identity. The runtime executes
as UID/GID 10001 and creates no writable application path.

`deploy/workflow-consumer/compose.yaml` requires an explicit repository and
64-character image digest. It does not build in place, publish ports, mount the
Docker socket, request privileges, or enable insecure-loopback mode. Database DSNs,
database CA, and Temporal mTLS files are mounted as named secrets. The caller must
create the private control network and secret files; the manifest never generates
or defaults authority.

The source-bundle release is distinct from the registry's image digest. Both are
required: the former ties runtime state to reviewed inputs and the latter fixes the
exact distributed image. Neither is yet a signed release manifest.

## Real Image Lab

Run:

```sh
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
```

The lab computes the release from exact build inputs, builds the image, and runs
only `tests/container` with synthetic configuration. It verifies image metadata,
non-root import under a networkless read-only runtime, fixed failure without
mounted authority, health denial before a successful cycle, later Docker health,
zero-exit `SIGTERM`, and termination after a bounded external-supervisor retry
count.
Every test container is resource-bounded and labeled for exact cleanup. The image
and containers are removed even on test failure.

## Verification

The completed slice is verified by the commands in the root README and the
dedicated image command above. All 493 non-database Python cases, 171 API cases,
390 real PostgreSQL cases, three real Temporal cases, one joint
PostgreSQL/Temporal consumer case, and six real Docker image cases pass. The five
Keycloak and seven OpenBao real-provider scenarios remain green. All 14 repository
cases and documentation checks across 88 Markdown files pass; Ruff lint/format
cover 117 Python files, and dependency consistency passes.

Final image test results, local image identity, base digest, release identity,
cleanup state, and source hashes are in
[0033-workflow-consumer-image.json](../evidence/0033-workflow-consumer-image.json).
Current regression evidence is in
[0033-temporal.json](../evidence/0033-temporal.json),
[0033-consumer.json](../evidence/0033-consumer.json), and
[0033-postgresql.json](../evidence/0033-postgresql.json). Every record reports
`production_authority` false and completed cleanup.

## Explicit Limits

- No image was pushed to a registry, signed, attested, installed, or run with real
  credentials. The Compose contract is statically tested but no private-pilot host
  deployment is claimed.
- Docker Scout required a Docker-account login that was unavailable. There is no
  vulnerability-scan result; dependency/image scan, SBOM, provenance, license
  inventory, signing, and triage remain release blockers rather than silently
  passing.
- The package runs only the command consumer. It does not register
  `CrawlSiteWorkflow`, execute crawl activities, route Temporal worker build IDs,
  or prove a task-queue poller is healthy.
- Readiness exercises the workflow-role database only when a command is processed.
  An empty clean cycle proves Temporal startup and scheduler-database access, not
  every downstream dependency.
- JSON logs and probes are instrumentation surfaces, not a selected metrics/log/
  trace backend. There is no alert manager, dashboard, SLO evidence, support
  bundle, or on-call integration.
- No crawler, artifact store, model, approval, GitHub, GSC, Telegram, CMS, undo,
  customer data, production write, or production authority is added.

See [ADR-0033](../adr/0033-release-bound-consumer-health-and-packaging.md) for the
decision and the [deployment runbook](../runbooks/workflow-consumer-deployment.md)
for operator preflight, rollout, drain, and rollback boundaries.
