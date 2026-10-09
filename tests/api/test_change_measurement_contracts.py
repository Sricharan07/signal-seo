"""No response contract can promote provider-reported data to complete."""

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError
from signal_api.weekly_report_contracts import (
    ChangeMeasurementResponse,
    MeasurementSource,
    NeedsDecisionResponse,
)


@pytest.mark.parametrize(
    "mutation",
    [
        "complete_state",
        "complete_coverage",
        "missing_generation",
        "missing_metrics",
        "pending_metrics",
        "nonfinite_metrics",
    ],
)
def test_source_requires_exact_partial_evidence(mutation):
    value = {
        "state": "measured_as_reported",
        "reason": "AS_REPORTED_COMPLETENESS_NOT_GUARANTEED",
        "generation_id": uuid4(),
        "coverage": {"complete": False, "missing_data": "unknown"},
        "metrics": {"clicks": 10, "impressions": 100, "ctr": 0.1, "position": 4},
    }
    if mutation == "complete_state":
        value["state"] = "complete"
    elif mutation == "complete_coverage":
        value["coverage"] = {"complete": True}
    elif mutation == "missing_generation":
        value["generation_id"] = None
    elif mutation == "missing_metrics":
        value["metrics"] = None
    elif mutation == "pending_metrics":
        value["state"] = "awaiting_data"
    else:
        value["metrics"]["clicks"] = float("nan")
    with pytest.raises(ValidationError):
        MeasurementSource.model_validate(value)


def test_inbox_count_and_destinations_are_evidence_bound():
    identifier = uuid4()
    with pytest.raises(ValidationError):
        NeedsDecisionResponse.model_validate(
            {
                "count": 0,
                "url": "/approvals",
                "items": [
                    {
                        "revision_id": identifier,
                        "kind": "technical",
                        "url": f"/approvals?revision={identifier}",
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        NeedsDecisionResponse.model_validate(
            {
                "count": 1,
                "url": "/approvals",
                "items": [
                    {"revision_id": identifier, "kind": "technical", "url": "https://other.invalid"}
                ],
            }
        )


def new_page_measurement():
    source = {
        "state": "unavailable",
        "reason": "NO_PRE_CHANGE_WINDOW",
        "generation_id": None,
        "coverage": None,
        "metrics": None,
    }
    operation = uuid4()
    return {
        "operation_id": operation,
        "horizon": 7,
        "page_url": "https://example.invalid/new.html",
        "verified_live_at": "2026-09-21T12:00:00Z",
        "due_at": "2026-09-28T12:00:00Z",
        "baseline_start": None,
        "baseline_end": None,
        "baseline": {
            "state": "new_page",
            "reason": "NO_PRE_CHANGE_WINDOW",
            **{key: deepcopy(source) for key in ("gsc_page", "bing_site_context", "bing_page")},
        },
        "post_start": "2026-09-22",
        "post_end": "2026-09-28",
        "evidence_url": f"/changes#operation-{operation}",
        "verification_attempt_id": uuid4(),
        "observation": {
            "state": "not_yet_due",
            "reason": "HORIZON_NOT_YET_DUE",
            "post": None,
            "observed_change": None,
            "confounders": [],
        },
        "recorded_at": None,
    }


def test_new_page_measurement_has_no_pre_change_window_or_metrics():
    value = ChangeMeasurementResponse.model_validate(new_page_measurement())
    assert value.baseline.state == "new_page"
    assert value.baseline_start is None and value.baseline_end is None


@pytest.mark.parametrize(
    "mutation", ["start", "end", "metrics", "generation", "coverage", "delta", "missing_state"]
)
def test_new_page_cannot_fabricate_baseline_or_observed_change(mutation):
    value = new_page_measurement()
    if mutation in {"start", "end"}:
        value[f"baseline_{mutation}"] = "2026-09-20"
    elif mutation in {"generation", "coverage", "metrics"}:
        value["baseline"]["gsc_page"][{"generation": "generation_id"}.get(mutation, mutation)] = {
            "generation": str(uuid4()),
            "coverage": {"complete": False},
            "metrics": {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0},
        }[mutation]
    elif mutation == "delta":
        value["observation"]["observed_change"] = {
            source: {key: 0 for key in ("clicks", "impressions", "ctr", "position")}
            for source in ("gsc_page", "bing_site_context")
        }
    else:
        del value["baseline"]["state"]
        del value["baseline"]["reason"]
    with pytest.raises(ValidationError):
        ChangeMeasurementResponse.model_validate(value)
