# ADR-0079: Bound Browser Choice to Recorded Link IDs

Status: Accepted for the internal browser boundary; no production release.

## Context

Revision 4.0 section 9 requires Jev Choice over reduced interactive elements,
immutable per-step evidence and a low-confidence handoff. The slice deliberately
excludes frontier planning, vision, Lighthouse and Core Web Vitals.

## Decision

Keep the planner deterministic. The internal API renders or reads an admitted
page, optionally makes one Jev-selected link hop, or compares rendered visible text
with an exact SHA-256-sealed fragment. Read goals are bounded caller inputs, never
instructions taken from a page. 0085's independent GET verification is unchanged.

Reduce Chromium's accessibility tree to admitted anchor links with bounded name,
URL, role and stable id. Deterministically prune to at most 255 candidates and a
16 KiB list. Exclude authentication and transaction links. Follow a link by
resolving its recorded href and issuing a GET navigation, never a DOM click.
The worker itself exposes only navigate, follow-link, scroll, bounded network-idle
wait, reduced-tree read and screenshot. Caller JavaScript, typing and clicks have
no action representation.

Reuse `DecisionService` and `PostgresDecisionRecorder` for a typed `element`
Choice. The recommendation ceiling is `ask_owner`; it cannot grant external-write
authority. Independently validate the element answer, complete probability vector
and confidence before following. A rejection, fallback, invalid id, low confidence,
missing decision service, fewer than two candidates, form/login/CAPTCHA path or
incomplete network stops the choice. No frontier fallback plans a browser action.

Migration 0072 adds immutable forced-RLS session, step and egress-reference records.
Session intent precedes container startup. Goal, prior choice-list digest, decision
identity/confidence or deterministic label, resulting URL, snapshot digest and
robots/admission references are recorded. Snapshots and screenshots are encrypted
using the existing artifact store, authenticated on readback and registered in
the existing artifact catalog. Sequence, terminality and session budgets are
checked again in PostgreSQL. A failure is incomplete/failed, never verified.

## Alternatives

- Model-generated browser scripts would turn data into commands.
- DOM clicking can invoke handlers, forms or purchase flows.
- A silent singleton/fallback selection would overstate Jev qualification.
- Recording screenshots as local plaintext would violate artifact isolation.

## Consequences

Pages requiring credentials, interaction, extra origins or unsupported resources
remain unavailable/incomplete. Truncated text cannot prove a missing fragment.
Low-confidence results return to the caller; this slice supplies no planner or
vision escalation implementation. Internal configuration is explicit and single-use.
No dashboard or weekly-loop integration, production readiness, live Jev success,
deployment correlation or external write is implied.

## Verification

`tests/tooling/test_browser_policy.py`, `tests/control_plane/test_browser_records.py`
and the real Docker browser lab exercise choices, injection, denial, encrypted
evidence, tenancy, budgets and teardown. See [0067](../implementation/0067-sandboxed-browser-worker.md).
