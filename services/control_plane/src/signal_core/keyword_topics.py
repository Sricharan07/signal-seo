"""Evidence-only multilingual topic clustering. No I/O, estimated volumes or authority."""

import unicodedata
from collections import defaultdict
from fractions import Fraction
from uuid import NAMESPACE_URL, uuid5

from signal_core.decision_contracts import canonical_json

VERSION = "keyword-topics-v1"
IDEA_LABEL = "idea, no volume data"
HIGH_IMPRESSIONS = 100
WEAK_POSITION = 8


def tokens(text):
    """Keep Unicode marks attached to letters (including Telugu vowel signs/virama)."""
    result, word = [], []
    for char in unicodedata.normalize("NFKC", text).casefold():
        category = unicodedata.category(char)[0]
        if category in {"L", "N"} or category == "M" and word:
            word.append(char)
        elif char in {"\u200c", "\u200d"} and word:
            # Join controls are not word boundaries, but carry no search-term identity.
            continue
        elif word:
            result.append("".join(word))
            word = []
    if word:
        result.append("".join(word))
    return tuple(result)


def overlap(left, right):
    a, b = set(left), set(right)
    shared = sorted(a & b)
    grams = sorted(
        set(zip(left, left[1:], strict=False)) & set(zip(right, right[1:], strict=False))
    )
    score = Fraction(len(shared), len(a | b)) if a or b else Fraction(0)
    matches = left == right or len(shared) >= 2 and (score >= Fraction(1, 2) or bool(grams))
    return matches, {
        "shared_terms": shared,
        "shared_bigrams": [list(g) for g in grams],
        "term_jaccard": float(score),
    }


def labelled_ideas(output, cluster_ids):
    """A model can return text only; labels, evidence and metrics are never model fields."""
    if not isinstance(output, dict) or set(output) != {"ideas"}:
        raise ValueError("Invalid topic ideas.")
    ideas = output["ideas"]
    if not isinstance(ideas, list) or len(ideas) > 20:
        raise ValueError("Invalid topic ideas.")
    result = {}
    for idea in ideas:
        if not isinstance(idea, dict) or set(idea) != {"cluster_id", "query"}:
            raise ValueError("Invalid topic idea.")
        query = idea["query"]
        if (
            idea["cluster_id"] not in cluster_ids
            or not isinstance(query, str)
            or not 1 <= len(query) <= 200
            or not tokens(query)
            or any(unicodedata.category(c) == "Cc" for c in query)
        ):
            raise ValueError("Invalid topic idea.")
        key = (idea["cluster_id"], tokens(query))
        value = {
            "cluster_id": idea["cluster_id"],
            "query": query,
            "label": IDEA_LABEL,
            "volume": None,
        }
        if key not in result or query < result[key]["query"]:
            result[key] = value
    return sorted(result.values(), key=lambda i: (i["cluster_id"], i["query"]))


def _metrics(rows, evidence_id):
    impressions = sum(Fraction(str(r["impressions"])) for r in rows)
    known = [r for r in rows if r["impressions"] > 0]
    position = None
    if known and all(r.get("position") is not None for r in known):
        # Rational arithmetic makes row order irrelevant, including fractional impressions.
        weighted = sum(
            Fraction(str(r["position"])) * Fraction(str(r["impressions"])) for r in known
        )
        position = round(float(weighted / Fraction(str(impressions))), 6)
    return {
        "clicks": float(sum(Fraction(str(r["clicks"])) for r in rows)),
        "impressions": float(impressions),
        "average_position": position,
        "evidence_ids": [evidence_id],
    }


def _volume(query, records):
    latest = {}
    for record in sorted(records, key=lambda r: (r["recorded_at"], r["id"]), reverse=True):
        result = record["result"]
        if result["kind"] == "volume" and tokens(result["subject"]) == tokens(query):
            latest.setdefault((result["location_code"], result["language_code"]), record)
    matches = [latest[key] for key in sorted(latest)]
    return [
        {
            "value": r["result"]["search_volume"],
            "provider": "dataforseo",
            "label": "provider-reported search volume, not site impressions",
            "location_code": r["result"]["location_code"],
            "language_code": r["result"]["language_code"],
            "recorded_at": r["recorded_at"],
            "evidence_ids": [r["id"]],
        }
        for r in matches
    ]


