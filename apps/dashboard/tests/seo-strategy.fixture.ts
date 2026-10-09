import type { SeoProjection } from "../lib/seo-strategy-api";

export const SITE = "11111111-1111-4111-8111-111111111111";
export const ID = "22222222-2222-4222-8222-222222222222";
export const ITEM = "33333333-3333-4333-8333-333333333333";
export const projection: SeoProjection = {
  schema_version:1, decisions:[], snapshot:{id:ID,version:1,created_at:"2026-10-02T00:00:00Z",sha256:"a".repeat(64),payload:{
    version:"seo-baseline-strategy-v1",headline:{fetched_count:{value:1,evidence_ids:[ID]}},
    unavailable:[{source:"dataforseo",reason:"DataForSEO is not configured."},{source:"bing",reason:"Bing unbound."},{source:"ai_visibility",reason:"No AI-visibility observations."}],
    sources:{crawl:{reason:null,records:[{id:ID,coverage:"complete"}]}},evidence:{[ID]:{kind:"crawl",record:{id:ID,coverage:"complete"}}},
    pages:[{url:"https://example.invalid/",title:"Synthetic observed page",evidence_id:ID,findings:[],metrics:[]}],
    performance:[{source:"gsc",evidence_id:ID,dimensions:["date"],window:{start:"2026-09-01",end:"2026-09-02"},coverage:{complete:false,missing_data:"unknown_not_zero"},total_scope:"returned cohort only; not complete site totals",totals:{clicks:{value:2,evidence_ids:[ID]},impressions:{value:100,evidence_ids:[ID]}},rows:[{labels:{date:"2026-09-01"},metrics:{clicks:{value:2,evidence_ids:[ID]},impressions:{value:100,evidence_ids:[ID]}}}]}],
    strategy:{horizon_days:90,decision:{provider:"deterministic_fallback",fallback:true,reason:"MODEL_REORDERING_UNCONFIGURED",version:"seo-baseline-strategy-v1"},items:[{
      id:ITEM,kind:"content",title:"Review query: startup planning",evidence_ids:[ID],target:null,
      priority:{score:16.666667,impact_proxy:100,confidence:.5,effort_days:3,effort_factor:.333333,inputs:{impressions:100,position:11,ctr:.01,confidence_basis:"Incomplete returned cohort; diagnose before drafting."},formula:"impact_proxy x confidence x (1 / effort_days)"},
      action:{kind:"brief",payload:{}},unavailable_reason:null,autonomy:"Owner required; planning is never authorization.",measurement:"Reobserve the motivating cohort; no ranking guarantee.",phase:"days_1_30",
    }]},
  }},
};
