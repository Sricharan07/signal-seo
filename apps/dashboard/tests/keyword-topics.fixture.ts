import type { SeoProjection } from "../lib/seo-strategy-api";
import { ID, ITEM, projection } from "./seo-strategy.fixture";

export const topicsProjection: SeoProjection = structuredClone(projection);
topicsProjection.snapshot!.payload.unavailable = [
  {source:"dataforseo",reason:"Only recorded provider volumes are available; new volume lookup, ranking competitors and competitor backlinks unavailable here."},
  {source:"bing_page_query_metrics",reason:"No query-dimensional import; query metrics unavailable."},
];
topicsProjection.snapshot!.payload.topics = {
  version:"keyword-topics-v1", ideas_status:"available", ideas_reason:"Recorded model hypotheses.",
  volume_status:"available", scope:"One returned query cohort per source; not complete site totals. Page targeting is title overlap in the pinned crawl only.",
  unavailable:[{source:"bing",reason:"No query-dimensional import; query metrics unavailable."}],
  policy:{high_impressions_minimum:100,weak_position_minimum:8,clustering:"NFKC and casefold; multilingual terms and n-grams; deterministic complete-link clustering."},
  clusters:[{
    id:ITEM,title:"తెలుగు వార్తలు",source:"gsc",evidence_ids:[ID],
    window:{start:"2026-09-01",end:"2026-09-28"},coverage:{complete:false,missing_data:"unknown_not_zero"},dimensions:["query","page"],
    members:[{query:"తెలుగు వార్తలు",variants:["తెలుగు వార్తలు"],tokens:["తెలుగు","వార్తలు"],
      membership:{shared_terms:["తెలుగు","వార్తలు"],shared_bigrams:[["తెలుగు","వార్తలు"]],term_jaccard:1},
      volumes:[{value:300,provider:"dataforseo",label:"provider-reported search volume, not site impressions",location_code:2356,language_code:"te",recorded_at:"2026-10-04T00:00:00Z",evidence_ids:[ID]}]},
      {query:"తెలుగు వార్తలు నేడు",variants:["తెలుగు వార్తలు నేడు"],tokens:["తెలుగు","వార్తలు","నేడు"],membership:{shared_terms:["తెలుగు","వార్తలు"],shared_bigrams:[["తెలుగు","వార్తలు"]],term_jaccard:2/3},volumes:[]}],
    metrics:{clicks:4,impressions:240,average_position:11.5,evidence_ids:[ID]},
    ranking_pages:["https://example.invalid/"],targeting_pages:[],targeting_assessed:true,
    gaps:["high_impressions_weak_position","no_observed_page_targeting"],strategy_item_id:ITEM,
    ideas:[{cluster_id:ITEM,query:"తెలుగు స్థానిక వార్తలు",label:"idea, no volume data",volume:null,evidence_ids:[ID],volumes:[],strategy_item_id:ITEM}],
  }],
};
