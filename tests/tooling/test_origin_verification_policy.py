import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from signal_core.crawl_http import (
    CrawlFetchRejected,
    CrawlFetchResult,
    CrawlFetchUnavailable,
    PinnedHttpFetcher,
)
from signal_core.origin_verification import (
    InvalidOriginVerification,
    OriginProofObservation,
    PreparedOriginVerification,
    observe_origin_proof,
    record_origin_verification,
)


def prepared():
    challenge_id = uuid4()
    content = f"signal-site-verification={challenge_id}\n"
    now = datetime.now(UTC)
    return PreparedOriginVerification(
        tenant_id=uuid4(),
        user_id=uuid4(),
        site_id=uuid4(),
        challenge_id=challenge_id,
        origin="https://example.invalid",
        proof_url=("https://example.invalid/.well-known/signal-site-verification.txt"),
        proof_content=content,
        proof_sha256=hashlib.sha256(content.encode("ascii")).digest(),
        expires_at=now + timedelta(minutes=30),
    )


def fetch_result(proof, **overrides):
    body = proof.proof_content.encode("ascii")
    values = {
        "schema_version": 1,
        "original_url": proof.proof_url,
        "final_url": proof.proof_url,
        "normalized_key": proof.proof_url,
        "outcome": "fetched",
        "http_status": 200,
        "media_type": "text/plain",
        "response_headers": (("content-type", "text/plain"),),
        "redirect_chain": (),
        "resolved_address": "93.184.216.34",
        "body": body,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "decoded_bytes": len(body),
        "elapsed_ms": 8,
        **overrides,
    }
    return CrawlFetchResult(**values)


def test_observer_uses_exact_pinned_plaintext_policy(monkeypatch):
    proof = prepared()
    boundary = PinnedHttpFetcher(lambda host, port, timeout: ["93.184.216.34"])
    calls = []

    def fetch_text(url, *, policy):
        calls.append((url, policy))
        return fetch_result(proof)

    monkeypatch.setattr(boundary, "fetch_text", fetch_text)
    observation = observe_origin_proof(boundary, proof)

    assert observation == OriginProofObservation(
        outcome="matched",
        http_status=200,
        media_type="text/plain",
        response_sha256=proof.proof_sha256,
        final_url=proof.proof_url,
        resolved_address="93.184.216.34",
        elapsed_ms=8,
    )
    assert calls[0][0] == proof.proof_url
    assert calls[0][1].allowed_origins == (proof.origin,)
    assert calls[0][1].max_redirects == 0
    assert calls[0][1].max_body_bytes == 1024


@pytest.mark.parametrize(
    ("result_overrides", "expected"),
    [
        ({"body": b"wrong", "body_sha256": hashlib.sha256(b"wrong").hexdigest()}, "proof_mismatch"),
        (
            {
                "http_status": 404,
                "body": b"",
                "body_sha256": hashlib.sha256(b"").hexdigest(),
            },
            "proof_not_found",
        ),
        ({"http_status": 500}, "invalid_response"),
        ({"media_type": None}, "invalid_response"),
        (
            {"outcome": "unsupported_media_type", "body": b"", "body_sha256": None},
            "invalid_response",
        ),
    ],
)
def test_observer_classifies_nonmatching_responses(monkeypatch, result_overrides, expected):
    proof = prepared()
    boundary = PinnedHttpFetcher(lambda host, port, timeout: ["93.184.216.34"])
    monkeypatch.setattr(
        boundary,
        "fetch_text",
        lambda url, *, policy: fetch_result(proof, **result_overrides),
    )

    assert observe_origin_proof(boundary, proof).outcome == expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (CrawlFetchRejected("blocked"), "policy_rejected"),
        (CrawlFetchUnavailable("unavailable"), "transport_unavailable"),
    ],
)
def test_observer_sanitizes_boundary_failures(monkeypatch, error, expected):
    proof = prepared()
    boundary = PinnedHttpFetcher(lambda host, port, timeout: ["93.184.216.34"])

    def fail(url, *, policy):
        raise error

    monkeypatch.setattr(boundary, "fetch_text", fail)
    observation = observe_origin_proof(boundary, proof)

    assert observation == OriginProofObservation(outcome=expected)
    assert "blocked" not in repr(observation)
    assert "unavailable" not in repr(observation)


@pytest.mark.parametrize(
    "observation",
    [
        OriginProofObservation(outcome="candidate"),
        OriginProofObservation(outcome="matched"),
        OriginProofObservation(outcome="policy_rejected", http_status=403),
        OriginProofObservation(outcome="proof_mismatch", media_type="x" * 101),
        OriginProofObservation(outcome="proof_mismatch", resolved_address="x" * 65),
    ],
)
def test_malformed_observations_fail_before_database_access(observation):
    proof = prepared()
    with pytest.raises(InvalidOriginVerification):
        record_origin_verification(
            None,
            session_token="x" * 43,
            current_recovery_generation="generation-1",
            requested_site_id=proof.site_id,
            challenge_id=proof.challenge_id,
            origin=proof.origin,
            idempotency_key=uuid4(),
            observation=observation,
        )


def test_proof_bearing_values_are_not_rendered_in_representations():
    proof = prepared()
    assert proof.proof_content not in repr(proof)
    observation = replace(
        OriginProofObservation(outcome="proof_mismatch"),
        response_sha256=b"x" * 32,
    )
    assert "xxxxxxxx" not in repr(observation)
