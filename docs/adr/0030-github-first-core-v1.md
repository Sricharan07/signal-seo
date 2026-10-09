# ADR-0030: GitHub-First Core V1 Product Contract

- Status: Accepted; scope, deferrals, and milestone sequence superseded by [ADR-0060](0060-autonomous-seo-employee-direction.md)
- Date: 2026-09-08
- Owners: Signal product and engineering
- Supersedes: The future pilot sequence in [ADR-0001](0001-incremental-delivery.md)
- Preserves: The WordPress experiment boundary in [ADR-0002](0002-wordpress-lab-boundary.md)

## Context

Revision 3.1 chose a narrow WordPress broken-link pilot before broader product
work. That was a useful way to force an early real-provider test, and the completed
disposable WordPress lab remains valid evidence within its stated limits.

The owner's actual first product evaluation is different: connect an
owner-controlled public website, its Google Search Console property, its GitHub
repository, and Telegram; then use a clean dashboard to watch a coordinated SEO
workforce research, plan, propose, test, request approval, create a pull request,
observe delivery, verify the public result, measure, and recover.

The prior sequence treated GitHub as later scope and described broad analyst and
implementer roles. Leaving the specialist responsibilities implicit creates a
real risk that competitor research, content strategy, analytics, or performance
work will be deferred even though they are essential to the product's AI employee
experience. Conversely, treating every role as a separate service would add
operational complexity without adding product correctness.

## Decision

Adopt the preserved
[Revision 3.2 specification](../../Signal_Production_Engineering_Specification_Revision_3_2.md),
[Core V1 PRD](../product/core-v1-prd.md), and
[Core V1 roadmap](../implementation/core-v1-roadmap.md) as the active product and
implementation contract.

Core V1 is GitHub-first and requires the complete owner-controlled GSC, GitHub,
public-site, dashboard, and Telegram journey. It includes nine explicit roles:

1. Coordinator / Planner
2. Technical SEO Specialist
3. Analytics Specialist
4. Competitor Research Specialist
5. Content Strategy Specialist
6. Performance Specialist
7. Repository Implementer
8. Independent Reviewer / Verifier
9. Communicator

These are versioned, permissioned, evaluated logical responsibilities. They may
share a bounded agent runtime and OpenAI adapter. Durable workflows coordinate
typed work; deterministic domain services own authority, budgets, policy,
approvals, external operation identity, verification state, and recovery. Signal
will not build an unbounded agent chat swarm or nine premature microservices.

Only CMS-specific delivery teams are deferred from the previously discussed agent
set. Core V1 produces a PR for one certified repository recipe but receives no
merge, deployment, repository administration, protected-workflow, or standing
autonomy authority. External deployment is observed, the public site is verified
independently, and recovery is another reviewed PR.

All applicable safety and release gates remain mandatory. Narrower breadth does
not permit fake provider qualification, missing negative/failure tests, operator-
only user flows, or untested recovery.

## Alternatives Considered

### Keep The WordPress-First Product Pilot

Rejected because it does not exercise the owner's real code, analytics, delivery,
dashboard, or communication loop. The WordPress lab remains evidence rather than
being discarded or misrepresented as the product pilot.

### Ship A Smaller Generic Agent Set

Rejected because collapsing content, competitor, analytics, performance, and
technical SEO into a generic analyst makes ownership, tool permissions,
evaluation, disagreement, and product completeness ambiguous.

### Create A Service For Every Agent

Rejected for Core V1. Process boundaries must follow measured security, scaling,
failure, or ownership needs. Typed role contracts provide the necessary separation
without multiplying deployable infrastructure prematurely.

### Build Full GA1 Before Testing The Owner Flow

Rejected because CMS breadth, additional recipes, multi-site scale, advanced
administration, and standing autonomy would delay evidence that the central
product is useful. Core V1 retains rigor while testing a narrower real path.

## Consequences

- M2 through M6 now prioritize owner-ready GSC, GitHub, Telegram, dashboard,
  evidence/memory, all nine roles, one repository recipe, PR delivery, live
  verification, measurement, and recovery.
- The full first journey is larger than a disposable provider experiment, but it
  tests the actual product proposition rather than isolated infrastructure.
- Role contracts, evaluation data, permission matrices, and durable handoffs must
  be designed before claiming that an agent exists.
- GitHub PR operations require the applicable external-write, ambiguity,
  reconciliation, and recovery safeguards even without merge authority.
- WordPress/CMS teams, GA4, additional recipes, multi-site scale, merge/deploy
  authority, and standing autonomy remain explicitly unavailable.
- The active specification revision, PRD, roadmap, indexes, status, tests, and
  changelog must move together to prevent two competing plans.

## Verification

This decision is a documentation contract, not runtime evidence. Repository checks
must prove that:

- Revision 3.0 and Revision 3.1 remain byte-for-byte protected baselines;
- Revision 3.2 is protected as the active accepted revision;
- the PRD, roadmap, ADR, implementation record, indexes, status, and changelog are
  present and consistently name the Core V1 resources, nine roles, and deferrals;
- the active contract does not call GitHub, GSC, or Telegram optional for Core V1;
  and
- no implementation or pilot admission is claimed by this documentation change.

Runtime qualification follows the PRD Definition of Done and the specification's
applicable gates, including real-provider and complete failure-path evidence.
