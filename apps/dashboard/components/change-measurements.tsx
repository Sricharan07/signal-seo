import type { ChangeMeasurement, MeasurementMetrics, MeasurementSource } from "../lib/change-measurement-api";
import { readableCode } from "./home-panels";

const metrics = ["clicks", "impressions", "ctr", "position"] as const;
const format = (value: number | null | undefined) => value == null ? "Unavailable" : new Intl.NumberFormat("en", { maximumFractionDigits: 4 }).format(value);
function SourceEvidence({ name, value }: { name: string; value: MeasurementSource }) {
  return <details className="measurement-evidence"><summary>{name}: {readableCode(value.state)}</summary>
    <p>{readableCode(value.reason)}</p>
    {(value.generation_id || value.coverage) && <details className="technical-details"><summary>Technical details</summary>
      {value.generation_id && <p>Import generation: <code style={{ overflowWrap: "anywhere" }}>{value.generation_id}</code></p>}
      {value.coverage && <pre>{JSON.stringify(value.coverage, null, 2)}</pre>}
    </details>}
  </details>;
}
function MetricRows({ before, after, delta, context = false }: { before?: MeasurementMetrics | null; after?: MeasurementMetrics | null; delta?: MeasurementMetrics | null; context?: boolean }) {
  return <dl className="change-scope">{(context ? metrics.slice(0, 2) : metrics).map(metric => <div key={metric}>
    <dt>{metric === "ctr" ? "CTR" : metric[0].toUpperCase() + metric.slice(1)}</dt>
    <dd>Before {format(before?.[metric])}; after {format(after?.[metric])}; observed change {format(delta?.[metric])}</dd>
  </div>)}</dl>;
}
export function ChangeMeasurements({ values }: { values: ChangeMeasurement[] }) {
  return <section aria-labelledby="measurement-report-title"><h3 id="measurement-report-title">What it did</h3>
    {values.length === 0 ? <p>No measurement horizons became due or changed this week.</p> : values.map(value => <article className="change-measurement" key={`${value.operation_id}:${value.horizon}`}>
      <h4>{value.horizon}-day observation</h4>
      <p className="measurement-page">{value.page_url}</p>
      <p>{value.observation.state === "measured_as_reported" ? "As reported by provider; completeness not guaranteed" : readableCode(value.observation.state)}</p>
      <p>{readableCode(value.observation.reason)}</p>
      <p>{"state" in value.baseline ? "New page: no pre-change window." : <>Baseline: {value.baseline_start} to {value.baseline_end}.</>} Post window: {value.post_start} to {value.post_end}.</p>
      <p>Calendar dates: GSC America/Los_Angeles; Bing UTC. Bing top-page dates have unknown granularity and incomplete coverage.</p>
      <h4>Search Console, this page</h4>
      <MetricRows before={value.baseline.gsc_page.metrics} after={value.observation.post?.gsc_page.metrics} delta={value.observation.observed_change?.gsc_page} />
      <SourceEvidence name="GSC baseline" value={value.baseline.gsc_page} />
      {value.observation.post && <SourceEvidence name="GSC post window" value={value.observation.post.gsc_page} />}
      <h4>Bing, whole site (context)</h4>
      <MetricRows context before={value.baseline.bing_site_context.metrics} after={value.observation.post?.bing_site_context.metrics} delta={value.observation.observed_change?.bing_site_context} />
      <SourceEvidence name="Bing site baseline" value={value.baseline.bing_site_context} />
      {value.observation.post && <SourceEvidence name="Bing site post window" value={value.observation.post.bing_site_context} />}
      <h4>Bing, this page</h4>
      <p>Clicks and impressions are as reported by Bing. Its two named positions are not comparable to GSC position and are not combined.</p>
      <MetricRows context before={value.baseline.bing_page.metrics} after={value.observation.post?.bing_page.metrics} delta={value.observation.observed_change?.bing_page} />
      <SourceEvidence name="Bing page baseline" value={value.baseline.bing_page} />
      {value.observation.post && <SourceEvidence name="Bing page post window" value={value.observation.post.bing_page} />}
      <p>Observed change, not a causal estimate. Other influences may be unobserved.</p>
      {value.observation.confounders.length === 0 ? <p>No other Signal live-verified changes to this page recorded in these windows.</p> : <ul>{value.observation.confounders.map(c => <li key={c.operation_id}><a href={`/changes#operation-${c.operation_id}`}>Other page change</a>: {c.verified_live_at}</li>)}</ul>}
      <a href={value.evidence_url}>Live verification evidence</a>
    </article>)}
  </section>;
}
