from dataclasses import replace
from uuid import uuid4

import pytest
from signal_core.crawl_admission import (
    OriginAdmissionPolicy,
    OriginPermitCompletion,
    acquire_origin_permit,
    finish_origin_permit,
    reconcile_expired_origin_permits,
)


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"profile_version": 2},
        {"profile_version": True},
        {"min_delay_ms": 999},
        {"min_delay_ms": 60_001},
        {"min_delay_ms": True},
        {"permit_lease_seconds": 0},
        {"permit_lease_seconds": 121},
    ],
)
def test_origin_admission_policy_rejects_unsupported_values(overrides):
    with pytest.raises(ValueError, match="Invalid origin admission policy"):
        OriginAdmissionPolicy(**overrides)


@pytest.mark.parametrize(
    "arguments",
    [
        {"kind": "unknown", "observed_latency_ms": 1},
        {"kind": None, "observed_latency_ms": 1},
        {"kind": [], "observed_latency_ms": 1},
        {"kind": "success", "observed_latency_ms": -1},
        {"kind": "success", "observed_latency_ms": 120_001},
        {"kind": "success", "observed_latency_ms": True},
        {"kind": "success", "observed_latency_ms": 1, "retry_after_ms": 1000},
        {"kind": "rate_limited", "observed_latency_ms": 1},
        {"kind": "rate_limited", "observed_latency_ms": 1, "retry_after_ms": 999},
        {
            "kind": "service_unavailable",
            "observed_latency_ms": 1,
            "retry_after_ms": 86_400_001,
        },
    ],
)
def test_origin_completion_rejects_ambiguous_or_unbounded_values(arguments):
    with pytest.raises(ValueError):
        OriginPermitCompletion(**arguments)


def test_origin_completion_accepts_explicit_provider_and_local_outcomes():
    assert OriginPermitCompletion("success", 0).retry_after_ms is None
    assert OriginPermitCompletion("transport_error", 10).kind == "transport_error"
    assert OriginPermitCompletion("cancelled", 10).kind == "cancelled"
    assert OriginPermitCompletion("rate_limited", 10, 5000).retry_after_ms == 5000
    assert OriginPermitCompletion("service_unavailable", 10).retry_after_ms is None


class _ConnectionMustNotRun:
    autocommit = True

    def execute(self, *args, **kwargs):
        raise AssertionError("invalid input must fail before database I/O")


def test_public_operations_reject_untyped_authority_before_database_io():
    connection = _ConnectionMustNotRun()
    with pytest.raises(ValueError, match="frontier lease"):
        acquire_origin_permit(
            connection,
            object(),
            permit_id=uuid4(),
            permit_kind="robots",
            policy=OriginAdmissionPolicy(),
        )
    with pytest.raises(ValueError, match="active origin permit"):
        finish_origin_permit(
            connection,
            object(),
            OriginPermitCompletion("success", 1),
        )
    with pytest.raises(ValueError, match="reconciliation limit"):
        reconcile_expired_origin_permits(connection, limit=0)


def test_policy_and_completion_records_are_immutable_values():
    policy = OriginAdmissionPolicy()
    completion = OriginPermitCompletion("success", 25)
    assert replace(policy, min_delay_ms=2000).min_delay_ms == 2000
    assert replace(completion, observed_latency_ms=50).observed_latency_ms == 50
    with pytest.raises(AttributeError):
        policy.min_delay_ms = 50
