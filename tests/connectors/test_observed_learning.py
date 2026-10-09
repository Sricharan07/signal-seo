import copy
from datetime import date, timedelta
from uuid import UUID

import pytest
from signal_core.observed_learning import decay, effectiveness, priority_factor
from signal_core.seo_strategy import build_snapshot

from tests.connectors.test_seo_strategy_domain import packet


def measurement(n, *, horizon=28, delta=100, confounders=0):
    return {
        "id": str(UUID(int=n)),
        "operation_id": str(UUID(int=n + 100)),
        "horizon": horizon,
        "work_type": "metadata_pr",
        "recipe_key": "technical_canonical",
        "baseline": {"gsc_page": {"metrics": {"clicks": 100, "impressions": 1000}}},
        "document": {
            "state": "measured_as_reported",
            "confounders": ["other"] * confounders,
            "observed_change": {
                "gsc_page": {
                    "clicks": delta,
                    "impressions": delta * 10,
                    "ctr": 0.01,
                    "position": -2,
                }
            },
        },
    }


def generation(n, start, end, clicks=20, impressions=200):
    return {
        "id": str(UUID(int=n)),
        "dimensions": ["page", "date"],
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "coverage": {"complete": False, "missing_data": "unknown_not_zero"},
        "imported_at": f"2026-10-02T00:00:{n:02d}Z",
        "rows": [
            {
                "keys": ["https://example.invalid/", (start + timedelta(days=i)).isoformat()],
                "clicks": clicks,
                "impressions": impressions,
                "ctr": clicks / impressions if impressions else 0,
                "position": 4,
            }
            for i in range((end - start).days + 1)
        ],
    }


def history(*, yearly=False, yy_clicks=30, prior_clicks=30):
    values = [
        generation(10, date(2026, 8, 6), date(2026, 9, 2), prior_clicks),
        generation(11, date(2026, 9, 3), date(2026, 9, 30), 10),
    ]
    if yearly:
        values.extend(
            [
                generation(12, date(2025, 8, 1), date(2025, 8, 28)),
                generation(13, date(2025, 9, 3), date(2025, 9, 30), yy_clicks),
            ]
        )
    return values


@pytest.mark.parametrize("n", [0, 1, 2])
def test_small_n_neutral(n):
    panel = effectiveness([measurement(i + 1) for i in range(n)])
    assert priority_factor(panel, "metadata_pr", "technical_canonical")["factor"] == 1
    assert all(g["shrinkage"] == 0 for g in panel["groups"])


@pytest.mark.parametrize("horizon", [28, 90])
def test_new_page_has_no_comparative_effectiveness_or_priority_bonus(horizon):
    record = measurement(1, horizon=horizon)
    record.update(work_type="new_article", recipe_key="owner_editorial")
    record["baseline"] = {"state": "new_page", "gsc_page": {"state": "new_page", "metrics": None}}
    record["document"]["observed_change"]["gsc_page"] = dict.fromkeys(
        ["clicks", "impressions", "ctr", "position"]
    )
    panel = effectiveness([record])
    assert panel["state"] == "unavailable"
    assert panel["excluded_measurements"] == 1
    assert panel["groups"] == []
    assert priority_factor(panel, "new_article", "owner_editorial")["factor"] == 1


@pytest.mark.parametrize("horizon", [28, 90])
def test_exact_shrinkage_weighted_deltas_and_horizons(horizon):
    panel = effectiveness([measurement(i + 1, horizon=horizon) for i in range(5)])
    group = next(g for g in panel["groups"] if g["recipe_key"])
    assert group["sample_size"] == group["effective_sample_size"] == 5
    assert group["factor"] == 1.1 and group["shrinkage"] == 0.5
    assert group["metrics"]["clicks"]["mean_delta"] == 100
    assert group["metrics"]["position"]["shrunk_delta"] == -1
    assert group["horizon"] == horizon
    assert panel == effectiveness(
        copy.deepcopy([measurement(i + 1, horizon=horizon) for i in range(5)])
    )


def test_confounders_downweight_and_prevent_false_sample_confidence():
    clean = effectiveness([measurement(i + 1) for i in range(6)])
    affected = effectiveness([measurement(i + 1, confounders=1) for i in range(6)])
    assert (
        priority_factor(affected, "metadata_pr", "technical_canonical")["factor"]
        < priority_factor(clean, "metadata_pr", "technical_canonical")["factor"]
    )
    neutral = effectiveness([measurement(i + 1, confounders=2) for i in range(6)])
    assert priority_factor(neutral, "metadata_pr", "technical_canonical")["factor"] == 1
    mixed = effectiveness([measurement(1, delta=-100, confounders=1), measurement(2)])
    assert next(g for g in mixed["groups"] if g["recipe_key"])["metrics"]["clicks"][
        "mean_delta"
    ] == pytest.approx(33.333333)


