"""Site-local, deterministic observations. No provider I/O or execution authority."""

import calendar
import math
from collections import defaultdict
from datetime import date, timedelta
from statistics import median, variance

VERSION = "site-observed-learning-v1"
METRICS = ("clicks", "impressions", "ctr", "position")
LABEL = "Observed on this site; not evidence of causation."


def _numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid observed metric.")
    return value


def effectiveness(records):
    groups = defaultdict(list)
    excluded = 0
    seen = set()
    for record in records:
        identity = (record["operation_id"], record["horizon"])
        if identity in seen:
            raise ValueError("Duplicate measurement horizon.")
        seen.add(identity)
        if (
            record["horizon"] not in (28, 90)
            or record["document"]["state"] != "measured_as_reported"
            or record["baseline"].get("state") == "new_page"
            or not record.get("work_type")
            or not record.get("recipe_key")
        ):
            excluded += 1
            continue
        for recipe in (None, record["recipe_key"]):
            groups[(record["work_type"], recipe, record["horizon"])].append(record)
    summaries = []
    for (work, recipe, horizon), samples in sorted(groups.items(), key=lambda x: str(x[0])):
        weights = [1 / (1 + len(s["document"]["confounders"])) for s in samples]
        effective_n = sum(weights)
        shrinkage = effective_n / (effective_n + 5) if effective_n >= 3 else 0
        deltas = {}
        signals = []
        for metric in METRICS:
            values = [
                (_numeric(s["document"]["observed_change"]["gsc_page"][metric]), w)
                for s, w in zip(samples, weights, strict=True)
                if s["document"]["observed_change"]["gsc_page"].get(metric) is not None
            ]
            n = sum(w for _, w in values)
            mean = sum(v * w for v, w in values) / n if n else None
            deltas[metric] = {
                "sample_size": len(values),
                "effective_sample_size": round(n, 6),
                "mean_delta": round(mean, 6) if mean is not None else None,
                "shrunk_delta": round(mean * (n / (n + 5) if n >= 3 else 0), 6)
                if mean is not None
                else None,
            }
        for s, w in zip(samples, weights, strict=True):
            baseline = s["baseline"]["gsc_page"].get("metrics") or {}
            changes = s["document"]["observed_change"]["gsc_page"]
            ratios = [
                max(-1, min(1, _numeric(changes[k]) / _numeric(baseline[k])))
                for k in ("clicks", "impressions")
                if baseline.get(k) is not None
                and _numeric(baseline[k]) > 0
                and changes.get(k) is not None
            ]
            if ratios:
                signals.append((sum(ratios) / len(ratios), w))
        signal_n = sum(w for _, w in signals)
        signal = sum(v * w for v, w in signals) / signal_n if signal_n >= 3 else 0
        signal_shrinkage = signal_n / (signal_n + 5) if signal_n >= 3 else 0
        summaries.append(
            {
                "work_type": work,
                "recipe_key": recipe,
                "horizon": horizon,
                "sample_size": len(samples),
                "effective_sample_size": round(effective_n, 6),
                "shrinkage": round(shrinkage, 6),
                "factor": round(1 + 0.2 * signal_shrinkage * signal, 6),
                "metrics": deltas,
                "evidence_ids": sorted(s["id"] for s in samples),
            }
        )
    return {
        "state": "partial" if summaries else "unavailable",
        "label": LABEL,
        "reason": "Provider-reported cohorts; completeness not guaranteed."
        if summaries
        else "No eligible measured 28/90-day page observations.",
        "excluded_measurements": excluded,
        "groups": summaries,
    }


def priority_factor(panel, work, recipe):
    # Never pool sites or count the same operation's two horizons as two samples.
    selected = [g for g in panel["groups"] if g["work_type"] == work and g["recipe_key"] == recipe]
    if not selected and recipe is not None:
        selected = [
            g for g in panel["groups"] if g["work_type"] == work and g["recipe_key"] is None
        ]
    return {
        "label": LABEL,
        "factor": round(sum(g["factor"] for g in selected) / len(selected), 6) if selected else 1,
        "groups": selected,
        "bounds": [0.8, 1.2],
        "basis": "Equal horizon factors; exact recipe preferred, "
        "work-type fallback; low n neutral.",
    }


