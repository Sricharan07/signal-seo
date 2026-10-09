"use client";

import { readableCode } from "./home-panels";
import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { validPageSpeed, type FieldExperience, type PageSpeedData, type SpeedMetric } from "../lib/pagespeed-api";

const label = readableCode;
const metricValue = (metric: SpeedMetric) => metric.state === "unavailable" ? `Unavailable: ${label(metric.reason ?? "not reported")}` : `${metric.value} ${metric.unit === "ms" ? "ms" : ""}${metric.rating ? ` (${label(metric.rating)})` : ""}`;
function Field({ value, title }: { value: FieldExperience; title: string }) {
  const period = value.collection_period;
  return <section className="speed-measurements" aria-label={title}>
    <h3>{title} <span>{label(value.status)}</span></h3>
    <p className="speed-locator">{value.locator}</p>
    <dl>{Object.entries(value.metrics).map(([name, metric]) => <div key={name}><dt>{name.toUpperCase()} p75</dt><dd>{metricValue(metric)}</dd></div>)}</dl>
    <p>Collection period: {period.state === "available" ? `${period.first_date} to ${period.last_date}` : "Unavailable: not reported by PSI"}</p>
  </section>;
}
export function PageSpeed({ siteId, initialData }: { siteId: string; initialData?: PageSpeedData }) {
  const [data, setData] = useState<PageSpeedData | null>(initialData ?? null);
  const [state, setState] = useState<"loading" | "loaded" | "unavailable">(initialData ? "loaded" : "loading");
  const load = useCallback(async () => {
    setState("loading");
    try {
      const response = await fetch(`/actions/pagespeed?site_id=${siteId}`, { cache: "no-store" });
      const value: unknown = await response.json();
      if (!response.ok || !validPageSpeed(value, siteId)) throw new Error();
      setData(value); setState("loaded");
    } catch { setData(null); setState("unavailable"); }
  }, [siteId]);
  useEffect(() => { if (!initialData) void load(); }, [load, initialData]);
  return <section className="page-speed settings-section" aria-labelledby="speed-title">
    <header className="brain-toolbar"><h2 id="speed-title">Core Web Vitals</h2><button className="icon-command" type="button" title="Refresh performance observations" aria-label="Refresh performance observations" disabled={state === "loading"} onClick={() => void load()}><RefreshCw size={16} /></button></header>
    {state !== "loaded" ? <p role="status">{state === "loading" ? "Loading performance observations" : "Performance observations unavailable. Refresh to retry."}</p> : <>
      {data?.samples.length === 0 && <p>No PageSpeed observations for this verified site.</p>}
      {data?.samples.map((sample) => <article key={sample.sample_id} className="speed-page">
        <header><h3 className="speed-locator">{sample.url}</h3><p>{label(sample.strategy)} / Week of {sample.week_start} / {sample.selection_source === "gsc_clicks" ? "GSC clicks sample" : "Crawl order sample"}</p></header>
        {!sample.observation ? <p>{sample.state === "outcome_unknown" ? "Provider outcome unknown" : label(sample.state)}{sample.reason ? `: ${label(sample.reason)}` : ""}</p> : <>
          <p>Fetched <time dateTime={sample.observation.fetched_at}>{new Date(sample.observation.fetched_at).toLocaleString("en-US", { timeZone: "UTC" })} UTC</time></p>
          <div className="speed-sources"><Field value={sample.observation.field_url} title="Field / Page" /><Field value={sample.observation.field_origin} title="Field / Origin" />
            <section className="speed-measurements" aria-label="Lab / Lighthouse"><h3>Lab / Lighthouse {sample.observation.lighthouse_version}</h3><p>Performance score: {sample.observation.lab.performance_score === null ? "Unavailable" : Math.round(sample.observation.lab.performance_score * 100)}</p><dl>{Object.entries(sample.observation.lab.metrics).map(([name, metric]) => <div key={name}><dt>{name === "speed_index" ? "Speed index" : name.toUpperCase()}</dt><dd>{metricValue(metric)}</dd></div>)}</dl></section>
          </div>
          <details><summary>Evidence and observations</summary><p>Evidence {sample.observation.evidence_id}</p><p className="speed-locator">Response SHA-256 {sample.observation.response_sha256}</p><ul>{sample.observation.findings.map((finding) => <li key={finding.id}>{finding.title}: {finding.summary}</li>)}</ul></details>
        </>}
      </article>)}
    </>}
    <p>Local Lighthouse: unavailable</p>
  </section>;
}
