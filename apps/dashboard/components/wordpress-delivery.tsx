"use client";

import { readableCode } from "./home-panels";
import { useCallback, useEffect, useState } from "react";
import { Check, FilePenLine, RefreshCw, Unplug, X } from "lucide-react";
import { PUBLISH_REASON, QUARANTINE_REASON, UPDATES_REASON, validWordPressData, type WordPressData } from "../lib/wordpress-api";

export function WordPressDelivery({siteId,inbox=false,initialData}:{siteId:string;inbox?:boolean;initialData?:WordPressData}) {
  const [data,setData]=useState<WordPressData|null>(initialData??null);
  const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState("");
  const [bindingId,setBindingId]=useState("");
  const load=useCallback(async()=>{try {const r=await fetch(`/actions/wordpress?site_id=${siteId}`,{cache:"no-store"});const value=await r.json();if(!r.ok || !validWordPressData(value))throw new Error();setData(value);setNotice("");}catch{setData(null);setNotice("WordPress delivery is unavailable. Refresh to retry.");}},[siteId]);
  useEffect(()=>{if(!initialData)void load();},[initialData,load]);
  async function act(command:Record<string,unknown>) {setBusy(true);try{const r=await fetch("/actions/wordpress",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema_version:1,site_id:siteId,command})});if(!r.ok)throw new Error();const result=await r.json();await load();setNotice(readableCode(String(result.state)));}catch{setNotice("The outcome is not confirmed. Refresh before taking another action.");}finally{setBusy(false);}}
  const bindings=data?.bindings.filter(b=>b.current)??[];
  const binding=bindings.find(b=>b.id===bindingId)??bindings[0];
  const drafts=data?.drafts?.filter(d=>!data.candidates.some(c=>c.draft_id===d.draft_id&&c.binding_id===binding?.id))??[];
  return <section className="business-brain wordpress-delivery" aria-label={inbox?"WordPress Inbox":"WordPress connector"}>
    <header className="brain-toolbar"><h2>{inbox?"WordPress Inbox":"WordPress"}</h2><button aria-label="Refresh WordPress" title="Refresh WordPress" disabled={busy} onClick={()=>void load()}><RefreshCw size={16}/></button></header>
    {notice&&<p role="status">{notice}</p>}
    {!inbox&&<><p>{UPDATES_REASON}</p><p>{PUBLISH_REASON}</p></>}
    {!data?<p role="status">{notice?"No WordPress data loaded.":"Loading WordPress"}</p>:<>
      <p role="status">{data.provider_state==="unavailable"?"Unavailable: WordPress provider is not configured.":binding?`Connected to ${binding.origin}`:"No current WordPress binding."}</p>
      {!inbox&&<>
        {data.bindings.some(b=>!b.current)&&<details><summary>Unavailable bindings</summary><ul className="brain-facts">{data.bindings.filter(b=>!b.current).map(b=><li key={b.id}><div><h3>{b.origin}</h3><p>Stale or revoked</p><details className="technical-details"><summary>Technical details</summary><p>Stale or revoked: user {b.user_id}</p><p className="wordpress-digest">{b.id}</p></details></div><div className="brain-actions"><button disabled={busy} onClick={()=>void act({operation:"revoke",binding_id:b.id})}><Unplug size={16}/>Revoke binding</button></div></li>)}</ul></details>}
        {binding?<><label>Publishing connection<select value={binding.id} disabled={busy} onChange={e=>setBindingId(e.currentTarget.value)}>{bindings.map(b=><option key={b.id} value={b.id}>{b.origin} ({b.observed.roles.join(", ")})</option>)}</select></label><details className="technical-details"><summary>Technical details</summary><p>Binding {binding.id}</p><p>Bound user {binding.user_id}</p><ul>{Object.entries(binding.observed.capabilities).filter(([,value])=>value).map(([name])=><li key={name}>{name}</li>)}</ul></details><button disabled={busy} onClick={()=>void act({operation:"revoke",binding_id:binding.id})}><Unplug size={16}/>Revoke binding</button></>:<p>An operator must provision a dedicated least-privilege WordPress user and application password in OpenBao, then bind it to this verified site. Self-service connection is unavailable.</p>}
        {binding&&(drafts.length?<form className="brain-form" onSubmit={e=>{e.preventDefault();void act({operation:"seal",binding_id:binding.id,draft_id:new FormData(e.currentTarget).get("draft_id")});}}><label>Article<select name="draft_id" required disabled={busy}>{drafts.map(d=><option key={d.draft_id} value={d.draft_id}>{d.title}</option>)}</select></label><button className="primary-command" disabled={busy}><Check size={16}/>Seal for review</button></form>:<p>No reviewable articles are ready. Prepare an original article from an accepted brief in Articles.</p>)}
      </>}
      <ul className="brain-facts">{data.candidates.map(c=><li key={c.id}><div><h3>{c.payload.title}</h3><p>{c.decision} · New draft only</p><details><summary>Sealed revision</summary><details className="technical-details"><summary>Technical details</summary><p className="wordpress-digest">{c.revision_sha256}</p></details><h4>Content</h4><pre>{c.payload.content}</pre><h4>Excerpt</h4><p>{c.payload.excerpt}</p></details></div><div className="brain-actions">{c.decision==="pending"&&(["approved","rejected"] as const).map(decision=><button key={decision} className={decision==="approved"?"primary-command":undefined} disabled={busy||!binding} onClick={()=>void act({operation:"review",candidate_id:c.id,revision_sha256:c.revision_sha256,decision})}>{decision==="approved"?<Check size={16}/>:<X size={16}/>} {decision==="approved"?"Approve draft":"Reject"}</button>)}{c.decision==="approved"&&!data.intents.some(i=>i.candidate_id===c.id)&&<button className="primary-command" disabled={busy||!binding||data.provider_state!=="available"} onClick={()=>void act({operation:"create",candidate_id:c.id,intent_id:crypto.randomUUID(),prior_intent_id:null})}><FilePenLine size={16}/>Create draft</button>}</div></li>)}</ul>
      {inbox&&!data.candidates.length&&<p>No sealed WordPress candidates.</p>}
      <ul className="brain-facts">{data.intents.map(i=>{
        const quarantined=["outcome_unknown","dispatching","escalated"].includes(i.state);
        return <li key={i.id}><div>
          <h3>{data.candidates.find(c=>c.id===i.candidate_id)?.payload.title??"WordPress intent"}</h3>
          <p>{quarantined?i.state==="escalated"?"Multiple matching drafts: owner review required":"Quarantined: outcome unknown":readableCode(i.state)}</p>
          {quarantined&&<p>{QUARANTINE_REASON}</p>}
          {i.reconcile_count>=12&&<p>Read-only reconciliation limit reached. Owner review required.</p>}
          {i.observation==="edited_before_publish"&&<p>Edited before publish. The published draft differs from the sealed revision.</p>}
          <details className="technical-details"><summary>Technical details</summary><p className="wordpress-digest">{i.marker}</p></details>
          {i.post_url&&<a href={i.post_url} target="_blank" rel="noreferrer">View in WordPress</a>}
          {quarantined&&<details><summary>Request a separate draft</summary><form onSubmit={e=>{e.preventDefault();void act({operation:"create",candidate_id:i.candidate_id,intent_id:crypto.randomUUID(),prior_intent_id:i.id});}}><label className="writer-checkbox"><input type="checkbox" required/>The old draft may still appear. Keep reconciling it.</label><button disabled={busy||!binding||data.provider_state!=="available"}><FilePenLine size={16}/>Create separate draft</button></form></details>}
        </div><div className="brain-actions">
          {quarantined&&<button disabled={busy||!binding||i.reconcile_count>=12||data.provider_state!=="available"} onClick={()=>void act({operation:"reconcile",intent_id:i.id})}><RefreshCw size={16}/>Check for draft</button>}
          {["queued","retry"].includes(i.state)&&<button disabled={busy||!binding||data.provider_state!=="available"} onClick={()=>void act({operation:"create",candidate_id:i.candidate_id,intent_id:i.id,prior_intent_id:null})}><RefreshCw size={16}/>{i.state==="retry"?"Retry unapplied request":"Resume draft request"}</button>}
          {i.state==="recorded"&&<button disabled={busy||!binding||i.reconcile_count>=12||data.provider_state!=="available"} onClick={()=>void act({operation:"observe",intent_id:i.id})}><RefreshCw size={16}/>Observe publication</button>}
        </div></li>;
      })}</ul>
    </>}
  </section>;
}
