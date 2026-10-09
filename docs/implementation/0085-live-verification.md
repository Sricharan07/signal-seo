# Slice 0085: Delivery Observation and Independent Live Verification

Status: **INTERNAL OBSERVATION PATH IMPLEMENTED AND LOCALLY QUALIFIED; LIVE GITHUB AND SITE NOT_EXECUTED; R2 PARTIAL**.

## Objective And Tier

Observe the customer-controlled delivery of an opened 0084 PR and verify the
exact served result without merging or deploying. This is **security-critical**:
provider credentials, egress, current authority, and immutable delivery evidence
are involved. [ADR-0091](../adr/0091-independent-live-observation.md) distinguishes
independent page verification from signed delivery certification.

## Implemented Boundary

- Migration `0055`, following `0054`, adds site-scoped bounded observation
  attempts and append-only canonical receipts. Both tables force RLS; runtime
  roles have no direct table access. Narrow security-definer functions recheck
  current owner authority, active site, verified origin, binding/extension,
  epochs, and external recovery generation. Receipt digests, exact operation
  and revision identity, sealed recovery plan, and committed egress provenance
  are checked before completion. A missing receipt remains outcome unknown.
- The read adapter gets a one-repository token through the existing OpenBao
  credential boundary. Its only POST obtains a narrowed installation token;
  subsequent routes are fixed GitHub GETs for repository, PR, checks, commit
  statuses, merged commit, deployments, and deployment statuses. Current
  authority is checked before every request. No merge/deploy/update-ref,
  workflow, secret, other-repository, or default-branch write route exists.
- Checks count only provider-observed records. A merged PR must identify the
  expected head and base repository, exact merged SHA, and full sealed result
  tree. The latest selected-environment deployment must match that SHA and
  the explicitly selected trusted actor, production/non-transient status,
  timestamps, success status, and verified root origin. A later unrelated
  deployment cannot be hidden by selecting an older matching one.
- A GET uses the existing crawl profile, exact origin, robots snapshots,
  leased frontier, global origin permit, pinned peer, zero redirects, and
  bounded timeout/body. No browser or credentials are used for page fetching.
  Postconditions are derived from the exact sealed manifest, not a new draft:
  titles, descriptions, image alt text, canonical, JSON-LD, or removed broken
  internal links. Semantic values and the entire result digest must match.
- Old source bytes are inconclusive/stale. Missing or differently changed
  postconditions are inconclusive; noindex, harmful canonical drift, and
  404/410/5xx are regressed. Robots denial, redirect rejection, timeout,
  provider failure/rate limiting, and lost observations never report success.
  A second GitHub observation after the fetch confirms deployment identity.
- Attempts are bounded to 24 per operation with a five-minute lifetime and
  exponential 30-second to one-hour backoff. Completed identities replay exact
  receipts without network; unresolved identities do not blindly refetch.
  After expiry/backoff a new read-only attempt may be explicitly scheduled.
- The same-origin read API/BFF validates schema, site, canonical receipt bytes
  and digest. Changes matches operation and sealed revision identities and
  shows only evidence-backed PR/check/merge/deploy/live stages, observed
  postconditions, fetched and receipt digests, raw evidence, and the sealed
  recovery plan. Invisible Unicode is visible. No review control grants
  merge/deploy or automatically executes recovery.

## Operation And Qualification

The scoped rebase onto `d935bbe` preserves connector and autonomy foundations.
GitHub observation selects the typed `github_rest` read profile; independent live
verification selects uncredentialed `crawl_page` GET. Receipt admission requires
those exact committed profile identities. It cannot borrow the repository-write
profile introduced by ADR-0092. Migrations `0001` through `0052` are unchanged.
The [stack-rebase evidence](../evidence/0085-stack-rebase.json) records the full
gate on the integrated branch; the earlier evidence preserves pre-rebase results.

`observe_github_delivery` is an internal composition entrypoint. The caller
supplies the exact opened operation, a new UUID4 attempt identity, selected
environment and trusted actor, current session/generation, OpenBao credential
handle, and site-bound GitHub and fresh-root crawl egress contexts. It returns
only after receipt commit; it has no public write or observation-start endpoint.
The local pilot composes the authenticated read projection. Scheduled production
composition follows with the weekly loop, not an implicit background poller.

Exact commands, counts, and provider limitations belong in
[the evidence record](../evidence/0085-live-verification.json). The full gate is:

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
.venv/bin/ruff check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/ruff format --check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/python -m pip check
npm test
gitleaks git --log-opts="slice/0084-idempotent-pr-creation..HEAD" .
```

Real PostgreSQL plus the GitHub double behind real shared egress exercises
exact success, durable replay, bounds/backoff, immutable receipts, stale and
absent pages, wrong deployment SHA, timeout, robots-denied/no-dispatch, loss,
expiry, revocation, and RLS. The isolated crawler-network lab fetches real HTTP
bytes and checks exact/stale pages, cross-origin redirects with no second
resolution, and timeout. Contract tests cover all six recipe families,
duplicate metadata, malformed HTML, indexability/canonical regression,
provider denial/rate limit/unavailability, and lost response.

## Limits And Live Resources

Only the existing Eleventy `index.html` static-homepage subset is supported;
no dynamic routing, multi-page candidates, or CDN byte rewriting is guessed.
Every receipt sets `delivery_certified: false`: Revision 3.2 section 21.5's
signed delivery contract is still required for certified automated delivery.
Production observation scheduling, signed deployment receipt ingestion,
browser verification, real-provider qualification, and revert-PR mechanics
remain unavailable. No new authority, repository write, merge, or deploy is
enabled. Restore replay and full R2 release admission remain separate gates.

Live GitHub and live site qualification are **NOT_EXECUTED**. The owner must
provide a dedicated App installed only on the selected test repository, with
contents/pull-requests/checks/commit-statuses/deployments read permissions
(and the separate already-required PR write permissions for 0084); its App key
in OpenBao; a protected bound base; a reviewed recipe and approved exact
candidate; and an owner/customer merge and delivery system reporting GitHub
deployment/status identities. Select the production environment and trusted
deployment actor ID. Provide a verified public HTTPS origin whose robots policy
admits Signal and whose served homepage exactly matches the sealed artifact,
plus private shared-egress and artifact services. Signed deployment keys and
receipts are additionally required for delivery certification, not simulated.

Provider contract references: [GitHub deployments](https://docs.github.com/en/rest/deployments/deployments),
[deployment statuses](https://docs.github.com/en/rest/deployments/statuses), and
[commit statuses](https://docs.github.com/en/rest/commits/statuses). The adapter
retains the repository's pinned API-version boundary rather than changing it.

Operational owner: the control-plane observer and the customer's deployment
owner. On inconclusive/regressed/unknown results retain evidence, escalate to
the owner, and preserve the sealed conflict-aware inverse patch; never force
rollback. Destructive downgrade of migration `0055` is disabled; use reviewed
forward recovery without deleting receipts.
