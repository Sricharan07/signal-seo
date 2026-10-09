"use client";

import { readableCode } from "./home-panels";
import { useEffect, useState } from "react";
import { RefreshCw, Save } from "lucide-react";
import { validVisibilitySchedule, type VisibilityScheduleData } from "../lib/visibility-schedule-api";

const money=(micros:number)=>new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",minimumFractionDigits:2,maximumFractionDigits:6}).format(micros/1_000_000);
export function VisibilitySchedule({siteId, initialData}:{siteId:string;initialData?:VisibilityScheduleData}) {
  const [data,setData]=useState<VisibilityScheduleData|null>(initialData??null);
  const [notice,setNotice]=useState("");const [busy,setBusy]=useState(false);const [refresh,setRefresh]=useState(0);
  useEffect(()=>{if(initialData)return;const controller=new AbortController();setData(null);setNotice("");void fetch(`/actions/visibility-schedule?site_id=${siteId}`,{cache:"no-store",signal:controller.signal}).then(async r=>{const v:unknown=await r.json();if(!r.ok||!validVisibilitySchedule(v,siteId))throw new Error();setData(v);}).catch(()=>{if(!controller.signal.aborted)setNotice("Re-observation schedule unavailable.");});return()=>controller.abort();},[siteId,initialData,refresh]);
  async function save(form:HTMLFormElement){
    const f=new FormData(form);const cap=String(f.get("cap"));const cadence=Number(f.get("cadence"));
    if(!/^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?$/.test(cap)){setNotice("Enter a USD amount with up to six decimal places.");return;}
    const [dollars,fraction=""]=cap.split(".");const micros=Number(dollars)*1_000_000+Number(fraction.padEnd(6,"0"));
    if(!Number.isSafeInteger(micros)||micros>100_000_000||!Number.isInteger(cadence)||cadence<1||cadence>30){setNotice("Cadence must be 1 to 30 days and the cap $0 to $100.");return;}
    setBusy(true);setNotice("");
    try{const r=await fetch("/actions/visibility-schedule",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema_version:1,site_id:siteId,request_id:crypto.randomUUID(),cadence_days:cadence,monthly_cap_micros:micros,enabled:f.get("enabled")==="on"})});const v:unknown=await r.json();if(!r.ok||!validVisibilitySchedule(v,siteId))throw new Error();setData(v);setNotice("Settings saved.");}catch{setNotice("Save outcome unknown. Refresh before retrying.");}finally{setBusy(false);}
  }
  return <section className="settings-section visibility-schedule" aria-labelledby="visibility-schedule-title">
    <header className="brain-toolbar"><h2 id="visibility-schedule-title">Re-observation schedule</h2><button type="button" title="Refresh schedule" aria-label="Refresh schedule" disabled={busy} onClick={()=>setRefresh(n=>n+1)}><RefreshCw size={16}/></button></header>
    {notice&&<p role="status">{notice}</p>}
    {!data?<p role="status">{notice?"No schedule loaded.":"Loading schedule"}</p>:<>
      <p role="status">{data.runtime_state==="unavailable"?"Worker unavailable":!data.enabled?"Paused":!data.authority_current?"Authorization unavailable":data.cap_reached?"Spend cap reached":"Scheduled"}</p>
      <form key={data.settings_id??"default"} className="brain-form" onSubmit={e=>{e.preventDefault();void save(e.currentTarget);}}>
        <label>Cadence (days)<input name="cadence" type="number" min={1} max={30} step={1} required defaultValue={data.cadence_days} disabled={busy}/></label>
        <label>Monthly cap (USD)<input name="cap" type="number" min={0} max={100} step="0.000001" required defaultValue={data.monthly_cap_micros/1_000_000} disabled={busy}/></label>
        <label className="writer-checkbox"><input name="enabled" type="checkbox" defaultChecked={data.enabled} disabled={busy}/>Enabled</label>
        <button disabled={busy} title="Save schedule"><Save size={16}/>Save</button>
      </form>
      <dl className="visibility-usage"><div><dt>Held, including prior uncertainty</dt><dd>{money(data.held_micros)}</dd></div><div><dt>Actual spend</dt><dd>Unpriced</dd></div><div><dt>UTC budget month</dt><dd>{data.month_start}</dd></div></dl>
      <h3 id="visibility-history-title">Run history</h3>
      {!data.runs.length?<p>No runs recorded.</p>:<div className="visibility-history" role="region" aria-labelledby="visibility-history-title" tabIndex={0}><table><thead><tr><th>Started (UTC)</th><th>Question version</th><th>Result</th><th>Held</th></tr></thead><tbody>{data.runs.map(run=><tr key={run.run_id}><td>{new Date(run.started_at).toISOString().replace("T"," ").slice(0,19)}</td><td>{run.question_set_id?<details><summary>Version {run.question_set_id.slice(0,8)}</summary><span>{run.question_set_id}</span></details>:"Unavailable"}</td><td>{(run.reason ? readableCode(run.reason) : null)??(run.observed_changes.length?"observed change":"re-observation")}<ul>{run.calls.map(call=><li key={call.operation_id}>{call.provider}: {(call.reason ? readableCode(call.reason) : null)??readableCode(call.status)}</li>)}</ul></td><td>{money(run.calls.reduce((sum,call)=>sum+call.reserved_micros,0))}</td></tr>)}</tbody></table></div>}
    </>}
  </section>;
}
