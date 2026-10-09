# Slice 0123: Owner-Approved Article Delivery

Classification: **security-critical**, new candidate kind at the write-authority
boundary. Base `404eeb9`. Revision 4.0 sections 4, 6.2, 14 and 19; Revision 3.2
sections 18, 19, 21 and 22. INV-004 through INV-013, INV-025, INV-030 and INV-034.

Status: implemented and locally qualified against the requested base. The full
unfiltered delivery suite passed 70 cases (34 article, 36 existing technical).
The local gates passed 2,401 tests in total. No production write or release
authority. Live GitHub stays `NOT_EXECUTED`.

## Boundary

- Migration `0071`, down `0070`, adds one forced-RLS, function-only immutable
  editorial delivery decision table. Existing migrations and specification bytes
  are unchanged. Destructive downgrade is refused.
- `POST /v1/sites/{site_id}/content-writer/approve-delivery` accepts candidate
  ID, exact revision hash and explicit flagged-sentence acknowledgement paths.
  Current database owner, dashboard CSRF and MFA within five minutes are required.
  Legacy reviews stay record-only; they are not grandfathered into authority.
  A later legacy rejection or requested changes blocks further dispatch effects.
- Operation authority is exactly one of `owner_inbox`, `standing_grant` or
  `owner_editorial`, immutable with its real owner, decision, candidate, revision
  and recovery generation. Composite FKs separate content from technical records.
- Current facts, unsuperseded brief, exact recovery-bound build, active protected
  binding and observed base are checked at approval and dispatch. Remote base
  inspection, one-file reconstruction and protected-path preflight precede effects.
  Output slots are reserved independently of draft slots; article and refresh
  share the same current seven-day cap. Opened PRs count from their recorded effect
  time, not their initial reservation; an unresolved reservation older than seven
  days cannot resume writes into a new cap window.
- Existing 0084 journal acknowledgement, exact Git objects, leases, fencing,
  per-effect permits and ambiguous-response reconciliation are reused. PR bodies
  name editorial authority, owner, decision, candidate/revision, build, grounding,
  originality, risk and recovery. No other path changes.
- 0085 check/deployment/live observation accepts the sealed content revision.
  Exact artifact routing and served digest are required for live proof. PR opening
  is not delivery certification.
- Dashboard shows a separate PR approval and individual sentence checkboxes.
  Slack/Telegram and standing workloads cannot approve or dispatch an article.
  The closed technical A2 set and `autonomy_eligible=false` are unchanged.

## Verification

Run every `scripts/run-*-tests.py` lab, `scripts/openbao_lab.py`,
`scripts/check-gsc-provider-boundary.py`, then:

```sh
.venv/bin/pytest -q tests/api tests/identity tests/tooling tests/connectors
.venv/bin/ruff check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/ruff format --check apps services scripts tests database/migrations deploy/crawler-network
.venv/bin/python -m pip check
npm test
gitleaks git --config .gitleaks.toml --log-opts="main..HEAD" .
```

The unsharded delivery lab includes real PostgreSQL, a separate write journal,
actual shared egress and disposable credential-free builds, with synthetic GitHub.
Its process deadline is one hour for the expanded technical/editorial suite;
production leases, network budgets and every assertion are unchanged.
Imported and collected delivery fixtures share one synthetic signing/encryption
key pair for the lab's single journal stream. Journal signatures and full-chain
verification are unchanged; independently generated fixture keys are not mixed.
It covers approval/dispatch/replay, stale brief/facts/base/build/binding, MFA,
acknowledgements, roles/tenants, immutable authority, caps, chat exclusion,
protected paths, standing plus Jev ship, worker death and all four lost responses.
The database lab checks head, privileges and forced RLS. API and dashboard tests
check closed ingress and acknowledgement controls. Exact counts and commands
belong in [the evidence](../evidence/0123-owner-approved-article-delivery.json).
The exact staged diff is secret-scanned before commit; the committed range is
scanned again before push and its result is recorded in the pull request.

## First Live Run

