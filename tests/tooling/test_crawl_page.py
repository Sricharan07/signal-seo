from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from signal_core.crawl_artifacts import FetchObservationRecorded
from signal_core.crawl_page import (
    CrawlPageAttemptPending,
    classify_origin_completion,
    execute_crawl_page_attempt,
)


def observation(status=200, retry_after=None, *, finished_at=None):
    finished = finished_at or datetime(2026, 9, 9, 12, tzinfo=UTC)
    headers = () if retry_after is None else (("retry-after", retry_after),)
    return FetchObservationRecorded(
        observation_id=uuid4(),
        tenant_id=uuid4(),
        site_id=uuid4(),
        crawl_run_id=uuid4(),
        frontier_id=uuid4(),
        url_id=uuid4(),
        fetch_attempt_id=uuid4(),
        started_at=finished - timedelta(milliseconds=25),
        finished_at=finished,
        outcome="fetched",
        http_status=status,
        final_url="https://example.test/",
        response_headers=headers,
        redirect_chain=(),
        resolved_address="8.8.8.8",
        media_type="text/html",
        decoded_bytes=1,
        elapsed_ms=25,
        network_profile_sha256="ab" * 32,
        raw_artifact=None,
        duplicate=False,
    )


@pytest.mark.parametrize(
    ("status", "header", "expected_ms", "basis"),
    [
        (200, None, None, "none"),
        (429, "15", 15_000, "provider_seconds"),
        (429, None, 60_000, "fallback_missing"),
        (429, "not-a-date", 60_000, "fallback_invalid"),
        (503, None, None, "fallback_missing"),
        (503, "0", None, "fallback_invalid"),
        (503, "999999", 86_400_000, "capped"),
    ],
)
def test_completion_classification_preserves_bounded_backoff_basis(
    status, header, expected_ms, basis
):
    completion, observed_basis = classify_origin_completion(observation(status, header))

    assert (
        completion.kind
        == {
            200: "success",
            429: "rate_limited",
            503: "service_unavailable",
        }[status]
    )
    assert completion.retry_after_ms == expected_ms
    assert observed_basis == basis


def test_completion_classification_accepts_future_http_date():
    finished = datetime(2026, 9, 9, 12, tzinfo=UTC)
    completion, basis = classify_origin_completion(
        observation(503, "Wed, 09 Sep 2026 12:00:10 GMT", finished_at=finished)
    )

    assert completion.retry_after_ms == 10_000
    assert basis == "provider_date"


def test_completion_classification_rejects_untyped_evidence():
    with pytest.raises(ValueError, match="fetch observation"):
        classify_origin_completion(object())


def test_public_outcomes_are_immutable_and_opaque():
    pending = CrawlPageAttemptPending(
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4(), None, "dispatch_outcome_unknown"
    )
    assert "dispatch_outcome_unknown" not in repr(pending)
    assert replace(pending, reason="permit_finished_without_attempt").reason.startswith("permit")
    with pytest.raises(AttributeError):
        pending.reason = "changed"


def test_page_attempt_rejects_shared_or_untyped_connections_before_io():
    connection = object()
    with pytest.raises(ValueError, match="separate database connections"):
        execute_crawl_page_attempt(
            connection,
            connection,
            object(),
            object(),
            object(),
            object(),
            object(),
            permit_id=uuid4(),
            admission_policy=object(),
            network_profile_sha256="ab" * 32,
            artifact_key=object(),
            retain_until=datetime.now(UTC) + timedelta(days=1),
        )
    with pytest.raises(ValueError, match="database connections"):
        execute_crawl_page_attempt(
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
            permit_id=uuid4(),
            admission_policy=object(),
            network_profile_sha256="ab" * 32,
            artifact_key=object(),
            retain_until=datetime.now(UTC) + timedelta(days=1),
        )