def _months_before(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _window(generations, page, start, end):
    eligible = [
        g
        for g in generations
        if set(g["dimensions"]) == {"page", "date"}
        and len(g["dimensions"]) == 2
        and date.fromisoformat(g["window"]["start"]) <= start
        and date.fromisoformat(g["window"]["end"]) >= end
    ]
    if not eligible:
        return {
            "state": "unavailable",
            "reason": "No daily page generation covers window.",
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "evidence_ids": [],
            "coverage": None,
            "daily": None,
        }
    g = max(eligible, key=lambda x: (x["imported_at"], x["id"]))
    result = {
        "state": "partial",
        "reason": "Missing page-days are unknown, never zero.",
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "evidence_ids": [g["id"]],
        "coverage": g["coverage"],
        "daily": None,
    }
    lag = g["coverage"].get("first_incomplete_date")
    if lag and date.fromisoformat(lag) <= end:
        result["reason"] = "Provider lag overlaps window."
        return result
    daily = {}
    for row in g["rows"]:
        labels = dict(zip(g["dimensions"], row["keys"], strict=True))
        day = date.fromisoformat(labels["date"])
        if labels["page"] != page or not start <= day <= end:
            continue
        if day in daily:
            raise ValueError("Duplicate page-day in source.")
        values = {k: _numeric(row[k]) for k in ("clicks", "impressions")}
        if not 0 <= values["clicks"] <= values["impressions"]:
            raise ValueError("Invalid page counts.")
        daily[day] = values
    days = [start + timedelta(days=i) for i in range(28)]
    if any(day not in daily for day in days):
        return result
    result.update(
        state="as_reported",
        reason="Observed returned page-days only; completeness not guaranteed.",
        daily=[daily[d] for d in days],
    )
    return result


def _comparison(recent, before):
    result = {"state": "partial", "baseline": before, "recent": recent, "metrics": None}
    if recent["daily"] is None or before["daily"] is None:
        return result
    metrics = {}
    for key, minimum in (("clicks", 100), ("impressions", 1000)):
        a, b = ([r[key] for r in w["daily"]] for w in (before, recent))
        total_a, total_b = sum(a), sum(b)
        weeks_a = [sum(a[i : i + 7]) for i in range(0, 28, 7)]
        weeks_b = [sum(b[i : i + 7]) for i in range(0, 28, 7)]

        def mad(values):
            return (1.4826 * median(abs(v - median(values)) for v in values)) ** 2

        noise = math.sqrt(
            max(
                total_a + total_b,
                28 * (variance(a) + variance(b)),
                4 * (variance(weeks_a) + variance(weeks_b)),
                28 * (mad(a) + mad(b)),
            )
        )
        drop = total_a - total_b
        metrics[key] = {
            "baseline": total_a,
            "recent": total_b,
            "relative_decline": round(drop / total_a, 6) if total_a else None,
            "noise": round(noise, 6),
            "minimum_volume": minimum,
            "declining": total_a >= minimum and drop >= total_a * 0.25 and drop > 3 * noise,
        }
    result.update(state="as_reported", metrics=metrics)
    return result


def decay(generations, changes, as_of):
    if not as_of or not generations:
        return {
            "state": "unavailable",
            "reason": "No current daily page imports.",
            "label": LABEL,
            "pages": [],
            "excluded_pages": [],
        }
    end = date.fromisoformat(as_of) - timedelta(days=3)
    start = end - timedelta(days=27)
    prior_end, prior_start = start - timedelta(days=1), start - timedelta(days=28)
    yy_end = _months_before(end, 12)
    yy_start = yy_end - timedelta(days=27)
    has_history = min(
        date.fromisoformat(g["window"]["start"]) for g in generations
    ) <= _months_before(end, 13)
    pages = sorted(
        {
            dict(zip(g["dimensions"], r["keys"], strict=True))["page"]
            for g in generations
            if set(g["dimensions"]) == {"page", "date"}
            for r in g["rows"]
        }
    )
    excluded = {c["page_url"] for c in changes if date.fromisoformat(c["post_end"]) >= prior_start}
    results = []
    for page in pages:
        if page in excluded:
            continue
        recent = _window(generations, page, start, end)
        monthly = _comparison(recent, _window(generations, page, prior_start, prior_end))
        yearly = (
            _comparison(recent, _window(generations, page, yy_start, yy_end))
            if has_history
            else None
        )
        chosen = yearly if yearly and yearly["state"] == "as_reported" else monthly
        basis = "year_over_year" if chosen is yearly else "prior_28_days"
        refs = sorted(set(chosen["recent"]["evidence_ids"] + chosen["baseline"]["evidence_ids"]))
        results.append(
            {
                "url": page,
                "state": chosen["state"],
                "basis": basis,
                "seasonality_possible": basis != "year_over_year",
                "year_over_year_reason": None
                if yearly and yearly["state"] == "as_reported"
                else "13 months of usable page history unavailable or partial.",
                "declining": bool(
                    chosen["metrics"] and any(m["declining"] for m in chosen["metrics"].values())
                ),
                "prior_28_days": monthly,
                "year_over_year": yearly,
                "evidence_ids": refs,
            }
        )
    return {
        "state": "partial" if results else "unavailable",
        "reason": "Returned cohorts only; absent pages and dates remain unknown.",
        "label": LABEL,
        "pages": results,
        "excluded_pages": sorted(set(pages) & excluded),
    }
