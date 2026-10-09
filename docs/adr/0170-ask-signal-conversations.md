# ADR-0170: Private, grounded Ask Signal conversations

Status: Accepted for slice 0162's backend; live deployment is not qualified.

## Context

Revision 3.2 section 25.2 requires typed interpretation, not natural-language
execution authority. Revision 4.0 sections 4 and 19 also apply to model calls,
tenant data and persistent memory. The existing Chat readiness ledger is not a
conversation store. The dashboard is a separate reviewer-owned implementation.

## Decision

Use migration 0099, following unchanged 0098 (merge train 5; written after 0092), for function-only, forced-RLS
conversation, message, memory and forgetting tables. Resolve the current session,
recovery generation, membership and site access on every database operation.
Bind all private rows to tenant, site and user. Members may read and resume only
their own conversations; explicit memory mutations require the owner.
SECURITY DEFINER functions use `search_path=pg_catalog`, qualified objects,
PUBLIC revoke-all and explicit runtime grants. Runtime roles own no tables.

Journal the owner message and provisional `outcome_unknown` reply before model
I/O. An identical request returns the journal, even after a crash. It cannot
dispatch or charge again. Reusing the request with different text or conversation
conflicts. A live request holds a connection-scoped advisory lock; a concurrent
duplicate receives 409, not a provisional success. Connection loss releases the
lock; replay then seals the unknown journal outcome without dispatch. Final
replies are write-once. Existing shared-egress model admission
and monthly budget reservations are reused for `owner_answers`, medium effort,
on the configured product model. Unknown or invalid outcomes retain holds.

Construct a bounded packet from committed site records, the last 12 turns, a
rolling quoted-owner summary and at most ten private memories ranked by simple
PostgreSQL full-text search plus recency. These are quoted untrusted data, never
instructions, approved facts, grants or policy. No tools or browsing are enabled.
Validate the model's closed output schema and resolve citations against the
packet. For this first backend, answers are limited to server-built record
excerpts. Unsupported prose becomes `I don't have a record of that`.

Action intents produce only server-built links to existing owner controls.
There is no approval, pause, resume, revoke, research dispatch, merge, deployment
or permission mutation in the assistant. Existing owner routes retain current
revision, MFA freshness and browser mutation checks. Model-supplied hrefs or
execution fields are invalid.

At most two proposed preference/context memories may be stored after a grounded
owner answer. Each must be an exact substring of that owner's current message
and pass conservative personal-context classification. Explicit additions use
the same classification; ambiguous or authority-like text is refused with 409.
Business statements instead use the existing Business Brain **propose** port,
with stable request-derived IDs, and link to owner review. They are neither
approved nor memory. A proposed fact cannot become answer grounding.
New proposed memories and their visible `remembered` reply commit atomically;
failed reply persistence cannot leave undisclosed long-term memory behind.

Forgetting is append-only audited. Forgotten items are excluded from retrieval;
history and summaries containing their text are also excluded. Recheck memory
after model I/O and atomically check owner-forgetting at final reply commit.
Summary replacement has a separate audit reason and cannot undo owner forgetting.
Summaries refresh after 12 turns and every subsequent 12 turns, even when the
model is unavailable. They retain bounded eligible personal owner quotes and
opaque cited-record references, not business statements, invented facts or an
unbudgeted extra model call. Safe personal quotes in the summary can be cited as
conversation memory; record references alone are not facts or authority.

Conversation retention is 365 days from creation, including its messages and
linked memories; independent memories expire 365 days after creation. Reads
exclude expired data. Due private scopes are physically pruned on access.
`control.assistant_purge()` is a scheduler-only, bounded, skip-locked cleanup port
for inactive scopes. Deployment must schedule this port; this slice does not
start Temporal or claim a deployed cleanup timer.

## Consequences

The shared v1 API is suitable for the separate dashboard, with honest unavailable
states, UTC timestamps, bounded JSON and the existing browser mutation proof.
There are no new dependencies, provider credentials or authority channels.

Extractive answers are intentionally less fluent than unconstrained generation.
The explicit-memory endpoint is more conservative than the contract's length
and kind validation: otherwise valid ambiguous text can receive 409. These
limitations favor approved-fact grounding over apparent capability. Semantic
memory editing, arbitrary commands, external research and production readiness
are not claimed. Live gateway, dashboard integration and inactive-scope cleanup
timer qualification remain separate work.

See [implementation](../implementation/0162-ask-signal.md) and
[evidence](../evidence/0162-ask-signal.json).