@pytest.mark.parametrize("delta", [-100000, 100000])
def test_factor_bounded_and_applied_as_explained_priority_input(delta):
    data = packet()
    data["learning"] = {"measurements": [measurement(i + 20, delta=delta) for i in range(100)]}
    result = build_snapshot(data)
    item = next(i for i in result["strategy"]["items"] if i["kind"] == "technical")
    observed = item["priority"]["inputs"]["observed_effectiveness"]
    assert 0.8 <= observed["factor"] <= 1.2
    assert item["priority"]["score"] == round(5 * observed["factor"], 6)
    assert "Observed on this site" in observed["label"]
    assert set(item["evidence_ids"]) <= result["evidence"].keys()


def test_invalid_unknown_missing_metrics_and_duplicate_samples():
    value = measurement(1)
    value["document"]["observed_change"]["gsc_page"]["ctr"] = None
    panel = effectiveness([value])
    assert panel["groups"][0]["metrics"]["ctr"]["mean_delta"] is None
    with pytest.raises(ValueError, match="Duplicate"):
        effectiveness([value, value])
    value["document"]["observed_change"]["gsc_page"]["clicks"] = float("nan")
    with pytest.raises(ValueError):
        effectiveness([value])
    value["document"]["state"] = "failed"
    assert effectiveness([value])["state"] == "unavailable"


def test_monthly_decline_flagged_seasonality_and_as_reported():
    panel = decay(history(), [], "2026-10-03")
    page = panel["pages"][0]
    assert panel["state"] == "partial"
    assert page["declining"] and page["seasonality_possible"]
    assert page["basis"] == "prior_28_days"
    assert page["prior_28_days"]["baseline"]["coverage"]["complete"] is False


@pytest.mark.parametrize("yy_clicks,declining", [(30, True), (10, False)])
def test_yearly_preferred_even_when_monthly_declines(yy_clicks, declining):
    page = decay(history(yearly=True, yy_clicks=yy_clicks), [], "2026-10-03")["pages"][0]
    assert page["basis"] == "year_over_year" and not page["seasonality_possible"]
    assert page["declining"] is declining
    assert page["prior_28_days"]["metrics"]["clicks"]["declining"]


@pytest.mark.parametrize("case", ["low_volume", "small_drop", "noisy", "weekly_noisy"])
def test_thresholds_minimum_volume_and_noise(case):
    values = history()
    if case == "low_volume":
        values = [
            generation(10, date(2026, 8, 6), date(2026, 9, 2), 2, 20),
            generation(11, date(2026, 9, 3), date(2026, 9, 30), 0, 0),
        ]
    elif case == "small_drop":
        values = history(prior_clicks=12)
    else:
        for i, r in enumerate(values[0]["rows"]):
            r["clicks"] = 300 if (i == 0 if case == "noisy" else i < 7) else 0
            r["impressions"] = 200
            if r["clicks"] > r["impressions"]:
                r["impressions"] = 300
    assert not decay(values, [], "2026-10-03")["pages"][0]["declining"]


@pytest.mark.parametrize(
    "case", ["missing_day", "lag", "no_date", "partial_year", "duplicate", "invalid"]
)
def test_partial_unavailable_and_failure_never_estimated(case):
    values = history(yearly=case == "partial_year")
    if case == "missing_day":
        values[1]["rows"].pop()
    elif case == "lag":
        values[1]["coverage"]["first_incomplete_date"] = "2026-09-30"
    elif case == "no_date":
        values = []
    elif case == "partial_year":
        values[-1]["rows"].pop()
    elif case == "duplicate":
        values[1]["rows"].append(values[1]["rows"][0])
    else:
        values[1]["rows"][0]["clicks"] = -1
    if case in {"duplicate", "invalid"}:
        with pytest.raises(ValueError):
            decay(values, [], "2026-10-03")
    else:
        panel = decay(values, [], "2026-10-03")
        if case == "no_date":
            assert panel["state"] == "unavailable"
        elif case == "partial_year":
            assert panel["pages"][0]["seasonality_possible"]
            assert panel["pages"][0]["year_over_year"]["metrics"] is None
        else:
            assert panel["pages"][0]["state"] == "partial"
            assert panel["pages"][0]["prior_28_days"]["metrics"] is None
            assert not panel["pages"][0]["declining"]


def test_changed_pages_excluded_and_refresh_is_proposal_only():
    changes = [{"page_url": "https://example.invalid/", "post_end": "2026-10-01"}]
    panel = decay(history(), changes, "2026-10-03")
    assert not panel["pages"] and panel["excluded_pages"] == [changes[0]["page_url"]]
    data = packet()
    data["learning"] = {"as_of": "2026-10-03", "generations": history(), "changes": []}
    result = build_snapshot(data)
    item = next(i for i in result["strategy"]["items"] if i["kind"] == "decay")
    assert item["action"]["kind"] == "brief"
    assert item["action"]["payload"]["kind"] == "content_refresh"
    assert item["autonomy"].startswith("Owner required")
    assert "decision" not in item
    assert set(item["evidence_ids"]) <= result["evidence"].keys()
