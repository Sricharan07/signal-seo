"""Provider research is a bounded heuristic; search cohorts stay separate."""

from signal_core.keyword_topics import build_topics
from signal_core.seo_strategy import build_snapshot

from tests.connectors.test_seo_strategy_domain import evidence_id, packet


def research(kind="volume", volume=1000):
    return {
        "id": evidence_id(30),
        "recorded_at": "2026-10-04T00:00:00Z",
        "query": {"subject": "useful query", "location_code": 2840, "language_code": "en"},
        "result": {
            "kind": kind,
            "subject": "useful query",
            "search_volume": volume,
            "location_code": 2840,
            "language_code": "en",
        },
    }


def test_reported_volume_ranks_topic_with_provenance_not_a_prediction():
    data = packet()
    before = build_snapshot(data)
    data["dataforseo"] = {"reason": None, "records": [research()]}
    after = build_snapshot(data)
    candidate = next(i for i in after["strategy"]["items"] if i["kind"] == "content")
    original = next(i for i in before["strategy"]["items"] if i["kind"] == "content")
    assert candidate["priority"]["score"] > original["priority"]["score"]
    assert evidence_id(30) in candidate["evidence_ids"]
    assert "not a ranking prediction" in candidate["priority"]["inputs"]["research_basis"]
    assert after["evidence"][evidence_id(30)]["kind"] == "dataforseo_volume"
    assert "dataforseo" not in {x["source"] for x in after["unavailable"]}
    data["dataforseo"]["records"][0]["result"]["search_volume"] = 10**9
    bounded = next(i for i in build_snapshot(data)["strategy"]["items"] if i["kind"] == "content")
    assert bounded["priority"]["inputs"]["reported_demand_factor"] == 2


def test_serp_and_backlinks_are_evidence_not_volume_or_query_cohorts():
    data = packet()
    data["dataforseo"] = {
        "reason": None,
        "records": [research("serp"), {**research("backlinks"), "id": evidence_id(31)}],
    }
    topics = build_topics(data)
    assert topics["clusters"] and all(not c["ideas"] for c in topics["clusters"])
    result = build_snapshot(data)
    assert result["evidence"][evidence_id(30)]["kind"] == "dataforseo_serp"
    assert result["evidence"][evidence_id(31)]["kind"] == "dataforseo_backlinks"
    assert len(result["performance"]) == 2


def test_bing_pages_keep_named_positions_unknown_granularity_and_per_source_totals():
    data = packet()
    data["bing_pages"] = {
        "reason": None,
        "records": [
            {
                "id": evidence_id(32),
                "dimensions": ["page", "date"],
                "coverage": {"complete": False, "date_granularity": "unknown"},
                "rows": [
                    {
                        "page_url": "https://example.invalid/",
                        "date": "/Date(1788220800000-0700)/",
                        "clicks": 8,
                        "impressions": 120,
                        "avg_click_position": 2.5,
                        "avg_impression_position": 9,
                    }
                ],
            }
        ],
    }
    result = build_snapshot(data)
    assert [(c["source"], c["totals"]["clicks"]["value"]) for c in result["performance"]] == [
        ("gsc", 2),
        ("bing", 4),
        ("bing", 8),
    ]
    cohort = result["performance"][2]
    assert cohort["rows"][0]["labels"]["date"] == "2026-09-01"
    assert cohort["rows"][0]["metrics"]["avg_click_position"]["value"] == 2.5
    assert "position" not in cohort["rows"][0]["metrics"]
    assert {m["source"] for m in result["pages"][0]["metrics"]} == {"gsc", "bing"}
    assert cohort["coverage"]["complete"] is False


def test_provider_unavailable_cap_and_unknown_copy_is_kept_plain():
    for reason in [
        "DataForSEO is not configured.",
        "The monthly DataForSEO cap is reached.",
        "DataForSEO has an unresolved call; spend is held.",
    ]:
        data = packet(False)
        data["dataforseo"] = {"reason": reason, "records": []}
        result = build_snapshot(data)
        assert (
            next(x["reason"] for x in result["unavailable"] if x["source"] == "dataforseo")
            == reason
        )
        assert "Not implemented" not in str(result)
