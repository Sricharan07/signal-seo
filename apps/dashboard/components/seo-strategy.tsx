"use client";

import { useEffect, useState } from "react";
import { Check, Lightbulb, RefreshCw, X } from "lucide-react";
import { formatDay, readableCode } from "./home-panels";
import { validSeoProjection, type EvidenceNumber, type SeoProjection, type SeoSnapshot, type PerformanceCohort, type TopicCluster, type TopicVolume } from "../lib/seo-strategy-api";

type View = "overview" | "pages" | "strategy" | "analytics" | "topics";
const NAMES: Record<string, string> = {
  metadata_pr: "Title and description fixes", draft_patch: "Content changes", new_article: "New articles",
  content_refresh: "Content refreshes", research_audit: "Research", technical_description: "Description fix",
  dataforseo: "DataForSEO", bing: "Bing Webmaster Tools", gsc: "Google Search Console", ga4: "Google Analytics",
  ai_visibility: "AI answers", crawl: "Site crawl", ctr: "Click-through rate", prior_28_days: "Prior 28 days",
  year_over_year: "Year over year",
  serp: "search results", volume: "search volume", backlinks: "backlinks",
};
// Internal keys become plain names; anything unnamed reads as a sentence, never as a key.
const label = (value: string) => {
  const named = NAMES[value.toLowerCase()];
  if (named) return named;
  return readableCode(value);
};
const numeric = (value: number) => new Intl.NumberFormat("en", {maximumFractionDigits: 4}).format(value);

function Evidence({ siteId, snapshot, ids }: {siteId: string; snapshot: SeoSnapshot; ids: string[]}) {
  return <span className="seo-evidence">{ids.map(id => <a key={id} href={`/actions/seo-strategy?site_id=${siteId}&snapshot_id=${snapshot.id}&evidence_id=${id}`} target="_blank" rel="noreferrer" title={`Inspect evidence ${id}`}>Evidence {id.slice(0,8)}</a>)}</span>;
}
function Measurement({ siteId, snapshot, value }: {siteId: string; snapshot: SeoSnapshot; value: EvidenceNumber}) {
  return <><strong className="seo-number">{numeric(value.value)}</strong> <Evidence siteId={siteId} snapshot={snapshot} ids={value.evidence_ids}/></>;
}

