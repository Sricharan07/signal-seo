# ADR-0054: Prove the Finding Pipeline With a Fixed Fixture

- Status: Accepted
- Date: 2026-09-13
- Scope: First durable evidence-to-finding product path

## Context

Slice 0053 made a terminal audit manifest inspectable but correctly produced no
SEO conclusions. The repository has separately qualified crawler primitives, but
the local product workflow still uses a no-network executor and is not authorized
to read the configured origin. Rendering a realistic customer finding at this
point would be fabricated.

The next useful increment must prove that a detector result can retain exact audit
provenance, survive retries and restarts, remain scoped to live user authority, and
be inspected in the product without implying customer evidence.

## Decision

Analyze one immutable HTML fixture whose expected missing-meta-description result
is deterministic. Persist an immutable evidence record tied to the current actor's
latest completed audit command and manifest. Maintain one current finding keyed by
detector release, finding type, and fixture resource, while appending a new
supporting evidence link after a later completed audit.

Expose only two narrow authenticated operations: record the fixed fixture result
and list current findings for the selected exact site. Enable them only in the
explicit local-pilot composition. The dashboard must label the source as a test
fixture, name the configured origin as not analyzed, and show exact evidence
identity, digest, time, and confidence.

Use the existing identity connection for this bounded local operation, granting
that role function execution only and no table privileges. Introduce a dedicated
analysis role before any customer observation can enter this boundary.

## Consequences

- Users can exercise and inspect a real persisted finding flow through the browser.
- Exact retry and concurrent retry cannot duplicate or silently replace evidence.
- Later audit evidence advances the current finding without deleting history.
- The data model now separates immutable observations and links from the guarded
  current finding projection.
- This does not improve or analyze the configured website and cannot support a
  customer SEO claim.

## Alternatives Rejected

- **Infer a finding from the synthetic manifest:** counts and a digest contain no
  page metadata and cannot support an SEO conclusion.
- **Activate the incomplete crawler in the pilot:** production resolver, egress,
  frontier settlement, workflow composition, and customer qualification are not
  complete.
- **Render hard-coded finding rows without persistence:** visually faster but not
  auditable, restart-safe, scoped, or useful for later proposal provenance.
- **Add a broad analysis service now:** unnecessary for one detector and likely to
  create dormant authority before real customer observations exist.

## Verification

Pure parser tests cover positive, negative, and malformed documents. Disposable
PostgreSQL tests cover migration rollback, current authority, completed-audit
provenance, replay, concurrency, later evidence, conflicts, RLS, privilege denial,
and immutability. API and dashboard tests cover strict schemas, same-origin
mutation, closed unconfigured state, and visible fixture provenance. The real
local browser journey executes the action after the durable audit and verifies no
customer-origin or external-write claim.
