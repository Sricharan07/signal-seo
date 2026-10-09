# ADR-0174: Declarative Python Egress Profiles

Status: Accepted for security-critical slice 0167, stacked on 0166.

## Context

Python traffic rules were split between profile budgets, origin overrides,
provider branches, owner admission and connector factories. Revision 4.0
INV-032/INV-033 and Revision 3.2 sections 2 and 10 require a closed, scoped
boundary before outbound I/O. This consistency refactor must not change any
allowed request, denied request, SQL authority gate or external capability.

## Decision

Declare origins (including their existing HTTPS port semantics), methods,
route patterns/exact URLs, headers, sensitivity, budgets, scope validators,
reserved-origin exclusions, credential kind, robots profile, context limits
and SQL binding ports in `egress_profiles.py`. One validator interprets those
entries. Keep provider resource/body validation in small pure validators.
Connector factories derive their existing origin sets and crawl budgets from
the same declarations. Owner context/admission/completion ports are declared
in `owner_connector_egress.py`; one implementation invokes those unchanged ports.

Do not rewrite SQL functions or migrations. SQL admission, journal durability,
intent-before-I/O, DNS pinning, private-address denial, robots decisions,
credential redaction and unknown-outcome handling remain independent gates.
SMTP retains its separate protocol boundary and cannot borrow HTTP.

## Equivalence And Consequences

Freeze requests captured at the pre-refactor validator from existing profile,
connector and tooling tests, supplemented with missing positive routes and
generic negative boundaries from the old code. Replay that oracle against both
the 0166 validator and the new validator; do not derive expectations from the
new declarations. Every HTTP profile has positive and negative examples; SMTP
has an HTTP-denial example and retains its existing dedicated protocol tests.

Exact-URL distinctions, explicit default ports, query/fragment handling and
reserved-origin exclusions are retained, even where a stricter new policy might
seem attractive. Such policy changes belong in a separately approved slice.
No dependencies, grants, provider readiness or publishing authority are added.
Existing tests remain unchanged. See the
[implementation record](../implementation/0167-egress-declarations.md).