export function SeoStrategy({siteId, view, initialData}: {siteId: string | null; view: View; initialData?: SeoProjection}) {
  const [data,setData] = useState<SeoProjection | null>(initialData ?? null);
  const [state,setState] = useState<"loading"|"ready"|"unavailable">(initialData ? "ready" : "loading");
  const [busy,setBusy] = useState(false), [notice,setNotice] = useState<string | null>(null);
  const [page,setPage] = useState(0);
  useEffect(() => {
    if (!siteId) return;
    const controller = new AbortController();
    async function load() {
      try {
        const r = await fetch(`/actions/seo-strategy?site_id=${siteId}`,{cache:"no-store",signal:controller.signal});
        const value: unknown = await r.json();
        if (!r.ok || !validSeoProjection(value)) throw new Error();
        setData(value); setState("ready");
      } catch { if (!controller.signal.aborted) setState("unavailable"); }
    }
    void load(); return () => controller.abort();
  },[siteId]);
  async function command(action: string, values: Record<string,unknown> = {}) {
    if (!siteId || busy) return;
    setBusy(true); setNotice(null);
    try {
      const r = await fetch("/actions/seo-strategy",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema_version:1,site_id:siteId,action,...values})});
      if (!r.ok) throw new Error();
      const outcome = await r.json();
      const updated = await fetch(`/actions/seo-strategy?site_id=${siteId}`,{cache:"no-store"});
      const value: unknown = await updated.json();
      if (!updated.ok || !validSeoProjection(value)) throw new Error();
      setData(value); setState("ready"); setPage(0);
      setNotice(outcome.target_kind === "brief" ? "Brief proposal created. Owner acceptance is still required in Content Writer." : outcome.target_kind === "inbox" ? "Exact revision is waiting in Inbox. No approval or delivery was created." : action === "ideas" ? "Model ideas recorded; no approval or work was dispatched." : action === "refresh" ? "Baseline and plan recorded from current evidence." : "Item dismissed.");
    } catch { setNotice("Request unavailable or evidence changed. Refresh and retry; no new authority was granted."); }
    finally { setBusy(false); }
  }
  if (!siteId) return <section className="seo-section"><h2>SEO baseline</h2><p>Owner access to a selected site is required.</p></section>;
  const snapshot = data?.snapshot;
  const title = view === "topics" ? "Keyword ideas" : view === "strategy" ? "90-day strategy" : view === "analytics" ? "Search performance" : view === "pages" ? "Page evidence" : "SEO baseline";
  return <section className="seo-section" aria-busy={busy}>
    <div className="seo-heading"><h2>{title}</h2><button className="button secondary" onClick={() => void command("refresh")} disabled={busy || state === "loading"} title="Record a new evidence snapshot"><RefreshCw size={14} aria-hidden="true"/>{snapshot ? "Refresh baseline" : "Build baseline"}</button></div>
    {notice && <p role="status">{notice}</p>}
    {state === "loading" ? <p role="status">Loading site evidence.</p> : state === "unavailable" ? <p role="alert">Baseline unavailable. The site-scoped evidence service could not be read.</p> : !snapshot ? <p>No baseline has been recorded for this site.</p> : <>
      <p className="seo-meta">Version {snapshot.version} · {formatDay(snapshot.created_at)}</p>
      <details className="technical-details"><summary>Technical details</summary><p>Snapshot: <code>{snapshot.id}</code></p><p>SHA-256: <code>{snapshot.sha256}</code></p><p>Recorded: {snapshot.created_at}</p></details>
      {(view === "overview" || view === "analytics") && <Learning siteId={siteId} snapshot={snapshot}/>}
      {view === "topics" && <>
        {!snapshot.payload.topics ? <p>Topics unavailable in this historical snapshot. Refresh the baseline.</p> : <>
          <p>{snapshot.payload.topics.scope}</p>
          <div className="seo-heading"><p>Related ideas: {readableCode(snapshot.payload.topics.ideas_status)}. {snapshot.payload.topics.ideas_reason} Search volume: {readableCode(snapshot.payload.topics.volume_status)}.</p><button className="button secondary" title="Request budgeted model ideas for this exact snapshot" disabled={busy || !snapshot.payload.topics.clusters.length || snapshot.payload.topics.ideas_status === "available"} onClick={() => void command("ideas",{snapshot_id:snapshot.id})}><Lightbulb size={14} aria-hidden="true"/>Expand ideas</button></div>
          {snapshot.payload.topics.unavailable.map(u => <p key={u.source}>{label(u.source)}: {u.reason}</p>)}
          <details><summary>Clustering and gap policy</summary><p>{snapshot.payload.topics.policy.clustering}</p><p>High impressions: at least {snapshot.payload.topics.policy.high_impressions_minimum}. Weak average position: at least {snapshot.payload.topics.policy.weak_position_minimum}.</p></details>
          {!snapshot.payload.topics.clusters.length && <p>No observed query clusters. Import query-dimensional search data, then refresh.</p>}
          <ol className="seo-items">{snapshot.payload.topics.clusters.slice(page*25,(page+1)*25).map(cluster => {
            const item = snapshot.payload.strategy.items.find(i => i.id === cluster.strategy_item_id);
            const decision = data?.decisions.find(d => d.item_id === cluster.strategy_item_id);
            return <li key={cluster.id}>
              <div className="seo-heading"><h3>{cluster.title}</h3><span>{label(cluster.source)} · {readableCode(decision?.decision ?? "observed")}</span></div>
              <p>{cluster.window ? `${cluster.window.start} to ${cluster.window.end}` : "Window unavailable"}</p>
              <dl className="seo-headline"><div><dt>Impressions</dt><dd>{numeric(cluster.metrics.impressions)}</dd></div><div><dt>Clicks</dt><dd>{numeric(cluster.metrics.clicks)}</dd></div><div><dt>Average position</dt><dd>{cluster.metrics.average_position === null ? "Unavailable" : numeric(cluster.metrics.average_position)}</dd></div></dl>
              <details className="technical-details"><summary>Technical details</summary><p>Cluster: <code>{cluster.id}</code></p><Evidence siteId={siteId} snapshot={snapshot} ids={cluster.evidence_ids}/></details>
              <p>{cluster.gaps.length ? cluster.gaps.map(label).join(" · ") : "No gap signal under the current policy."}</p>
              <TopicMembers key={`${snapshot.id}:${cluster.id}`} cluster={cluster} siteId={siteId} snapshot={snapshot}/>
              <details className="technical-details"><summary>Technical details</summary><p>Ranking pages: {cluster.ranking_pages === null ? "Unavailable; no page dimension" : cluster.ranking_pages.join(" · ") || "None in returned rows"}</p><p>Targeting pages: {!cluster.targeting_assessed ? "Unassessed; no successful crawl pages" : cluster.targeting_pages.join(" · ") || "None in observed titles"}</p><pre>{JSON.stringify(cluster.coverage,null,2)}</pre></details>
              {cluster.ideas.map(idea => <div className="seo-finding" key={idea.query}><p>{idea.query} · <strong>{idea.label}</strong></p><details className="technical-details"><summary>Technical details</summary><Evidence siteId={siteId} snapshot={snapshot} ids={idea.evidence_ids}/></details>{idea.volumes.map((volume,index) => <TopicVolumeEvidence key={index} volume={volume} siteId={siteId} snapshot={snapshot}/>)}<a href="/strategy">Review idea proposal in Strategy</a></div>)}
              {item && (decision ? <p>{decision.target_kind === "brief" ? <a href="/content-writer">Review brief proposal</a> : "Dismissed by the owner."}</p> : <div className="seo-actions"><button className="primary-command" disabled={busy || !item.action} onClick={() => void command("decide",{snapshot_id:snapshot.id,item_id:item.id,decision:"accepted"})}><Check size={14} aria-hidden="true"/>Accept proposal</button><button className="button secondary" disabled={busy} onClick={() => void command("decide",{snapshot_id:snapshot.id,item_id:item.id,decision:"dismissed"})}><X size={14} aria-hidden="true"/>Dismiss</button>{item.unavailable_reason && <p>{item.unavailable_reason}</p>}</div>)}
            </li>;
          })}</ol><Pagination page={page} count={snapshot.payload.topics.clusters.length} setPage={setPage}/>
        </>}
      </>}
      {view === "overview" && <>
        {Object.keys(snapshot.payload.headline).length ? <dl className="seo-headline">{Object.entries(snapshot.payload.headline).map(([key,value]) => <div key={key}><dt>{label(key)}</dt><dd><Measurement siteId={siteId} snapshot={snapshot} value={value}/></dd></div>)}</dl> : <p>No headline measurements are available in this snapshot.</p>}
        <div className="seo-table-wrap"><table><caption>Source coverage</caption><thead><tr><th>Source</th><th>Evidence and coverage</th></tr></thead><tbody>{Object.entries(snapshot.payload.sources).filter(([,source]) => typeof source === "object" && source !== null && "records" in source).map(([key,source]) => {
          const s = source as {reason:string|null; records:{id:string;coverage?:unknown}[]};
          return <tr key={key}><th>{label(key)}</th><td>{s.records.length ? <><Evidence siteId={siteId} snapshot={snapshot} ids={s.records.map(r => r.id)}/><details><summary>Coverage</summary><pre>{JSON.stringify(s.records.map(r => r.coverage ?? {scope:"observed records only"}),null,2)}</pre></details></> : s.reason}</td></tr>;
        })}</tbody></table></div>
        {snapshot.payload.performance.filter(c => c.dimensions.includes("query") || c.dimensions.includes("page")).map(c => <Performance key={c.evidence_id} cohort={c} snapshot={snapshot} siteId={siteId}/>)}
      </>}
      {view === "pages" && <>
        {!snapshot.payload.pages.length ? <p>No successful page evidence in the pinned crawl.</p> : <div className="seo-table-wrap"><table><caption>Observed pages</caption><thead><tr><th>Page</th><th>Findings</th><th>Per-source metrics</th></tr></thead><tbody>{snapshot.payload.pages.slice(page*25,(page+1)*25).map(p => <tr key={p.evidence_id}><td><strong>{p.title || "Untitled page"}</strong><p className="seo-url">{p.url}</p><Evidence siteId={siteId} snapshot={snapshot} ids={[p.evidence_id]}/></td><td>{p.findings.length ? p.findings.map(f => <div className="seo-finding" key={f.id}>{f.severity}: {f.title}<Evidence siteId={siteId} snapshot={snapshot} ids={[f.source_id]}/></div>) : "No findings in the pinned report; not a clean bill of health."}</td><td>{p.metrics.length ? p.metrics.map((m,index) => <div key={index}>{label(m.source)}: {m.window?.start} to {m.window?.end}{Object.entries(m.row.metrics).map(([key,value]) => <div key={key}>{label(key)} <Measurement siteId={siteId} snapshot={snapshot} value={value}/></div>)}</div>) : "No page metrics imported."}</td></tr>)}</tbody></table></div>}
        <Pagination page={page} count={snapshot.payload.pages.length} setPage={setPage}/>
      </>}
      {view === "strategy" && <>
        <p className="seo-method"><span className="pill">Deterministic fallback</span> Ranked by expected impact, confidence, effort and what has worked on this site before. This is not a prediction of ranking gains. Phases run in order; anything beyond them is the backlog.</p>
        <ProviderResearch siteId={siteId} snapshot={snapshot}/>
        {!snapshot.payload.strategy.items.length && <p>No supported work candidates in this snapshot.</p>}
        <ol className="seo-items">{snapshot.payload.strategy.items.slice(page*25,(page+1)*25).map(item => {
          const decision = data?.decisions.find(d => d.item_id === item.id);
          return <li key={item.id}><div className="seo-heading"><h3>{item.title}</h3><span className="pill">{readableCode(decision?.decision ?? "proposed")}</span></div><p>{label(item.kind)} · {label(item.phase)}{item.target && <> · <span className="seo-url">{item.target}</span></>}</p><p>Priority {numeric(item.priority.score)} · about {numeric(item.priority.effort_days)} {item.priority.effort_days === 1 ? "day" : "days"} of work</p><details className="technical-details"><summary>Rationale and inputs</summary><p>Priority {numeric(item.priority.score)} = impact {numeric(item.priority.impact_proxy)} × confidence {numeric(item.priority.confidence)} × effort {numeric(item.priority.effort_factor)} × observed effect {numeric((item.priority.inputs.observed_effectiveness as {factor:number} | undefined)?.factor ?? 1)}</p><Evidence siteId={siteId} snapshot={snapshot} ids={item.evidence_ids}/><pre>{JSON.stringify(item.priority.inputs,null,2)}</pre><p>{item.measurement}</p><p>{item.autonomy}</p></details>{item.unavailable_reason && <p>Unavailable: {item.unavailable_reason}</p>}
            {decision ? <p>{decision.target_kind === "brief" ? <a href="/content-writer">Review brief proposal</a> : decision.target_kind === "inbox" ? <a href={`/approvals?revision=${decision.target_id}`}>Review exact Inbox revision</a> : "Dismissed by the owner."}</p> : <div className="seo-actions"><button className="primary-command" disabled={busy || !item.action} onClick={() => void command("decide",{snapshot_id:snapshot.id,item_id:item.id,decision:"accepted"})}><Check size={14} aria-hidden="true"/>Accept proposal</button><button className="secondary-command" disabled={busy} onClick={() => void command("decide",{snapshot_id:snapshot.id,item_id:item.id,decision:"dismissed"})}><X size={14} aria-hidden="true"/>Dismiss</button></div>}
          </li>;
        })}</ol><Pagination page={page} count={snapshot.payload.strategy.items.length} setPage={setPage}/>
      </>}
      {view === "analytics" && <>
        {!snapshot.payload.performance.length && <p>GSC and Bing trends unavailable: no current performance imports.</p>}
        {snapshot.payload.performance.map(cohort => <Performance key={cohort.evidence_id} cohort={cohort} snapshot={snapshot} siteId={siteId}/>)}
      </>}
      {snapshot.payload.unavailable.length > 0 && <details className="seo-unavailable-details"><summary>Sources not available yet ({snapshot.payload.unavailable.length})</summary><ul className="seo-unavailable">{snapshot.payload.unavailable.map(s => <li key={s.source}><strong>{label(s.source)}</strong><span>{s.reason}</span></li>)}</ul></details>}
    </>}
  </section>;
}
function ProviderResearch({siteId,snapshot}: {siteId:string;snapshot:SeoSnapshot}) {
  const records = Object.entries(snapshot.payload.evidence).filter(([,r]) => r.kind.startsWith("dataforseo_"));
  if (!records.length) return null;
  return <section className="seo-cohort"><h3>DataForSEO research</h3><p>Provider-reported research, not a ranking prediction or private competitor analytics.</p>
    {records.map(([id,{record}]) => {
      if (record.result === null || typeof record.result !== "object" || Array.isArray(record.result)) return null;
      const result = record.result as Record<string,unknown>;
      return <div className="seo-finding" key={id}><p>{String(result.subject)}: {label(String(result.kind))}. As reported by DataForSEO, {String(record.recorded_at).slice(0,10)}.</p>
        {result.kind === "volume" && <p>Search volume: {typeof result.search_volume === "number" ? numeric(result.search_volume) : "Unavailable"}.</p>}
        {result.kind === "serp" && Array.isArray(result.competitors) && <ul>{result.competitors.map((c: {rank:number;domain:string}) => <li key={c.rank}>Position {c.rank}: {c.domain}</li>)}</ul>}
        {result.kind === "backlinks" && <p>Backlinks: {String(result.backlinks)}. Referring domains: {String(result.referring_domains)}.</p>}
        <details className="technical-details"><summary>Technical details</summary><Evidence siteId={siteId} snapshot={snapshot} ids={[id]}/><pre>{JSON.stringify(result,null,2)}</pre></details>
      </div>;
    })}
  </section>;
}
function TopicMembers({cluster,siteId,snapshot}: {cluster:TopicCluster;siteId:string;snapshot:SeoSnapshot}) {
  const [page,setPage] = useState(0);
  return <>
    <div className="seo-table-wrap"><table><caption>Observed queries</caption><thead><tr><th>Query</th><th>Provider volume</th></tr></thead><tbody>{cluster.members.slice(page*25,(page+1)*25).map(member => <tr key={member.query}><td>{member.query}<details><summary>Membership evidence</summary><p>Variants: {member.variants.join(" · ")}</p><p>Shared terms: {member.membership.shared_terms.join(" · ")}. Term overlap: {numeric(member.membership.term_jaccard)}.</p><p>Shared bigrams: {member.membership.shared_bigrams.map(g => g.join(" ")).join(" · ") || "None"}</p></details></td><td>{member.volumes.length ? member.volumes.map((volume,index) => <TopicVolumeEvidence key={index} volume={volume} siteId={siteId} snapshot={snapshot}/>) : "Unavailable; no volume data"}</td></tr>)}</tbody></table></div>
    <Pagination page={page} count={cluster.members.length} setPage={setPage}/>
  </>;
}
function TopicVolumeEvidence({volume,siteId,snapshot}: {volume:TopicVolume;siteId:string;snapshot:SeoSnapshot}) {
  return <div><p>{volume.value === null ? "Unavailable" : numeric(volume.value)} · {volume.label} · as reported by DataForSEO, {formatDay(volume.recorded_at)}</p><details className="technical-details"><summary>Technical details</summary><p>Language: <code>{volume.language_code}</code> · Location: <code>{volume.location_code}</code></p><p>Recorded: {volume.recorded_at}</p><Evidence siteId={siteId} snapshot={snapshot} ids={volume.evidence_ids}/></details></div>;
}
function Learning({siteId,snapshot}: {siteId:string;snapshot:SeoSnapshot}) {
  const learning = snapshot.payload.learning;
  const [page,setPage] = useState(0);
  const declining = learning?.decay.pages.filter(p => p.declining) ?? [];
  return <>
    <h3>Observed effectiveness</h3>
    {!learning ? <p>Unavailable in this historical snapshot. Refresh baseline to read current measurements.</p> : <>
      <p>{learning.effectiveness.label} {learning.effectiveness.reason}</p>
      {!learning.effectiveness.groups.length ? <p>No eligible 28/90-day samples. Priority factor remains neutral (1).</p> : <div className="seo-table-wrap"><table><caption>Site-local measured deltas, as reported</caption><thead><tr><th>Work / recipe</th><th>Horizon</th><th>Samples / weighted</th><th>Mean deltas</th><th>Priority factor</th></tr></thead><tbody>{learning.effectiveness.groups.map(g => <tr key={`${g.work_type}:${g.recipe_key}:${g.horizon}`}><th>{label(g.work_type)}<p>{g.recipe_key === null ? "All recipes" : label(g.recipe_key)}</p></th><td>{g.horizon} days</td><td>{g.sample_size} / {numeric(g.effective_sample_size)}</td><td>{Object.entries(g.metrics).map(([key,m]) => <div key={key}>{label(key)}: {m.mean_delta === null ? "Unavailable" : numeric(m.mean_delta)} (n={m.sample_size})</div>)}<Evidence siteId={siteId} snapshot={snapshot} ids={g.evidence_ids}/></td><td>{numeric(g.factor)}<p>Shrinkage {numeric(g.shrinkage)}</p></td></tr>)}</tbody></table></div>}
      <h3>Declining pages</h3><p>{label(learning.decay.state)}: {learning.decay.reason}</p>
      {!declining.length ? <p>No material decline detected in usable returned page cohorts. Missing cohorts remain unknown.</p> : <div className="seo-table-wrap"><table><caption>Observed declines awaiting diagnosis</caption><thead><tr><th>Page</th><th>Comparison</th><th>Counts</th><th>Evidence</th></tr></thead><tbody>{declining.slice(page*25,(page+1)*25).map(p => {
        const comparison = p.basis === "year_over_year" ? p.year_over_year! : p.prior_28_days;
        return <tr key={p.url}><td className="seo-url">{p.url}</td><td>{label(p.basis)}<p>{p.seasonality_possible ? "Seasonality possible" : "Year-over-year preferred; festival dates may still shift"}</p>{comparison.baseline.window.start} to {comparison.baseline.window.end}<p>Recent: {comparison.recent.window.start} to {comparison.recent.window.end}</p></td><td>{Object.entries(comparison.metrics ?? {}).map(([key,m]) => <div key={key}>{label(key)}: {numeric(m.baseline)} to {numeric(m.recent)}{m.declining && " (material decline)"}</div>)}</td><td><Evidence siteId={siteId} snapshot={snapshot} ids={p.evidence_ids}/><details><summary>Comparison and coverage</summary><pre>{JSON.stringify(p,null,2)}</pre></details><a href="/strategy">Review refresh proposals</a></td></tr>;
      })}</tbody></table></div>}
      <Pagination page={page} count={declining.length} setPage={setPage}/>
      {learning.decay.excluded_pages.length > 0 && <details><summary>Pages under Signal measurement ({learning.decay.excluded_pages.length})</summary><ul>{learning.decay.excluded_pages.map(url => <li key={url} className="seo-url">{url}</li>)}</ul></details>}
      {learning.decay.pages.some(p => p.state === "partial") && <details><summary>Partial page comparisons</summary><ul>{learning.decay.pages.filter(p => p.state === "partial").map(p => <li key={p.url}><span className="seo-url">{p.url}</span><p>Missing or lagged page-days; decline unavailable.</p><Evidence siteId={siteId} snapshot={snapshot} ids={p.evidence_ids}/></li>)}</ul></details>}
    </>}
  </>;
}
function Pagination({page,count,setPage}: {page:number;count:number;setPage:(value:number)=>void}) {
  return count > 25 ? <div className="seo-actions"><button className="button secondary" disabled={page === 0} onClick={() => setPage(page-1)}>Previous</button><span>Page {page+1} of {Math.ceil(count/25)}</span><button className="button secondary" disabled={(page+1)*25>=count} onClick={() => setPage(page+1)}>Next</button></div> : null;
}
function Performance({cohort,snapshot,siteId}: {cohort:PerformanceCohort;snapshot:SeoSnapshot;siteId:string}) {
  const [page,setPage] = useState(0);
  const rows = [...cohort.rows].sort((a,b) => (a.labels.date ?? "").localeCompare(b.labels.date ?? "") || b.metrics.impressions!.value-a.metrics.impressions!.value);
  const bingPages = cohort.source === "bing" && cohort.dimensions.includes("page");
  return <section className="seo-cohort"><h3>{cohort.source === "gsc" ? "Google Search Console" : "Bing Webmaster Tools"} · {cohort.dimensions.join(" / ")}</h3><p>{cohort.window ? `${cohort.window.start} to ${cohort.window.end}` : "Coverage window unavailable"}. {cohort.total_scope}. Missing dates are unknown, not zero.</p><dl className="seo-headline">{Object.entries(cohort.totals).map(([key,value]) => <div key={key}><dt>{label(key)}</dt><dd><Measurement siteId={siteId} snapshot={snapshot} value={value}/></dd></div>)}</dl><details><summary>Import coverage</summary><pre>{JSON.stringify(cohort.coverage,null,2)}</pre></details>
    {!cohort.dimensions.includes("date") && <p>Daily trends unavailable for this import: no date dimension.</p>}
    {bingPages && <p>As reported by Bing. Top-page coverage is incomplete; date granularity is unknown. Named positions are not combined with GSC position.</p>}
    <div className="seo-table-wrap"><table><caption>{bingPages ? "Observed top-page rows; cohorts are not combined" : cohort.dimensions.includes("date") ? "Observed daily trend; cohorts are not combined" : "Top observed rows by impressions"}</caption><thead><tr>{cohort.dimensions.map(d => <th key={d}>{label(d)}</th>)}<th>Clicks</th><th>Impressions</th>{bingPages && <><th>Average click position</th><th>Average impression position</th></>}{cohort.source === "gsc" && <><th>CTR</th><th>Position</th></>}</tr></thead><tbody>{rows.slice(page*25,(page+1)*25).map((row,index) => <tr key={index}>{cohort.dimensions.map(d => <td key={d}>{row.labels[d]}</td>)}<td><Measurement siteId={siteId} snapshot={snapshot} value={row.metrics.clicks!}/></td><td><Measurement siteId={siteId} snapshot={snapshot} value={row.metrics.impressions!}/></td>{bingPages && ["avg_click_position","avg_impression_position"].map(k => <td key={k}>{row.metrics[k] ? <Measurement siteId={siteId} snapshot={snapshot} value={row.metrics[k]}/> : "Unavailable"}</td>)}{cohort.source === "gsc" && <><td>{row.metrics.ctr ? <Measurement siteId={siteId} snapshot={snapshot} value={row.metrics.ctr}/> : "Unavailable"}</td><td>{row.metrics.position ? <Measurement siteId={siteId} snapshot={snapshot} value={row.metrics.position}/> : "Unavailable"}</td></>}</tr>)}</tbody></table></div>
    <Pagination page={page} count={rows.length} setPage={setPage}/>
  </section>;
}
