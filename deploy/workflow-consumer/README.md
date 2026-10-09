# Workflow Consumer Package

This directory contains the qualified package shape for Signal's workflow command
consumer:

- `Dockerfile` builds a release-bound, non-root image from a digest-pinned base;
- `requirements.txt` is the exact runtime-only dependency graph; and
- `compose.yaml` defines private secret delivery, resource bounds, health,
  supervision, and graceful shutdown without publishing ports.

The package is not a released or deployed service. It contains no production
credentials and does not include the separate `CrawlSite` workflow worker. Follow
the [deployment runbook](../../docs/runbooks/workflow-consumer-deployment.md) and
[Slice 0033 implementation record](../../docs/implementation/0033-workflow-consumer-packaging.md)
for qualification evidence and current blockers.
