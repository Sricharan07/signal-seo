# Governance

Signal is maintained by Sricharan ([@Sricharan07](https://github.com/Sricharan07)),
who has final say on direction, releases and merges.

## How decisions are made

- The product contract is the current specification revision. Material scope
  changes need a new explicit revision and a superseding ADR (see `docs/adr/`).
- Safety invariants in [AGENTS.md](AGENTS.md) are not up for trade-offs. Changes
  that weaken them are declined.
- Everything else is decided in issues and pull requests, in public.

## How changes land

Every pull request needs the maintainer's review (see `.github/CODEOWNERS`), a
passing Quality workflow, and a signed [CLA](CLA.md). Security-critical changes
also need the records listed in [CONTRIBUTING.md](CONTRIBUTING.md).

## Labels

- `good first issue`: small, well-scoped work for new contributors.
- `help wanted`: the maintainer would welcome a pull request.
- `needs-triage`: not reviewed yet.
- `security-critical`: touches authority, secrets, egress or external writes.
- `area/*`: dashboard, api, workers, connectors, database, self-host, docs.
