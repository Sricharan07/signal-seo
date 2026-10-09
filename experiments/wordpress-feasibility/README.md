# WordPress Feasibility Lab

This is an isolated Milestone 0 experiment, **not a production Bridge plugin**.
It exposes no Signal HTTP routes, accepts no customer credentials, and grants no
production capability. SQL transactions cannot undo external hook effects.

## Run

Prerequisites: Node 22 and Docker with Compose v2. The initial run downloads the
digest-pinned images and starts two temporary, resource-limited containers.

```sh
npm ci
npm run test:wordpress
```

The runner generates disposable database passwords, snapshots PHP source into a
temporary directory, mounts it read-only, and creates a uniquely named Compose
project. Its network is internal, no host ports are published, the database uses
tmpfs, and the WordPress volume is discarded on completion. Only this invocation's
resources are removed. Existing Docker projects are not touched. A forcibly killed
runner may leave its project behind; the printed `signal-wp-lab-<random>` name
identifies it. Docker administrators can inspect lab credentials; use synthetic
data only. These are not production secrets-management controls.

The JSON report is written to `.runtime/wordpress-feasibility/latest.json` and is
ignored by Git. It records runtime versions, source hashes, case-level results,
and qualification limits. Reviewed evidence belongs in the
[implementation record](../../docs/implementation/0002-wordpress-feasibility.md).

## Organization

| File | Purpose |
| --- | --- |
| `compose.yaml` | Image digests, isolation, limits, ephemeral storage |
| `src/bootstrap.php` | CLI/environment/database guards and WordPress bootstrap |
| `src/install.php` | Synthetic installation and experimental operation table |
| `src/bridge.php` | Conditional exact patch, atomic receipt, limited inverse patch |
| `src/worker.php` | Separate database connections for concurrent editor/worker tests |
| `src/test.php` | Real-stack tests and machine-readable report |

## Contract Under Test

The prototype locks the actual post row and checks its raw content before calling
WordPress. A unique operation row serializes duplicate requests. A receipt commits
in the same database transaction as the supported content/metadata updates. Errors
roll back both; retries after an uncertain response consult that operation identity.
The experiment uses exact, uniquely matched snippets, not HTML reserialization.

These checks are narrower than production approval/version contracts. The expected
value covers content, not a sealed manifest covering all relevant publication or
policy fields. A snippet is not a certified broken-link recipe. Metadata is limited
to an existing single `_signal_lab_description` row; it is synthetic and has no
rendered SEO semantics. Missing/duplicate rows fail. Metadata creation and metadata
inverse recovery are unsupported. Inverse content recovery is a conservative
unique-snippet operation, not a general merge engine.

Concurrency barriers use process pipes and observation of actual waiting database
queries rather than assuming workers raced after an arbitrary sleep. Tests also
demonstrate that a stale native editor can overwrite a page after Signal commits.
A historical receipt must never be treated as proof of current content.

## Qualification Limits

No Yoast mapping, persistent object cache, CDN, third-party plugin certification,
multisite, localization, authorization, signed permits, independent journals,
durable orchestration, or tenant isolation is implemented here. Image digests pin
a reproducible baseline; they do not assert that the images are the latest or have
passed a production vulnerability review.

Full Milestone 0 and pilot admission remain incomplete. See
[ADR-0002](../../docs/adr/0002-wordpress-lab-boundary.md) before reusing this code.