Owner inputs: a verified disposable origin and protected disposable GitHub
repository without uncertified workflow files; least-privilege App/OpenBao key;
current read binding and complete Eleventy HTML extension; credential-free build
with exact `_site/<path>` output;
real approved facts and unsuperseded brief; newly sealed original candidate;
dashboard owner session with fresh MFA and explicit acknowledgements. Supply the
independent write journal keys/cluster, recovery anchor, role DSNs, admitted
shared GitHub/crawl contexts, robots/artifact keys and trusted customer deployment
environment/actor. The owner alone merges and deploys.

No live provider, production worker composition, signed delivery, semantic
grounding, article autonomy, bulk publishing, merge, deploy, workflow edit,
secrets read, deletion or unpublish is qualified by this slice.

## Merge Train 2

Rebased above merged Train 1 (main `7754746`) in the accepted PR order.
Migration `0071` follows `0070`; 130 cumulative app tables.
Migrations 0001-0070 and the 1200-second aggregate database budget are unchanged.
Fast checks passed: 1905 API/identity/tooling/connectors, 966 PostgreSQL,
43 repository and 203 dashboard cases; Ruff check and format passed.
The earlier qualification above is historical; final-stack full qualification is
recorded separately in the PR comments. No live or production authority is added.

## Train Measurement Integration Fix

Classification: security-critical cross-slice integration correction, authorized
after the train exposed `measurement_evidence_missing` for both verified article
kinds. Migration `0071_article_measurement.sql` follows article observation in
0071; frozen migrations 0001-0070 and the 1200-second database budget are unchanged.

The existing verified-live receipt trigger now selects the exact editorial
manifest through the same private delivery projection. The original technical
baseline function is retained unchanged and delegated to for non-editorial
operations. New articles record `new_page`/`NO_PRE_CHANGE_WINDOW`, null baseline
dates, and no source generations or metrics, even if unrelated pre-change imports
exist. A refresh selects its existing page URL and normal pre-PR generation cutoff
and 7/28/90-day baseline windows. Post windows retain 0094's page-dimension GSC
imports, calendar semantics, due times and explicit `not_yet_due`, `awaiting_data`,
`measured_as_reported` and `unavailable` states (invalid sources remain `failed`).
The only horizon-algorithm extension is a new page's confounder interval starting
at live verification; existing dated baselines are unchanged. Absent pre-change
metrics never become zeros or a numeric observed change. API and dashboard
contracts reject fabricated new-page windows, metrics, generations and deltas.

Tests extend both actual editorial dispatch/live-verification cases through all
three horizons, lag, imported page data, provider revocation, receipt replay,
measurement idempotency and Temporal history replay after a lost activity result.
The existing technical delivery/horizon/restart tests remain in place. PostgreSQL
also compares installed technical baseline and horizon function bodies against
the frozen 0066 definitions, allowing only the null-baseline confounder expression,
and checks that no runtime role gains direct measurement write authority.
Focused correction qualification passed all three new-article, refresh and
unchanged technical measurement cases on real PostgreSQL and Temporal (258.59s).
The added revocation assertion initially passed the raw token and reordered
arguments; it now uses the established digest/order helper. An unchanged retry
was needed after Temporal's five-second local-server startup timeout; no test or
runtime timeout was changed. Both article cases exercise all horizons, receipt
and measurement replay, lost-result history replay and explicit provider
unavailability. Synthetic dashboard renders at 1440x1000 and 390x844 show the
new-page absence without overflow or JavaScript errors; evidence disclosure opens.
Fast qualification is recorded in the merge-train counts above. Final-tip full
qualification is pending and will be recorded in PR comments; historical full
counts above do not qualify this updated source. No live provider or production
qualification is claimed.

A second, separate test-only fix on #24 corrects the inherited 0094 fixture's
wall-clock ordering assumption. Explicit earlier/later imports retain exact cutoff
assertions; equal-timestamp GSC/Bing UUID ordering is covered in both insertion
orders. See [0094's correction record](0094-change-measurement.md). The production
selector and all frozen migrations are unchanged.
