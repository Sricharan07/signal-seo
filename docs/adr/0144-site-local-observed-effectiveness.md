# ADR-0144: Site-Local Observed Effectiveness

Status: Accepted for local product implementation; no release authority.

Merge train 3 also qualifies the security-critical composition of the existing
weekly read port and recorded editorial measurement path. Explicit new-page
baselines remain non-comparable. The implementation record owns this integration;
the decision below still cannot create authority or infer causal effects.

## Context

0094 provides immutable provider-reported per-change observations, not causal
estimates. Strategy should learn from those records without pooling tenants,
rewarding tiny samples, or turning a traffic increase into execution authority.

## Decision

0088 extends only the existing owner-scoped strategy source packet. Latest
observations per operation/horizon replace earlier observations in the analysis,
never in storage. Eligible records are `measured_as_reported`, horizons 28/90,
with an existing recipe/work-type identity. Group by site, work type, optional
exact recipe, and horizon. There is no cross-site or cross-tenant training.

Each measurement weight is `1 / (1 + recorded_confounder_count)`. Each metric
shows raw sample size, sum of weights, weighted mean delta, and shrunk delta.
Null metrics stay null and do not enter that metric's sample size. With effective
n below 3, shrinkage is zero; otherwise it is `n / (n + 5)`, a neutral prior of
five observations. CTR is a fractional-rate delta; lower position is better.

For priority only, average the available relative clicks/impressions deltas per
measurement, clipping each to [-1,1]. Zero/missing baselines cannot supply ratios.
Use the same weights and shrinkage on ratio-eligible samples, producing
`1 + 0.2 * shrinkage * weighted_signal`. Equal-average the available horizon
factors, never treating two horizons as independent changes. Prefer exact recipe
groups; fall back to that site's work-type groups only when no exact group exists.
No matching history means 1. All factors are bounded [0.8,1.2]; results round to
six decimals. Original strategy item identities and owner decisions are preserved.

Every candidate exposes the factor, constituent horizon groups, sample sizes,
bounds, and measurement evidence in its priority inputs. The dashboard says
"Observed on this site; not evidence of causation." Provider completeness remains
partial even when arithmetic is available. None of this adjusts grants, Jev
thresholds, volume/spend caps, recipe eligibility, or external-write policy.

## Alternatives

Raw delta multipliers overreact to outliers and small sites. Global recipe
statistics mix business/seasonality differences and are outside this scope.
Causal learning needs a separately defensible experiment design and evidence.

## Consequences

Low-volume sites can remain neutral indefinitely. Confounding lowers confidence
without hiding observed deltas. Unknown external confounders remain possible.
Current delivery supports technical recipes; editorial measurements remain absent
until that existing delivery/measurement boundary actually supports them.

## Verification

Deterministic and negative tests in `tests/connectors/test_observed_learning.py`;
real PostgreSQL source/role tests in `tests/control_plane/test_observed_learning.py`
and measured delivery proof in `tests/delivery/test_change_measurement_delivery.py`.
See [0137](../implementation/0137-learning-and-decay.md) for gate status.