def build_topics(packet):
    clusters, unavailable = [], []
    volumes = [
        r
        for r in packet.get("dataforseo", {}).get("records", [])
        if r["result"]["kind"] == "volume"
    ]
    pages = packet["content"]["records"]
    for source in ("gsc", "bing"):
        cohorts = [g for g in packet[source]["records"] if "query" in g.get("dimensions", [])]
        if not cohorts:
            unavailable.append(
                {
                    "source": source,
                    "reason": "No query-dimensional import; query metrics unavailable.",
                }
            )
            continue
        # Choose one returned window, never add overlapping dimension cohorts together.
        cohort = sorted(
            cohorts,
            key=lambda g: (
                g.get("imported_at", ""),
                (g.get("window") or {}).get("end", ""),
                (g.get("window") or {}).get("start", ""),
                "page" in g["dimensions"],
                -len(g["dimensions"]),
                g["id"],
            ),
            reverse=True,
        )[0]
        grouped = defaultdict(list)
        for row in cohort["rows"]:
            labels = dict(zip(cohort["dimensions"], row["keys"], strict=True))
            term = tokens(labels["query"])
            if term:
                grouped[term].append({**row, "labels": labels})
        groups, index = [], defaultdict(set)
        for term in sorted(grouped):
            # Complete-link membership prevents a bridge query joining unrelated topics.
            counts = defaultdict(int)
            for word in set(term):
                for gid in index[word]:
                    counts[gid] += 1
            candidates = sorted(gid for gid, count in counts.items() if count >= 2)
            gid = next(
                (gid for gid in candidates if all(overlap(term, t)[0] for t in groups[gid])), None
            )
            if gid is None:
                gid = len(groups)
                groups.append([term])
                for word in set(term):
                    index[word].add(gid)
            else:
                groups[gid].append(term)
        for group in groups:
            rows = [r for term in group for r in grouped[term]]
            identity = {"source": source, "dimensions": cohort["dimensions"], "members": group}
            cid = str(uuid5(NAMESPACE_URL, VERSION + canonical_json(identity).decode()))
            metrics = _metrics(rows, cohort["id"])
            targeting = sorted(
                p["url"]
                for p in pages
                if any(overlap(tokens(p["title"]), term)[0] for term in group)
            )
            gaps = []
            if (
                metrics["impressions"] >= HIGH_IMPRESSIONS
                and metrics["average_position"] is not None
                and metrics["average_position"] >= WEAK_POSITION
            ):
                gaps.append("high_impressions_weak_position")
            if pages and not targeting:
                gaps.append("no_observed_page_targeting")
            members = []
            for term in group:
                query = " ".join(term)
                members.append(
                    {
                        "query": query,
                        "variants": sorted({r["labels"]["query"] for r in grouped[term]}),
                        "tokens": list(term),
                        "membership": overlap(group[0], term)[1],
                        "volumes": _volume(query, volumes),
                    }
                )
            clusters.append(
                {
                    "id": cid,
                    "title": " ".join(group[0]),
                    "source": source,
                    "evidence_ids": [cohort["id"]],
                    "window": cohort.get("window"),
                    "coverage": cohort["coverage"],
                    "dimensions": cohort["dimensions"],
                    "members": members,
                    "metrics": metrics,
                    "ranking_pages": sorted({r["labels"]["page"] for r in rows})
                    if "page" in cohort["dimensions"]
                    else None,
                    "targeting_pages": targeting,
                    "targeting_assessed": bool(pages),
                    "gaps": gaps,
                    "ideas": [],
                    "strategy_item_id": None,
                }
            )
    clusters.sort(key=lambda c: (-c["metrics"]["impressions"], c["id"]))
    topics = {
        "version": VERSION,
        "clusters": clusters,
        "unavailable": unavailable,
        "ideas_status": "unavailable",
        "ideas_reason": "No current recorded model expansion.",
        "volume_status": "available" if volumes else "unavailable",
        "scope": "One returned query cohort per source; not complete site totals. "
        "Page targeting is lexical title overlap in the pinned crawl only; "
        "a gap is a diagnosis signal, not proof that the site lacks content.",
        "policy": {
            "high_impressions_minimum": HIGH_IMPRESSIONS,
            "weak_position_minimum": WEAK_POSITION,
            "clustering": "NFKC/casefold; Unicode letters, numbers and attached marks; "
            "complete-link, sorted queries; 2 shared terms and "
            "Jaccard >= 0.5 or shared bigram. No stemming/translation.",
        },
    }
    records = packet.get("topic_ideas", {}).get("records", [])
    if records:
        try:
            current_input = idea_packet(topics)
        except ValueError:
            topics["ideas_reason"] = "Current query packet exceeds the expansion bound."
            return topics
    for record in records:
        if record["input"] != current_input:
            continue
        ideas = labelled_ideas(record["output"], {c["id"] for c in clusters})
        for idea in ideas:
            cluster = next(c for c in clusters if c["id"] == idea["cluster_id"])
            cluster["ideas"].append(
                {
                    **idea,
                    "evidence_ids": [record["id"]],
                    "volumes": _volume(idea["query"], volumes),
                    "strategy_item_id": None,
                }
            )
        topics["ideas_status"], topics["ideas_reason"] = "available", "Recorded model hypotheses."
        break
    return topics


def idea_packet(topics):
    packet = {
        "version": VERSION,
        "clusters": [
            {
                "cluster_id": c["id"],
                "queries": [m["query"] for m in c["members"][:10]],
                "evidence_ids": c["evidence_ids"],
            }
            for c in topics["clusters"][:20]
        ],
    }
    if len(canonical_json(packet)) > 24000:
        raise ValueError("Topic expansion input too large.")
    return packet
