"""Deterministic onboarding analysis over pinned evidence, with no I/O or authority."""

import hashlib
import re
from collections import Counter
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from signal_core.decision_contracts import canonical_json
from signal_core.internal_linking import RECIPE as INTERNAL_LINK_RECIPE
from signal_core.internal_linking import link_opportunities
from signal_core.keyword_topics import build_topics
from signal_core.observed_learning import decay, effectiveness, priority_factor
from signal_core.technical_seo_recipes import RECIPE_FINDING_KEYS

VERSION = "seo-baseline-strategy-v1"
SOURCES = ("crawl", "technical", "gsc", "bing", "ai_visibility", "business_brain", "content")


def number(value, evidence_ids):
    refs = sorted(set(evidence_ids))
    if not refs:
        raise ValueError("A measurement requires evidence.")
    return {"value": value, "evidence_ids": refs}


def _performance(source, generations):
    result = []
    for generation in generations:
        ref = [generation["id"]]
        dims = generation.get("dimensions", ["date"])
        rows = []
        for row in generation["rows"]:
            labels = dict(
                zip(
                    dims,
                    row.get(
                        "keys",
                        [row["page_url"], row["date"]] if "page_url" in row else [row.get("date")],
                    ),
                    strict=True,
                )
            )
            if source == "bing":
                match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", labels.get("date", ""))
                if match:
                    labels["date"] = (
                        datetime.fromtimestamp(int(match[1]) / 1000, UTC).date().isoformat()
                    )
            rows.append(
                {
                    "labels": labels,
                    "metrics": {
                        key: number(row[key], ref)
                        for key in (
                            "clicks",
                            "impressions",
                            "ctr",
                            "position",
                            "avg_click_position",
                            "avg_impression_position",
                        )
                        if key in row
                    },
                }
            )
        result.append(
            {
                "source": source,
                "evidence_id": generation["id"],
                "window": generation.get("window")
                or (
                    {
                        "start": min(r["labels"]["date"] for r in rows),
                        "end": max(r["labels"]["date"] for r in rows),
                    }
                    if rows and dims == ["date"]
                    else None
                ),
                "coverage": generation["coverage"],
                "dimensions": dims,
                "totals": {
                    key: number(sum(row[key] for row in generation["rows"]), ref)
                    for key in ("clicks", "impressions")
                },
                "rows": rows,
                "total_scope": "returned cohort only; not complete site totals",
            }
        )
    return result


