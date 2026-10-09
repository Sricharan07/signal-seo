# ADR-0165: Provider Data in Strategy

Status: Accepted for slice 0155 local implementation. Live qualification and
deployment NOT_EXECUTED; no production authority.

## Context

0076 could store DataForSEO credentials/caps without executing research. 0071 and
0129 could bind/import Bing only through internal ports. Strategy and weekly
imports omitted useful existing data. This is security-critical under Revision 4.0
section 19: OAuth, OpenBao, paid requests and egress are involved.

## Decision

Owner refresh and the existing weekly `strategy_rebuild` invoke the same optional
research service. Select at most five exact observed topics/current keyword ideas,
falling back to successful crawl titles. Use the existing volume/SERP adapters with
explicit location/language. Optionally fetch at most two returned competitor domain
backlink summaries. Text selects subjects, never authority, endpoints or costs.

Reserve the documented estimate against both the owner's monthly DataForSEO cap
and the current human standing research volume/spend caps before secrets or I/O.
A denial atomically rolls back both reservations. Round micro-USD up to standing
cents; retain the estimate and add reported overruns without automatic refunds.
Recheck authority, credential, month, intent/body and caps at dispatch. Owner
research requires fresh MFA and verified site; weekly research requires the admitted
strategy handle, grant/release, generation, pause/revocation and existing epochs.

Credential/month/query-derived intents cache complete observations and hold unknown
outcomes without redispatch. A different intent or rotated credential cannot bypass
an unresolved identical query, including across month changes. No automatic hold
reconciliation is added. Only pre-dispatch admission deferral can repeat the exact
intent, for at most five seconds. Credentials remain in existing OpenBao namespaces.
Results are immutable, scoped, untrusted data, never instructions or predictions.

Reuse shared pinned egress, encrypted robots evidence, global origin buckets and
unknown outcomes. Existing profiles are unchanged. Add only the exact uncredentialed
`GET https://api.dataforseo.com/robots.txt` profile; other paths, queries, hosts,
credentials and methods are denied. New connector SQL entries admit only the three
existing paid endpoints and existing Bing token/read endpoints. No SERP scraping.

Expose Bing through current-owner, five-minute MFA freshness, verified-site API
projections and browser mutation proofs. The BFF checks exact read-only OAuth URL,
one attempt cookie, one state/code and fixed callback. Confirmation remains an
explicit human action for the exact verified site. Revocation restricts the binding
before OpenBao destruction. More data uses the shared status pill/Technical details.

Weekly Bing imports request site and page performance independently. The page
recorder retains 0129's validation and egress provenance plus existing workload
stage/tenant/resource guards. Strategy pins the page generation separately. 0144
already measures independent `bing_page` windows; this supplies the missing weekly
input. Never sum GSC/Bing cohorts, invent missing page-days, reinterpret unknown
granularity or equate Bing's two named positions with GSC position.

## Consequences

Reported volume is a bounded priority heuristic, not a ranking prediction. SERP
competitors/optional backlinks carry as-reported DataForSEO date and provenance.
Unconfigured, capped, unresolved and unavailable states use plain words. Cached
evidence remains useful when research is capped. Optional unconfigured composition
stays visibly unavailable. Acceptance, publishing, external-write, merge and
deployment authority are unchanged. No dependency or frozen revision is changed.

0098 follows 0097 (merge train 5; written as 0093 after 0092). See [0155 implementation](../implementation/0155-data-providers.md)
and [qualification evidence](../evidence/0155-data-providers.json).
