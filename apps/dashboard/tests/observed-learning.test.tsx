import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { SeoStrategy } from "../components/seo-strategy";
import { validSeoProjection } from "../lib/seo-strategy-api";
import { learningProjection } from "./observed-learning.fixture";
import { projection, SITE } from "./seo-strategy.fixture";

test("0137 dashboard shows samples, observed deltas, seasonality and evidence", () => {
  assert.ok(validSeoProjection(learningProjection));
  for (const view of ["overview","analytics"] as const) {
    const html = renderToStaticMarkup(<SeoStrategy siteId={SITE} view={view} initialData={learningProjection}/>);
    assert.match(html,/Observed effectiveness/); assert.match(html,/5 \/ 5/);
    assert.match(html,/not evidence of causation/); assert.match(html,/Position: -2/);
    assert.match(html,/Declining pages/); assert.match(html,/Seasonality possible/);
    assert.match(html,/2026-08-06 to 2026-09-02/); assert.match(html,/840 to 280/);
    assert.match(html,/evidence_id=/); assert.match(html,/Pages under Signal measurement/);
    assert.doesNotMatch(html,/Accept proposal/);
  }
});
test("historical, no-sample, and partial page data remain honest", () => {
  assert.ok(validSeoProjection(projection));
  assert.match(renderToStaticMarkup(<SeoStrategy siteId={SITE} view="analytics" initialData={projection}/>),/Unavailable in this historical snapshot/);
  const p = structuredClone(learningProjection);
  p.snapshot!.payload.learning!.effectiveness.groups = [];
  const page = p.snapshot!.payload.learning!.decay.pages[0]!;
  page.declining = false; page.state = "partial";
  page.prior_28_days.state = "partial"; page.prior_28_days.metrics = null;
  page.prior_28_days.recent.state = "partial"; page.prior_28_days.recent.daily = null;
  assert.ok(validSeoProjection(p));
  const html = renderToStaticMarkup(<SeoStrategy siteId={SITE} view="analytics" initialData={p}/>);
  assert.match(html,/Priority factor remains neutral/); assert.match(html,/Partial page comparisons/);
  assert.match(html,/Missing or lagged page-days/); assert.doesNotMatch(html,/840 to 280/);
});
test("learning projection rejects malformed signed deltas, factor bounds and evidence", () => {
  for (const mutate of [
    (p: typeof learningProjection) => {p.snapshot!.payload.learning!.effectiveness.groups[0]!.factor = 2;},
    (p: typeof learningProjection) => {p.snapshot!.payload.learning!.effectiveness.groups[0]!.metrics.clicks!.mean_delta = Infinity;},
    (p: typeof learningProjection) => {p.snapshot!.payload.learning!.effectiveness.groups[0]!.evidence_ids = [SITE];},
    (p: typeof learningProjection) => {p.snapshot!.payload.learning!.decay.pages[0]!.url = "javascript:alert(1)";},
    (p: typeof learningProjection) => {p.snapshot!.payload.learning!.decay.pages[0]!.basis = "year_over_year";},
    (p: typeof learningProjection) => {p.snapshot!.payload.learning!.decay.pages[0]!.seasonality_possible = false;},
    (p: typeof learningProjection) => {p.snapshot!.payload.learning!.decay.pages[0]!.declining = false;},
    (p: typeof learningProjection) => {p.snapshot!.payload.strategy.items[0]!.priority.inputs.observed_effectiveness = {label:"Observed",factor:2,groups:[],bounds:[.8,1.2],basis:"Invalid"};},
  ]) {
    const p = structuredClone(learningProjection); mutate(p); assert.equal(validSeoProjection(p),false);
  }
});
