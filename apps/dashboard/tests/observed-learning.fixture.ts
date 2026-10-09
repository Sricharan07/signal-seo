import type { ObservedLearning } from "../lib/seo-strategy-api";
import { ID, projection } from "./seo-strategy.fixture";

const window = (start: string, end: string, clicks: number) => ({
  state: "as_reported", reason: "Returned page-days; completeness not guaranteed.",
  window: { start, end }, evidence_ids: [ID], coverage: { complete: false, missing_data: "unknown_not_zero" },
  daily: Array.from({length: 28}, () => ({clicks, impressions: 200})),
});
const comparison = {
  state: "as_reported", baseline: window("2026-08-06","2026-09-02",30), recent: window("2026-09-03","2026-09-30",10),
  metrics: {
    clicks: {baseline:840,recent:280,relative_decline:.666667,noise:33.466401,minimum_volume:100,declining:true},
    impressions: {baseline:5600,recent:5600,relative_decline:0,noise:105.830052,minimum_volume:1000,declining:false},
  },
};
export const learningFixture: ObservedLearning = {
  version: "site-observed-learning-v1",
  effectiveness: {state:"partial", label:"Observed on this site; not evidence of causation.",reason:"Provider-reported cohorts; completeness not guaranteed.",excluded_measurements:0,groups:[{
    work_type:"metadata_pr",recipe_key:"technical_description",horizon:28,sample_size:5,effective_sample_size:5,shrinkage:.5,factor:1.1,evidence_ids:[ID],
    metrics:{clicks:{sample_size:5,effective_sample_size:5,mean_delta:100,shrunk_delta:50},impressions:{sample_size:5,effective_sample_size:5,mean_delta:1000,shrunk_delta:500},ctr:{sample_size:5,effective_sample_size:5,mean_delta:.01,shrunk_delta:.005},position:{sample_size:5,effective_sample_size:5,mean_delta:-2,shrunk_delta:-1}},
  }]},
  decay: {state:"partial",label:"Observed on this site; not evidence of causation.",reason:"Returned cohorts only; absent pages and dates remain unknown.",excluded_pages:["https://example.invalid/recent-change"],pages:[{
    url:"https://example.invalid/calendar/festival-dates-and-observances",state:"as_reported",basis:"prior_28_days",seasonality_possible:true,year_over_year_reason:"13 months of usable page history unavailable or partial.",declining:true,evidence_ids:[ID],prior_28_days:comparison,year_over_year:null,
  }]},
};
export const learningProjection = structuredClone(projection);
learningProjection.snapshot!.payload.learning = learningFixture;
