# ADR-0033: Bind Consumer Health And Packaging To An Immutable Release

- Status: Accepted
- Date: 2026-09-09
- Owners: Workflow runtime, reliability, and deployment

## Context

The workflow command consumer already drains an active envelope on process signals
and has jointly qualified command delivery against PostgreSQL and Temporal. It was
still only a source-tree entrypoint. An operator or container supervisor could not
distinguish a live process from one that had never connected, could not tell whether
database scans were succeeding, and had no immutable image or bounded deployment
contract to run.

A process-only liveness check is insufficient. The consumer can remain alive while
its scheduler database is unavailable or its delivery path repeatedly has unknown
outcomes. Conversely, a transient command rejection does not mean the process
itself is unhealthy. Health payloads also cannot expose DSNs, event payloads,
provider errors, URLs, or high-cardinality customer identifiers.

Packaging introduces authority and supply-chain risks. Production database
credentials must not be ordinary environment variables, image tags must not stand
in for immutable identities, the health listener must not become a public API, and
container convenience must not silently enable insecure loopback transport.

## Decision

Add a closed in-process health state with separate `live` and `ready` probes.
Liveness means the process has not stopped. Readiness requires all of the following:

- the configured Temporal client connected within its bounded startup timeout;
- the consumer entered its running phase;
- at least one scheduler-backed delivery cycle completed without storage failure or
  ambiguous publication outcome; and
- the latest successful cycle remains inside a bounded staleness interval.

Readiness closes before cooperative drain. A later failed or ambiguous cycle closes
readiness immediately; a subsequent clean cycle restores it. The loopback-only HTTP
contract returns a fixed schema with release, phase, reason, and aggregate cycle
counters. It accepts only bounded `GET /health/live` and `GET /health/ready`
requests, exposes no metrics or customer data, and is not published by the
deployment manifest.

Require every runtime to carry a `sha256:<64 lowercase hex>` source-bundle release
identity. Embed the same identity in the OCI label, process configuration, and
health response. This is a code/config identity, not an image registry digest or a
Temporal workflow-worker build ID.

Build the consumer from a digest-pinned Python base and an exact runtime-only
dependency graph. Run as fixed non-root UID/GID 10001, write no bytecode, use a
read-only root filesystem, drop all Linux capabilities, set no-new-privileges, and
provide an exec-form health check and `SIGTERM` stop signal.

Provide a Compose deployment contract that accepts the image as explicit
repository plus `sha256` digest, publishes no port, joins only a named private
control network, mounts credentials as read-only secret files, uses bounded CPU,
memory, processes, logs, and temporary storage, restarts under an external
supervisor with at most five automatic failed-process retries, and permits 90
seconds for cooperative drain. Outside explicit
literal-loopback development mode, database DSNs are accepted only from absolute,
owner-only regular files opened without following symlinks. Existing database and
Temporal TLS requirements remain mandatory.

Qualify the built image in an invocation-owned Docker lab. The lab uses no network
or credentials when executing the image, verifies non-root/read-only behavior,
proves authority-free startup fails closed, exercises not-ready to ready health,
confirms `SIGTERM` exit zero, and proves an external restart policy stops after its
bounded failed-process retry count. It removes only its labeled containers and
exact image.

## Alternatives

- Report readiness immediately after process start. Rejected because it would hide
  failed dependency initialization and database scan failure.
- Make liveness depend on PostgreSQL or Temporal. Rejected because dependency
  outages should close readiness while leaving the process available for diagnosis
  and graceful recovery.
- Expose Prometheus metrics or a public health port in this slice. Deferred because
  OpenTelemetry, a selected backend, bounded labels, authentication, and alert
  ownership require a separate observability slice.
- Put DSNs directly in Compose environment variables. Rejected because environment
  inspection and crash tooling can expose reusable credentials.
- Permit mutable image tags in the deployment manifest. Rejected because an
  operator could unknowingly run code different from the reviewed release.
- Package the Temporal `CrawlSite` worker together with the consumer. Deferred
  because the production worker still lacks a scope-enforcing crawler, artifact
  persistence, and build-ID rollout qualification. Packaging a synthetic executor
  would misrepresent capability.
- Treat the container image lab as a production deployment. Rejected because it
  has synthetic authority-free execution, no registry publication/signature, no
  real private-pilot topology, and no monitoring backend.

## Consequences

- The consumer now has a supervisor-consumable, fail-closed readiness contract and
  an immutable release identity visible in sanitized operational state.
- Production-shaped image and Compose definitions are reviewable and tested rather
  than left to undocumented operator choices.
- Cooperative shutdown closes readiness before exit, while stable leases and
  workflow identities retain existing redelivery semantics after restart.
- The ready probe covers Temporal startup plus recent scheduler cycles. It does not
  prove the separate workflow task queue has a healthy `CrawlSite` worker, exercise
  the workflow-role database path when no command exists, or replace end-to-end
  alerts.
- The local image was not published, signed, deployed, or assigned production
  authority. SBOM/provenance publication and authenticated vulnerability scanning
  remain release gates; the available Docker Scout client required an unavailable
  Docker account and therefore produced no vulnerability result.

## Verification

Unit tests cover release and phase validation, stale and failed cycles, loopback
binding, bounded HTTP methods/routes, ready denial, stopped liveness, secret-file
permissions/symlink rejection, health/runtime release matching, observer failure,
and signal draining. Repository tests parse the Dockerfile, dependency graph,
Compose manifest, and CI gate.

The real Docker lab builds the digest-pinned image and executes six positive,
negative, and failure scenarios with no network, no credentials, a read-only root,
dropped capabilities, bounded resources, and invocation-owned cleanup. Existing
PostgreSQL/Temporal consumer, Temporal replay, database, identity-provider, API,
and repository suites remain regression gates.
