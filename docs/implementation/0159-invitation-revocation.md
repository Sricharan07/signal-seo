# 0159 - Invitation revocation

## Classification

Security-critical: identity, tenancy and restrictive authority, under Revision 4.0
section 19 and Revision 3.2 section 18.5. Stacked on slice 0158, head `26c74a2`.
[ADR-0169](../adr/0169-invitation-revocation.md) records the decision.
[Evidence](../evidence/0159-invitation-revocation.json) records local checks only,
not deployment, production readiness or publishing authority.

## Implemented boundary

- `POST /v1/sites/{site_id}/invitations/{invitation_id}/revoke` uses the existing
  browser mutation proof and closed empty JSON body. It requires the current
  selected site's owner, a verified site and MFA authenticated within five minutes,
  not in the future. `team_owner` rechecks live tenant/site/member/session/identity/
  user authority and recovery generation under locks inside the transaction.
- Only a live pending invitation on that tenant and site can change. Accepted,
  expired, revoked, replayed and other-tenant/site requests are denied. No role
  field or account lookup exists. Only revocation time and revoker can change.
- Migration `0097`, following `0096` (merge train 5; written as 0091 after 0090), relaxes the null-only revocation constraint,
  adds the revoker reference, and defines fixed-search-path guarded functions with
  explicit execution grants. It adds no tables, dependencies or direct invitation
  UPDATE grants. Earlier migrations and accepted specifications are unchanged.
- Revocation atomically writes invitation audit sequence two, its canonical hash
  and previous hash, a platform event and a typed independent-journal outbox record.
  Audit and journal facts exclude email and invitation bearer credentials. Audit
  collision rolls the whole transaction back; database failure grants nothing.
- Acceptance and revoke share tenant/site lock order and invitation serialization.
  Exactly one terminal transition wins. Revoked acceptance has the same generic
  denial as other invalid states, without disclosing the state or account existence.
- Owner Team settings show Revoke only on pending invitations when `can_revoke`
  reflects fresh verified owner authority and a live pending candidate. Confirmation
  says the link stops working and current members keep their access. Uncertain
  outcomes remain unconfirmed and request Reload team, never automatic retries.
  No change to `dashboard-view.tsx` or repository design guards was needed.

## Recovery and durability

The local response explicitly reports `AUTHORITY_DURABILITY_PENDING`. Team history
shows "Revoked locally" and "Recovery confirmation pending" until the existing
dispatcher records an independently verified receipt. Then it says "Recovery
confirmation recorded". It does not claim durable recovery during journal outage.

The existing journal accepts the closed `invitation` / `invitation_revoked` pair.
Restore replay applies a deny-only tombstone through a dispatcher-only function.
The restore-aware private acceptance wrapper checks the tombstone while holding
the common locks, including when the snapshot predates revocation. Replay does
not rewrite invitation/audit history or grant membership, and retains absent-target
tombstones. Unproved acceptance remains private; the purpose-bound proof gateway
is still required. An invitation revoked after it was accepted is not supported.

## Verification

```sh
.venv/bin/python -m pytest tests/api tests/identity tests/tooling tests/connectors -q
.venv/bin/ruff check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
.venv/bin/ruff format --check apps/api services/control_plane database/migrations deploy/crawler-network scripts tests
npm test
df -h .
.venv/bin/python scripts/run-team-invitation-tests.py
.venv/bin/python scripts/run-database-tests.py --shards 3
.venv/bin/python scripts/keycloak_lab.py
.venv/bin/python scripts/run-authority-journal-tests.py
```

Final local results on 2026-10-04:

| Command | Result |
| --- | --- |
| Fast API, identity, tooling and connector pytest | 2,908 passed; two dependency deprecation warnings |
| Ruff check / format check | Passed / 642 files already formatted |
| `npm test` | 46 repository and 302 dashboard cases; typecheck and production build passed; design guards unchanged |
| Focused invitation lab | 149 passed on PostgreSQL 17.11, including 22 new revocation cases |
| Database lab, three shards | 1,282 passed on PostgreSQL 17.11; stable source hashes; cleanup completed |
| Keycloak lab | 5 real-provider checks passed; cleanup completed |
| Independent authority-journal lab | 18 passed, including 2 invitation-specific cases, real snapshot restore and OpenBao rotation; cleanup completed |

Evidence records source/report hashes for these commands. Positive checks
cover atomic hashed revocation, generic acceptance denial and unchanged membership.
Negative checks cover non-owner/admin, stale/primary MFA, unverified site, invalid
recovery/session, another tenant/site, accepted/expired/revoked invitations, CSRF,
missing browser proofs, closed request fields and database privilege isolation.
Failure checks terminate only the test's own database connection, verify audit
collision rollback, and force both orderings of a real concurrent accept/revoke
race with the loser observed waiting on a database lock before committing the
winner. Dashboard checks cover confirmation, conditional controls, strict response
binding and explicit unconfirmed results.

The real two-cluster journal lab adds invitation-specific outage/receipt and
snapshot-restore cases. It rotates the actual disposable OpenBao recovery
generation, replays the independent signed journal, and proves the restored
pending invitation cannot be accepted with a fresh purpose-bound identity proof
and no member is added. Its synthetic proof supplements the separate real
Keycloak provider checks; it does not claim a live browser login.

Earlier focused runs had one incorrect failure-test expectation (a closed client
was rejected before I/O); it now terminates its own server backend and asserts
unchanged invitation state. An earlier npm run passed 302 dashboard cases but
failed a test-only TypeScript header type. Those runs are not qualification.

## Explicit limits

Not deployed or customer-enabled. Invitation email/resend, removal of accepted
members, and live hydrated owner-Team desktop/mobile capture remain outside this
qualification. The fresh finish review's disposition is `recapture` for the missing
hydrated desktop/mobile captures; the documenter confirmed incumbent-system
preservation from code, not visual approval. Existing DESIGN.md and its sidecar are
preserved; current static
design findings in the shared stylesheet predate the three added layout rules.
Repository design guards pass unchanged. Full gate and Temporal are deliberately
not run. Labs own and clean only their disposable resources; no other session's
containers or images are removed. No production or publishing authority is added.