def build_snapshot(packet):
    """Only typed observations drive candidates. Source prose cannot select an action."""
    unavailable = []
    evidence = {}

    def register(kind, record):
        evidence[record["id"]] = {"kind": kind, "record": record}

    for key in SOURCES:
        source = packet[key]
        if source["reason"]:
            unavailable.append({"source": key, "reason": source["reason"]})
        for record in source["records"]:
            register(key, record)
    for record in packet.get("dataforseo", {}).get("records", []):
        register("dataforseo_" + record["result"]["kind"], record)
    for record in packet.get("bing_pages", {}).get("records", []):
        register("bing_page_performance", record)
    for record in packet.get("topic_ideas", {}).get("records", []):
        register("topic_model_ideas", record)
    paid_source = packet.get("dataforseo", {})
    if paid_source.get("reason") or not paid_source.get("records"):
        unavailable.append(
            {
                "source": "dataforseo",
                "reason": paid_source.get("reason")
                or "No DataForSEO research recorded; execution or budget may be unavailable.",
            }
        )
    unavailable.extend(
        [
            {
                "source": "bing_page_query_metrics",
                "reason": packet.get("bing_pages", {}).get("reason")
                or "Bing top-page rows are incomplete and as reported; "
                "date granularity is unknown. "
                "Both named positions remain separate; query metrics unavailable.",
            },
            {
                "source": "model_reordering",
                "reason": "Unconfigured; deterministic-only labelled fallback.",
            },
        ]
    )
    crawl = next(iter(packet["crawl"]["records"]), None)
    report = next(iter(packet["technical"]["records"]), None)
    facts = packet["business_brain"]["records"]
    pages = packet["content"]["records"]
    for record in packet["writer_inventory"]:
        register("content_writer", record)
    learning = packet.get("learning", {})
    for record in learning.get("measurements", []):
        register("change_measurement", record)
    for record in learning.get("generations", []):
        register("gsc_page_history", record)
    effective = effectiveness(learning.get("measurements", []))
    declining = decay(
        learning.get("generations", []), learning.get("changes", []), learning.get("as_of")
    )
    headline = {}
    if crawl:
        for key in ("discovered_count", "terminal_count"):
            headline[key] = number(crawl[key], [crawl["id"]])
        headline["fetched_count"] = number(crawl["state_counts"].get("fetched", 0), [crawl["id"]])
    findings = report["findings"] if report else []
    if report:
        counts = Counter(item["severity"] for item in findings)
        for severity in ("critical", "high", "medium", "low", "info"):
            headline[f"findings_{severity}"] = number(counts[severity], [report["id"]])
    if facts:
        headline["approved_facts"] = number(len(facts), [fact["id"] for fact in facts])
    if packet["writer_inventory"]:
        headline["content_briefs"] = number(
            len(packet["writer_inventory"]), [r["id"] for r in packet["writer_inventory"]]
        )
    performance = _performance("gsc", packet["gsc"]["records"]) + _performance(
        "bing", packet["bing"]["records"] + packet.get("bing_pages", {}).get("records", [])
    )
    observations = packet["ai_visibility"]["records"]
    complete = [item for item in observations if item["status"] == "complete"]
    if complete:
        headline["ai_observations"] = number(len(complete), [o["id"] for o in complete])
        headline["ai_cited_observations"] = number(
            sum(bool(o["cited_pages"]) for o in complete), [o["id"] for o in complete]
        )
    page_rows = []
    for page in packet.get("page_inventory", pages):
        register("crawl_page_or_settlement", page)
        page_rows.append(
            {
                "url": page["url"],
                "title": page["title"],
                "evidence_id": page["id"],
                "findings": [f for f in findings if f["resource_locator"] == page["url"]],
                "metrics": [
                    {"source": cohort["source"], "window": cohort["window"], "row": row}
                    for cohort in performance
                    for row in cohort["rows"]
                    if row["labels"].get("page") == page["url"]
                ],
            }
        )
    candidates = []

    def candidate(
        kind, key, title, refs, impact, confidence, effort, inputs, target, action, reason
    ):
        work_type = (
            "metadata_pr"
            if kind == "technical"
            else ("content_refresh" if target else "new_article")
            if kind in {"content", "ai_visibility", "decay"}
            else None
        )
        observed = priority_factor(effective, work_type, inputs.get("recipe_key"))
        inputs = {**inputs, "observed_effectiveness": observed}
        refs = [*refs, *(ref for group in observed["groups"] for ref in group["evidence_ids"])]
        score = round(impact * confidence * (1 / effort) * observed["factor"], 6)
        candidates.append(
            {
                "id": str(uuid5(NAMESPACE_URL, f"{VERSION}:{kind}:{key}")),
                "kind": kind,
                "title": title,
                "evidence_ids": sorted(set(refs)),
                "priority": {
                    "score": score,
                    "impact_proxy": impact,
                    "confidence": confidence,
                    "effort_days": effort,
                    "effort_factor": round(1 / effort, 6),
                    "inputs": inputs,
                    "formula": "impact_proxy x confidence x (1 / effort_days) "
                    "x observed_effectiveness",
                },
                "target": target,
                "action": action,
                "unavailable_reason": reason,
                "autonomy": "Owner required; planning is never authorization.",
                "measurement": "Reobserve the motivating cohort after work; "
                "no ranking guarantee or causal claim.",
            }
        )

    for finding in findings:
        recipe = next(
            (key for key, keys in RECIPE_FINDING_KEYS.items() if finding["key"] in keys), None
        )
        matching = next(
            (item for item in packet["inbox"] if item["finding_id"] == finding["id"]), None
        )
        release = next((r for r in packet["recipes"] if r["recipe_key"] == recipe), None)
        refs = [report["id"], finding["source_id"]]
        if release:
            refs.append(release["id"])
            register("recipe_release", release)
        # Findings reference settlement/page evidence already bound by the immutable report.
        evidence.setdefault(finding["source_id"], {"kind": "finding_source", "record": finding})
        impact = {"critical": 10, "high": 8, "medium": 5, "low": 2, "info": 1}.get(
            finding["severity"], 1
        )
        action = {"kind": "inbox", "revision_id": matching["id"]} if matching else None
        candidate(
            "technical",
            finding["id"],
            finding["title"],
            refs,
            impact,
            1,
            1 if recipe else 3,
            {
                "severity": finding["severity"],
                "finding_key": finding["key"],
                "recipe_key": recipe,
                "recipe_release_id": release["id"] if release else None,
                "release_autonomy_eligible": bool(release and release["autonomy_eligible"]),
                "eligibility_scope": "Release only; current grant, exact candidate, "
                "caps and Jev must still pass 0104.",
            },
            finding["resource_locator"],
            action,
            None
            if action
            else "No matching sealed Inbox revision; candidate preparation unavailable."
            if recipe
            else "No supported 0081 recipe; owner diagnosis required.",
        )

    links = packet.get("internal_links")
    if links:
        evidence.setdefault(links["manifest_id"], {"kind": "crawl_graph", "record": links})
        for page in links["pages"]:
            register("crawl_page", page)
        for opportunity in link_opportunities(links["pages"], links["site_origin"]):
            matching = next(
                (
                    c
                    for c in links["candidates"]
                    if c["source_id"] == opportunity["source_id"]
                    and c["target_id"] == opportunity["target_id"]
                ),
                None,
            )
            action = {"kind": "inbox", "revision_id": matching["id"]} if matching else None
            candidate(
                "internal_link",
                links["manifest_id"] + canonical_json(opportunity).decode(),
                "Add contextual link to " + opportunity["target_url"],
                [links["manifest_id"], opportunity["source_id"], opportunity["target_id"]],
                3 if opportunity["incoming_count"] == 0 else 2,
                0.75,
                1,
                {
                    **opportunity,
                    "recipe_key": INTERNAL_LINK_RECIPE,
                    "coverage": links["coverage"],
                    "release_autonomy_eligible": False,
                    "basis": "Observed referring pages only; not a complete-site orphan claim.",
                },
                opportunity["source_url"],
                action,
                None
                if action
                else "Exact static paragraph candidate and owner Inbox review required.",
            )

    safe_facts = [
        f
        for f in facts
        if f["category"] in {"product", "audience", "positioning", "proof_point"}
        and not f["sensitive"]
    ]

    def brief_action(query, page=None):
        source = (
            next((p for p in pages if p["url"] == page), None) if page else next(iter(pages), None)
        )
        if not source or not safe_facts or not query.strip() or len(query) > 200:
            return None
        return {
            "kind": "brief",
            "payload": {
                "topic": query,
                "query": query,
                "intent": "informational",
                "kind": "content_refresh" if page else "new_article",
                "source_ids": [source["id"]],
                "fact_ids": [f["id"] for f in safe_facts[:20]],
                "internal_links": [source["url"]],
            },
        }

    for page in declining["pages"]:
        if not page["declining"]:
            continue
        chosen = (
            page["year_over_year"] if page["basis"] == "year_over_year" else page["prior_28_days"]
        )
        source = next((p for p in pages if p["url"] == page["url"]), None)
        topic = "Refresh: " + ((source["title"] or page["url"]) if source else page["url"])
        candidate(
            "decay",
            page["url"] + chosen["recent"]["window"]["start"],
            topic,
            page["evidence_ids"],
            min(chosen["metrics"]["impressions"]["baseline"], 10000),
            0.5,
            3,
            {
                "basis": page["basis"],
                "seasonality_possible": page["seasonality_possible"],
                "metrics": chosen["metrics"],
                "work_type": "content_refresh",
                "technical_finding_ids": [
                    f["id"] for f in findings if f["resource_locator"] == page["url"]
                ],
                "alternatives": "Seasonality, festivals, external edits and search updates "
                "remain possible; diagnose before refreshing.",
            },
            page["url"],
            brief_action(topic, page["url"]),
            None
            if brief_action(topic, page["url"])
            else "Current approved facts and crawled source required for a refresh brief proposal.",
        )

    topics = build_topics(packet)
    for cluster in topics["clusters"]:
        research = [
            {
                "provider": "DataForSEO",
                "label": "As reported by DataForSEO, " + r["recorded_at"][:10],
                "recorded_at": r["recorded_at"],
                "evidence_id": r["id"],
                "result": r["result"],
            }
            for r in packet.get("dataforseo", {}).get("records", [])
            if r["result"]["subject"] == cluster["title"]
        ]
        reported = [
            r["result"]["search_volume"]
            for r in research
            if r["result"]["kind"] == "volume" and r["result"]["search_volume"] is not None
        ]
        demand_factor = 1 + min(max(reported, default=0), 10000) / 10000
        if cluster["gaps"]:
            impressions = cluster["metrics"]["impressions"]
            position = cluster["metrics"]["average_position"]
            target = next(iter(cluster["targeting_pages"]), None)
            action = brief_action(cluster["title"], target)
            candidate(
                "content",
                cluster["id"],
                "Review topic: " + cluster["title"],
                cluster["evidence_ids"]
                + [p["id"] for p in pages]
                + [r["evidence_id"] for r in research],
                min(impressions, 10000) * demand_factor,
                0.5,
                3,
                {
                    "impressions": impressions,
                    "position": position,
                    "cluster_id": cluster["id"],
                    "provider_research": research,
                    "reported_demand_factor": demand_factor,
                    "research_basis": "Provider-reported demand is a bounded priority heuristic, "
                    "not a ranking prediction.",
                    "gaps": cluster["gaps"],
                    "high_impressions_minimum": topics["policy"]["high_impressions_minimum"],
                    "weak_position_minimum": 8,
                    "confidence_basis": "Incomplete returned cohort; diagnosis before drafting.",
                    "alternatives": "Query intent, SERP features, seasonality "
                    "and tracking changes remain unassessed.",
                },
                target,
                action,
                None
                if action
                else "Current approved facts and crawled source required for a brief proposal.",
            )
            cluster["strategy_item_id"] = candidates[-1]["id"]
        for idea in cluster["ideas"]:
            idea_research = [
                r
                for r in packet.get("dataforseo", {}).get("records", [])
                if r["result"]["subject"] == idea["query"]
            ]
            candidate(
                "content",
                cluster["id"] + ":idea:" + idea["query"],
                "Explore idea: " + idea["query"],
                cluster["evidence_ids"] + idea["evidence_ids"] + [r["id"] for r in idea_research],
                max(
                    1,
                    min(
                        max(
                            (v["value"] for v in idea["volumes"] if v["value"] is not None),
                            default=0,
                        ),
                        10000,
                    ),
                ),
                0.25,
                3,
                {
                    "cluster_id": cluster["id"],
                    "label": idea["label"],
                    "basis": "Model hypothesis, not observed demand; owner diagnosis required.",
                    "volumes": idea["volumes"],
                    "provider_research": [
                        {
                            "provider": "DataForSEO",
                            "label": "As reported by DataForSEO, " + r["recorded_at"][:10],
                            "evidence_id": r["id"],
                            "result": r["result"],
                        }
                        for r in idea_research
                    ],
                },
                None,
                brief_action(idea["query"]),
                None
                if brief_action(idea["query"])
                else "Current approved facts and crawled source required for a brief proposal.",
            )
            idea["strategy_item_id"] = candidates[-1]["id"]
    covered = {c["title"] for c in topics["clusters"]} | {
        i["query"] for c in topics["clusters"] for i in c["ideas"]
    }
    provider_topics = {}
    for record in sorted(
        packet.get("dataforseo", {}).get("records", []),
        key=lambda r: (r["recorded_at"], r["id"]),
        reverse=True,
    ):
        result = record["result"]
        if result["kind"] == "volume" and result["subject"] not in covered:
            provider_topics.setdefault(result["subject"], record)
    for subject, record in provider_topics.items():
        action = brief_action(subject)
        candidate(
            "content",
            "provider:" + subject,
            "Explore topic: " + subject,
            [record["id"]],
            min(record["result"]["search_volume"] or 1, 10000),
            0.25,
            3,
            {
                "provider_research": [
                    {
                        "provider": "DataForSEO",
                        "evidence_id": record["id"],
                        "label": "As reported by DataForSEO, " + record["recorded_at"][:10],
                        "result": record["result"],
                    }
                ],
                "basis": "Provider-reported demand, not site impressions or a ranking prediction.",
            },
            None,
            action,
            None
            if action
            else "Current approved facts and crawled source required for a brief proposal.",
        )
    if facts:
        for category in ("audience", "positioning", "proof_point"):
            if not any(f["category"] == category for f in facts):
                candidate(
                    "business_gap",
                    category,
                    f"Ask owner for approved {category.replace('_', ' ')} facts",
                    [f["id"] for f in facts],
                    2,
                    0.5,
                    1,
                    {
                        "missing_category": category,
                        "basis": "Category absent in pinned approved-fact cohort; "
                        "not an inferred business claim.",
                    },
                    None,
                    None,
                    "Owner facts required; unsupported brief generation unavailable.",
                )
    for observation in complete:
        if observation["cited_pages"]:
            continue
        action = brief_action(observation["question"])
        candidate(
            "ai_visibility",
            observation["id"],
            "Review uncited question: " + observation["question"],
            [observation["id"], observation["provider_evidence_id"]],
            3,
            0.5,
            3,
            {
                "provider": observation["provider"],
                "model": observation["model"],
                "observed_at": observation["observed_at"],
                "basis": "Official API observation, not consumer-app visibility; "
                "no citation causality assumed.",
            },
            None,
            action,
            None
            if action
            else "Current approved facts and crawled source required for a brief proposal.",
        )
        evidence[observation["provider_evidence_id"]] = {
            "kind": "assistant_response",
            "record": observation,
        }
    candidates.sort(key=lambda item: (-item["priority"]["score"], item["id"]))
    planned_days = 0
    for item in candidates:
        planned_days += item["priority"]["effort_days"]
        item["phase"] = (
            "days_1_30"
            if planned_days <= 30
            else "days_31_60"
            if planned_days <= 60
            else "days_61_90"
            if planned_days <= 90
            else "backlog"
        )
    return {
        "version": VERSION,
        "headline": headline,
        "unavailable": unavailable,
        "sources": packet,
        "evidence": evidence,
        "pages": page_rows,
        "performance": performance,
        "learning": {
            "version": "site-observed-learning-v1",
            "effectiveness": effective,
            "decay": declining,
        },
        "topics": topics,
        "strategy": {
            "horizon_days": 90,
            "items": candidates,
            "decision": {
                "provider": "deterministic_fallback",
                "fallback": True,
                "reason": "MODEL_REORDERING_UNCONFIGURED",
                "version": VERSION,
            },
        },
    }


def snapshot_digest(snapshot):
    return hashlib.sha256(canonical_json(snapshot)).digest()
