# Slice 0084: Idempotent GitHub Pull-Request Creation

Status: **INTERNAL WRITE PATH IMPLEMENTED AND LOCALLY QUALIFIED; LIVE GITHUB NOT EXECUTED; R2 PARTIAL**.

## Objective And Tier

Turn one exact owner-approved 0083 technical revision into at most one
reconciled GitHub pull request. This is **security-critical**: Git objects,
a branch, and a PR can cause remote side effects even without a merge.
[ADR-0090](../adr/0090-journal-git-objects-before-pr.md) records the
one-repository write-token, content-addressed, journal-before-GitHub design.

## Implemented Boundary

The scoped rebase onto `d935bbe` renumbers the operation migration to `0054`.
[ADR-0092](../adr/0092-repository-bound-github-write-profile.md) adds the separate
typed repository-write profile: exact GitHub origin and repository/token,
closed GET/POST routes, 128 KiB request, 256 KiB response and five-second timeout.
No existing profile is widened; JSON, scope, credentials and forbidden-route
checks precede network dispatch. Migration `0001` through `0052` is untouched.
The [stack-rebase evidence](../evidence/0084-stack-rebase.json) records the new
full gate. Pre-dispatch origin deferral waits at most five seconds with the same
egress identity and final fence validation; ambiguous responses never retry.

- Migration `0054` (after `0053`) adds a site-scoped operation with a unique revision and
  deterministic branch, immutable intent hash, journal acknowledgement, short
  lease with monotonic fence, step state, and append-only events. Narrow
  functions recheck the selected owner session, current recovery generation,
  decision epochs, verified site, one active binding and observed extension,
  passed candidate receipt, latest reviewed recipe release, and independent
  denial tombstones. All operation tables force RLS and runtime roles have no
  direct table rights.
- A separate encrypted, signed, hash-chained write-intent stream is provisioned
  on the independent journal cluster. The same operation ID and intent body
  replay exactly; different intent under one ID conflicts. The receipt is
  acknowledged in the primary database before any GitHub API call. No token
  or repository content is in the journal.
- The adapter obtains a new one-repository `contents:write` and
  `pull_requests:write` token only for this operation. The read adapter remains
  read-only. Every GitHub request passes through connector shared egress; the
  write transport validates exact origin, route, query, headers, and JSON body.
  Its fetcher checks the current fence and dispatch permit immediately before
  mutation. It cannot update any ref, push to the protected base, merge,
  deploy, edit workflows, or reach a secrets API.
- After journal acknowledgement, a fresh exact checkout reconstructs the
  sealed one-file patch. Source and result hashes, protected-path scope,
  candidate build digest, complete base Git tree, target tree, and deterministic
  one-parent commit are verified. Any repository workflow file blocks this
  initial subset until its trigger and secret behavior is certified. The
  branch name is `signal/<operation UUID>` and the PR targets the selected
  protected base. A duplicate or conflicting branch/PR is never claimed.
- A mutation with a lost response remains `OUTCOME_UNKNOWN`. A replacement
  worker with a new fence may only read-reconcile the exact tree, commit,
  branch, or PR; an absent provider object never authorizes blind recreation.
  The recovery intent specifies a separate inverse-patch PR, a three-way
  comparison against base and candidate, and owner review on overlap.
- The Changes view reads the operation and independent-journal receipt through
  the same-origin server boundary. It shows the PR link, state, revision and
  operation identities, expected tree, and journal hash. `opened` never says
  deployed or live verified. Inbox approval alone still makes no write.

## Qualification

The following gate must pass on the completed branch; exact results are in
[the evidence record](../evidence/0084-idempotent-pr-creation.json).

```sh
.venv/bin/python scripts/run-database-tests.py
.venv/bin/python scripts/run-authority-journal-tests.py
.venv/bin/python scripts/run-candidate-sandbox-tests.py
.venv/bin/python scripts/run-consumer-tests.py
.venv/bin/python scripts/run-crawler-network-tests.py
.venv/bin/python scripts/run-page-attempt-tests.py
.venv/bin/python scripts/run-temporal-tests.py
.venv/bin/python scripts/run-workflow-consumer-image-tests.py
.venv/bin/python scripts/openbao_lab.py
.venv/bin/pytest -q tests/api tests/identity tests/tooling tests/connectors
.venv/bin/ruff check apps services scripts tests database/migrations
.venv/bin/ruff format --check apps services scripts tests database/migrations
.venv/bin/python -m pip check
npm test
gitleaks git --log-opts="slice/0083-inbox-review..HEAD" .
```

Real PostgreSQL exercises operation preparation, exact replay, journal gate,
fence loss, unknown outcome, reconciliation, forced RLS, and read projection.
The separate-cluster journal lab exercises durable acknowledgement, replay,
conflicting identity, reader denial, and source restore. A GitHub test double
behind real shared-egress admission checks the fixed branch route and recorded
credentialed connector request; adapter fakes cover each lost-response step,
409/422, 429, and 5xx. Existing candidate-sandbox qualification retains
protected-path, timeout, OOM, crash, output, and forbidden-network coverage.

## Live Qualification And Limits

Live GitHub App, test repository, and real customer repository writes are
**NOT_EXECUTED**. The owner must supply a dedicated selected-repository App
with `contents:write` and `pull_requests:write`, protected base and safe
branch/ruleset behavior, an exact Eleventy static homepage whose source
matches verified crawl evidence, the OpenBao App key, an independent journal
cluster and managed signing/encryption keys, private connector egress, and a
reviewed technical recipe release. A repository with workflow files is
currently unavailable rather than guessed safe. Production write composition,
real-provider ambiguity windows, and primary-restore replay of write intents
remain release gates. No PR is a deployment or live-result claim; 0085 adds
check/deployment observation and independent live verification.
