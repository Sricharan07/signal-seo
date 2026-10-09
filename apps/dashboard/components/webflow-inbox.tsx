"use client";

import { readableCode } from "./home-panels";
import { Check, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { validWebflowData, type WebflowData } from "../lib/webflow-api";

export function WebflowInbox({siteId,initialData}:{siteId:string;initialData?:WebflowData}) {
  const [data,setData]=useState<WebflowData|null>(initialData??null);
  const [notice,setNotice]=useState("");const [busy,setBusy]=useState(false);
  const generation=useRef(0);
  const load=useCallback(async()=>{
    const turn=++generation.current;
    try {const r=await fetch(`/actions/webflow?site_id=${siteId}`,{cache:"no-store"});const result=await r.json();if(!r.ok||!validWebflowData(result))throw new Error();if(turn===generation.current)setData(result);}
    catch {if(turn===generation.current){setData(null);setNotice("Webflow Inbox unavailable.");}}
  },[siteId]);
  useEffect(()=>{if(!initialData)void load();return()=>{generation.current++;};},[initialData,load]);
  async function review(id:string,revision_sha256:string,decision:string) {
    if(busy)return;setBusy(true);setNotice("");
    try {const r=await fetch("/actions/webflow",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema_version:1,site_id:siteId,id,revision_sha256,decision})});if(!r.ok)throw new Error();await load();setNotice("Decision recorded for the exact draft revision.");}
    catch {setNotice("Decision not confirmed. Refresh before retrying.");}
    finally {setBusy(false);}
  }
  return <section className="business-brain webflow-inbox">
    <header className="brain-toolbar"><h2>Webflow drafts</h2><button title="Refresh Webflow Inbox" aria-label="Refresh Webflow Inbox" disabled={busy} onClick={()=>void load()}><RefreshCw size={16}/></button></header>
    <p role="status">Webflow delivery has not been tested against a live Webflow site yet, so drafts are not sent. Review them here.</p>
    <p>Updates unavailable: Webflow API v2 has no documented atomic update precondition.</p>
    <p>Publishing unavailable: Webflow API v2 has no documented atomic publish precondition. The owner publishes in Webflow.</p>
    {notice&&<p role="status">{notice}</p>}
    {!data?<p>{notice?"No drafts loaded.":"Loading Webflow drafts"}</p>:<>
      {!data.bindings.some(b=>!b.revoked_at)&&<p>No active Webflow OAuth binding.</p>}
      {!data.inbox.length&&<p>No sealed Webflow drafts.</p>}
      <ul className="brain-facts">{data.inbox.map(item=><li key={item.id}><div>
        <h3>{item.payload.items[0].fieldData.name}</h3><p>{readableCode(item.decision)} · {readableCode(item.state)}</p>
        {item.state==="OUTCOME_UNKNOWN"&&<p role="status">Quarantined: the draft may exist in Webflow. No second create request is permitted.</p>}
        {item.state==="ESCALATED"&&<p role="status">Owner investigation required: duplicate or conflicting provider items.</p>}
        <details className="technical-details"><summary>Technical details</summary><p>Revision {item.revision_sha256}</p><p>Article candidate {item.candidate_id}</p><p>Collection {data.bindings.find(b=>b.id===item.binding_id)?.collection_id??"unavailable"}</p><pre>{JSON.stringify(item.payload,null,2)}</pre>{item.provider_item&&<p>Webflow item {item.provider_item}</p>}</details>
      </div>{item.decision==="pending"&&item.state==="PLANNED"&&<div className="brain-actions">{(["approved","changes_requested","rejected"] as const).map(decision=><button className={decision==="approved"?"primary-command":undefined} key={decision} disabled={busy} onClick={()=>void review(item.id,item.revision_sha256,decision)}>{decision==="approved"?<Check size={16}/>:<X size={16}/>} {decision==="approved"?"Approve draft":decision==="rejected"?"Reject":"Request changes"}</button>)}</div>}</li>)}</ul>
    </>}
  </section>;
}
