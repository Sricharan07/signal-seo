import assert from "node:assert/strict";
import { test, afterEach } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { VisibilitySchedule } from "../components/visibility-schedule";
import { GET, POST } from "../app/actions/visibility-schedule/route";
import { validVisibilitySchedule, visibilitySettings, type VisibilityScheduleData } from "../lib/visibility-schedule-api";

const SITE="11111111-1111-4111-8111-111111111111";
const ID="22222222-2222-4222-8222-222222222222";
const DATA:VisibilityScheduleData={schema_version:1,site_id:SITE,settings_id:null,cadence_days:7,monthly_cap_micros:1_000_000,enabled:false,held_micros:0,spent_micros:null,currency:"USD",month_start:"2026-10-01",cap_reached:false,authority_current:false,runtime_state:"unavailable",runs:[]};
const originalFetch=globalThis.fetch;const origin=process.env.SIGNAL_DASHBOARD_ORIGIN;
afterEach(()=>{globalThis.fetch=originalFetch;if(origin===undefined)delete process.env.SIGNAL_DASHBOARD_ORIGIN;else process.env.SIGNAL_DASHBOARD_ORIGIN=origin;});

test("schedule exposes cadence cap unpriced holds and unavailable worker without fabricated history",()=>{
  assert.ok(validVisibilitySchedule(DATA,SITE));
  assert.equal(validVisibilitySchedule({...DATA,site_id:ID},SITE),false);
  for(const bad of [{cadence_days:31},{held_micros:-1},{spent_micros:0},{monthly_cap_micros:1.5},{enabled:"true"},{extra:true}])assert.equal(validVisibilitySchedule({...DATA,...bad},SITE),false);
  const html=renderToStaticMarkup(<VisibilitySchedule siteId={SITE} initialData={DATA}/>);
  assert.match(html,/Cadence \(days\)/);assert.match(html,/Monthly cap \(USD\)/);assert.match(html,/Worker unavailable/);assert.match(html,/Unpriced/);assert.match(html,/No runs recorded/);
  const reached=renderToStaticMarkup(<VisibilitySchedule siteId={SITE} initialData={{...DATA,enabled:true,authority_current:true,runtime_state:"available",held_micros:1_000_000,cap_reached:true}}/>);
  assert.match(reached,/Spend cap reached/);
});

test("strict command keeps integer currency caps and owner request identity",()=>{
 const command={schema_version:1,site_id:SITE,request_id:ID,cadence_days:7,monthly_cap_micros:1_000_000,enabled:true};
 assert.ok(visibilitySettings(command));
 for(const bad of [{cadence_days:true},{monthly_cap_micros:1.1},{enabled:"true"},{authority:"owner"}])assert.equal(visibilitySettings({...command,...bad}),null);
});

test("same-origin BFF binds current CSRF and rejects scope or oversized body before forwarding",async()=>{
 process.env.SIGNAL_DASHBOARD_ORIGIN="http://localhost:3000";const calls:Request[]=[];
 globalThis.fetch=async(input,init)=>{const r=new Request(input,init);calls.push(r);return Response.json(r.url.endsWith("tenant-csrf")?{schema_version:1,csrf_token:"c".repeat(43)}:DATA);};
 const request=(body:unknown,selectedOrigin="http://localhost:3000")=>new Request("http://localhost:3000/actions/visibility-schedule",{method:"POST",headers:{Cookie:`__Host-signal_session=${"synthetic-"+"t".repeat(33)}`,Origin:selectedOrigin,"Sec-Fetch-Site":"same-origin","Content-Type":"application/json"},body:JSON.stringify(body)});
 const command={schema_version:1,site_id:SITE,request_id:ID,cadence_days:7,monthly_cap_micros:1_000_000,enabled:true};
 assert.equal((await POST(request(command))).status,200);assert.equal(calls.length,2);assert.equal(calls[1]?.headers.get("x-csrf-token"),"c".repeat(43));assert.ok(calls[1]?.url.endsWith("ai-visibility/schedule"));
 calls.length=0;assert.equal((await POST(request(command,"https://offsite.test"))).status,403);assert.equal((await POST(request({...command,extra:"x".repeat(3000)}))).status,403);assert.equal(calls.length,0);
 assert.equal((await GET(new Request("http://localhost:3000/actions/visibility-schedule?site_id=invalid"))).status,403);
});
