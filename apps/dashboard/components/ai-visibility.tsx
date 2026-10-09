"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ListPlus, RefreshCw, X } from "lucide-react";
import { validVisibilityData, type VisibilityData, type VisibilityObservation, type VisibilityProposal } from "../lib/ai-visibility-api";
import { readableCode } from "./home-panels";
import { AiQuestionSet } from "./ai-question-set";

function StructuredRecipe({ proposal, busy, act }: { proposal: VisibilityProposal; busy: boolean; act: (action: string, values: Record<string, unknown>) => Promise<void> }) {
  if (proposal.sealing?.state === "sealed") return <><p><a href="/approvals">Review the exact change in the Inbox</a></p><details className="technical-details"><summary>Technical details</summary><p>Revision {proposal.sealing.revision_id} / Digest {proposal.sealing.revision_sha256}</p></details></>;
  if (proposal.sealing?.state !== "available") return proposal.sealing ? <p role="status">{proposal.sealing.reason}</p> : null;
  const fields = proposal.payload.schema_type === "Article" ? ["headline"] : proposal.payload.schema_type === "FAQPage" ? ["question", "answer"] : ["name", "description"];
  return <form className="brain-form" onSubmit={event => { event.preventDefault(); const values = new FormData(event.currentTarget); const fact_fields = Object.fromEntries(fields.flatMap(field => values.get(field) ? [[field, values.get(field)]] : [])); void act("seal", { proposal_id: proposal.id, digest: proposal.digest, fact_fields }); }}>
    {fields.map(field => <label key={field}>{field === "description" ? "Description (optional)" : readableCode(field)}<select name={field} required={field !== "description"} disabled={busy} defaultValue=""><option value="">Choose an approved fact</option>{proposal.payload.claims.flatMap(claim => claim.fact_ids.map(ref => <option key={ref} value={ref}>{claim.text}</option>))}</select></label>)}
    <button className="primary-command" disabled={busy}><ListPlus size={16} />Prepare change for Inbox</button>
  </form>;
}

const PROVIDERS: Record<string, string> = { openai: "ChatGPT (OpenAI API)", perplexity: "Perplexity", gemini: "Gemini" };
const provider = (value: string) => PROVIDERS[value] ?? readableCode(value);
const day = (value: string) => { const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(date); };

function Observation({ value }: { value: VisibilityObservation }) {
  return <li><p><strong>{provider(value.provider)}</strong> · {value.model} · <time dateTime={value.observed_at}>{day(value.observed_at)}</time></p>
    <p>{value.site_cited === null ? `Incomplete: ${readableCode(value.failure_code ?? "").toLowerCase()}` : value.site_cited ? "Site cited" : "Site not cited"}</p>
    {value.cited_pages.length>0 && <div><strong>Cited site pages</strong><ul>{value.cited_pages.map(url=><li key={url}><a href={url} target="_blank" rel="noreferrer">{url}</a></li>)}</ul></div>}
    {value.competitor_pages.length>0 && <div><strong>Other cited pages</strong><ul>{value.competitor_pages.map(url=><li key={url}><a href={url} target="_blank" rel="noreferrer">{url}</a></li>)}</ul></div>}
    <details className="technical-details"><summary>Technical details</summary><small>Observation {value.id}{value.provider_evidence_id && ` / Provider evidence ${value.provider_evidence_id}`}</small></details>
  </li>;
}
export function AiVisibility({ siteId, initialData }: { siteId: string; initialData?: VisibilityData }) {
  const [data,setData]=useState<VisibilityData|null>(initialData??null);
  const [notice,setNotice]=useState("");const [busy,setBusy]=useState(false);
  const load=useCallback(async()=>{try{const r=await fetch(`/actions/ai-visibility?site_id=${siteId}`,{cache:"no-store"});const value: unknown=await r.json();if(!r.ok||!validVisibilityData(value))throw new Error();setData(value);setNotice("");}catch{setData(null);setNotice("AI answers are unavailable. Refresh to retry.");}},[siteId]);
  useEffect(()=>{if(!initialData)void load();},[initialData,load]);
  async function act(action: string, values: Record<string,unknown>={}) {
    setBusy(true);setNotice("");
    try{const r=await fetch("/actions/ai-visibility",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema_version:1,site_id:siteId,action,...values})});if(!r.ok)throw new Error();await load();setNotice(action==="seal"?"Change prepared. Review the exact revision in the Inbox. Nothing shipped.":action==="prepare"?"Recommendations prepared. Nothing accepted or shipped.":values.decision==="accepted"?"Accepted for review. No publishing authority granted.":"Recommendation dismissed.");}catch{setNotice("The request could not be confirmed. Refresh before retrying.");}finally{setBusy(false);}
  }
  return <div className="business-brain ai-visibility">
    <header className="brain-toolbar"><h2>Questions your buyers ask</h2><div className="brain-actions"><button disabled={busy||data?.state!=="available"||!data.gaps.some(g=>g.observations.some(o=>o.site_cited===false))} onClick={()=>void act("prepare")}><ListPlus size={16}/>Prepare recommendations</button><button title="Refresh AI answers" aria-label="Refresh AI answers" disabled={busy} onClick={()=>void load()}><RefreshCw size={16}/></button></div></header>
    {notice&&<p role="status">{notice}</p>}
    {!data?<p role="status">{notice?"No visibility evidence loaded.":"Loading AI answers"}</p>:<>
      <p>{data.comparison_wording}</p>
      <AiQuestionSet key={siteId} siteId={siteId} onApproved={load} />
      {data.state!=="available"&&<p role="status">{data.state==="origin_unavailable"?"Verified site origin required.":"Evidence exceeds the bounded projection. No partial baseline is presented."}</p>}
      <section className="settings-section"><h2>Observation availability</h2><ul>{data.providers.map(p=><li key={p.provider}><strong>{provider(p.provider)}</strong>: {readableCode(p.state).toLowerCase()}<p>{p.reason}</p></li>)}</ul><p>Scheduled observations: {data.reobservation.reason}</p><p>On-demand observation unavailable: {data.on_demand_observation.reason}</p></section>
      <section className="settings-section"><h2>Question gaps</h2>{!data.gaps.length&&<p>No target-question observations yet.</p>}
        <ul className="brain-facts">{data.gaps.map(g=><li key={g.id}><div><h3>{g.question}</h3><details className="technical-details"><summary>Technical details</summary><small>Question version {g.question_set_id} / Crawl manifest {g.crawl_manifest_id}</small></details>
          {!g.observations.length&&<p>No observations for this question.</p>}<ul className="visibility-observations">{g.observations.map(o=><Observation key={o.id} value={o}/>)}</ul>
          <h4>Relevant crawled pages</h4>{!g.relevant_pages.length&&<p>No evidence-supported match.</p>}<ul>{g.relevant_pages.map(p=><li key={p.id}><a href={p.url} target="_blank" rel="noreferrer">{p.title??p.url}</a><p>{p.url}</p><small title={`Crawl evidence ${p.id}`}>Matched terms: {p.matched_terms.join(", ")}</small></li>)}</ul>
          <details><summary>Observation history ({g.history.length})</summary><ul className="visibility-observations">{g.history.map(o=><Observation key={o.id} value={o}/>)}</ul></details>
        </div></li>)}</ul>
      </section>
      <section className="settings-section"><h2>Recommendations</h2>{!data.proposals.length&&<p>No recommendations yet. Current approved facts and a relevant crawled page are required.</p>}
        <ul className="brain-facts">{data.proposals.map(p=><li key={p.id}><div><h3>{p.kind==="content"?"Content refresh brief":p.kind==="structured_data"?`${p.payload.schema_type} structured data`:"Internal links"}</h3>
          <p>{readableCode(p.decision)} · {readableCode(p.payload.state).toLowerCase()}</p><a href={p.payload.page_url} target="_blank" rel="noreferrer">{p.payload.page_url}</a><p>{p.payload.rationale}</p>{p.payload.reason&&!(p.decision==="accepted"&&p.sealing&&p.sealing.state!=="available")&&<p role="status">{p.payload.reason}</p>}
          <p>From {provider(p.payload.provider)} · {p.payload.model} · <time dateTime={p.payload.observed_at}>{day(p.payload.observed_at)}</time></p>
          <details className="technical-details"><summary>Technical details</summary><p>Observation {p.observation_id} / Provider evidence {p.payload.provider_evidence_id}</p><p>Crawl evidence {p.page_id} / Manifest {p.payload.manifest_id}</p>{p.payload.supporting_page_labels&&<p>Page labels: {p.payload.supporting_page_labels}</p>}
            <ul>{p.payload.claims.map((c,index)=><li key={index}><p>{c.text}</p>{c.fact_ids.map(id=><a key={id} href={`/actions/business-brain?site_id=${siteId}&resource=provenance&fact_id=${id}`} target="_blank" rel="noreferrer">Approved fact {id}</a>)}</li>)}</ul>
            <p>Originality: {readableCode(p.payload.originality.state)}</p>{p.payload.grounding.sentences.filter(s=>s.reasons.length).map(s=><p key={s.path}>{s.sentence}: {s.reasons.map(readableCode).join(", ")}</p>)}
            {p.payload.recipe_inputs&&<><h4>Existing recipe inputs</h4><pre>{JSON.stringify(p.payload.recipe_inputs,null,2)}</pre><a href="/approvals">Inbox</a></>}
            <small>Proposal {p.id} / Digest {p.digest}</small>
          </details>
          {p.brief_id&&<p><a href="/content-writer" title={`Brief ${p.brief_id}`}>Open the brief in Articles</a></p>}
          {p.kind==="structured_data"&&p.decision==="accepted"&&<StructuredRecipe proposal={p} busy={busy} act={act} />}
        </div>{p.decision==="proposed"&&<div className="brain-actions"><button title={p.kind==="content"?"Accept brief proposal":"Acknowledge recommendation only"} disabled={busy||p.payload.state!=="ready"} onClick={()=>void act("decide",{proposal_id:p.id,digest:p.digest,decision:"accepted"})}><Check size={16}/>{p.kind==="content"?"Accept brief":"Acknowledge"}</button><button disabled={busy} onClick={()=>void act("decide",{proposal_id:p.id,digest:p.digest,decision:"dismissed"})}><X size={16}/>Dismiss</button></div>}</li>)}</ul>
      </section>
    </>}
  </div>;
}
