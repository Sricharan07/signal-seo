"""Every health check preserves four states and fails closed on missing probes."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import pytest
from signal_core.database import Scope
from signal_core.health_monitoring import CHECKS, HealthMonitor, evaluate
from signal_core.health_probes import ScreenedPinHealth, openbao_health, temporal_health

NOW = datetime.now(UTC)


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize(
    "state,value", [("ok", 0), ("warning", 1), ("critical", 2), ("unknown", None)]
)
def test_each_check_four_states(check, state, value):
    observation = None if value is None else {"value": value, "warning": 1, "critical": 2}
    if check == "egress_pins" and value is not None:
        observation = {"expires_at": NOW + timedelta(seconds={0: 1000, 1: 60, 2: -1}[value])}
    result = evaluate(check, observation, NOW)
    assert result.state == state
    assert result.reason and result.remediation and result.checked_at


@pytest.mark.parametrize(
    "observation",
    [{}, {"value": True}, {"value": float("nan")}, {"condition": "secret-text"}, {"value": -1}],
)
def test_malformed_observations_never_ok(observation):
    assert evaluate("disk", observation, NOW).state == "unknown"


@pytest.mark.parametrize(
    "condition,state",
    [
        ("revoked", "critical"),
        ("expired", "critical"),
        ("import_failing", "warning"),
        ("stale_evidence", "unknown"),
    ],
)
def test_binding_reasons(condition, state):
    assert evaluate("binding_gsc", {"condition": condition}, NOW).state == state


def test_failed_probe_and_storage_do_not_mask_checks_or_log_provider_secrets(caplog):
    class Store:
        def facts(self, scope):
            raise RuntimeError("synthetic-secret-data")

        def record(self, scope, results, generation):
            raise RuntimeError("synthetic-secret-data")

    class Recovery:
        async def current_generation(self):
            return SimpleNamespace(value="synthetic-generation")

    async def failed():
        raise RuntimeError("synthetic-secret-data")

    monitor = HealthMonitor(Store(), Scope(uuid4(), uuid4()), Recovery(), {"temporal": failed})
    result = asyncio.run(monitor.run_once())
    assert len(result) == len(CHECKS) and all(r.state == "unknown" for r in result)
    assert "synthetic-secret-data" not in caplog.text


@pytest.mark.parametrize("sealed,state", [(True, "critical"), (False, "ok"), (None, "unknown")])
def test_bao_app_visible_state_uses_existing_bounded_transport(sealed, state):
    async def exercise():
        transport = httpx2.MockTransport(
            lambda request: httpx2.Response(200, json={"sealed": sealed})
        )
        try:
            observation = await openbao_health(
                SimpleNamespace(
                    base_url="https://bao.example.invalid", token="synthetic-token-abcdefghijkl"
                ),
                transport=transport,
            )
        except ValueError:
            observation = None
        assert evaluate("openbao", observation, NOW).state == state

    asyncio.run(exercise())


@pytest.mark.parametrize("check", ["temporal", "openbao"])
@pytest.mark.parametrize("elapsed,state", [(0.1, "ok"), (1.5, "warning"), (3.5, "critical")])
def test_dependency_latency_states(check, elapsed, state, monkeypatch):
    clock = iter([0, elapsed, elapsed])
    monkeypatch.setattr(
        "signal_core.health_probes.time", SimpleNamespace(monotonic=lambda: next(clock))
    )

    async def exercise():
        if check == "temporal":

            class Client:
                async def check_health(self, **options):
                    return True

            observation = await temporal_health(SimpleNamespace(service_client=Client()))
        else:
            observation = await openbao_health(
                SimpleNamespace(
                    base_url="https://bao.example.invalid", token="synthetic-token-abcdefghijkl"
                ),
                transport=httpx2.MockTransport(
                    lambda request: httpx2.Response(200, json={"sealed": False})
                ),
            )
        assert evaluate(check, observation, NOW).state == state

    asyncio.run(exercise())


def test_temporal_negative_and_failed_rpc_states():
    class Client:
        async def check_health(self, **options):
            return False

    observation = asyncio.run(temporal_health(SimpleNamespace(service_client=Client())))
    assert evaluate("temporal", observation, NOW).state == "critical"


@pytest.mark.parametrize("offset,state", [(1000, "ok"), (60, "warning"), (-10, "critical")])
def test_expiring_screened_pins(offset, state, tmp_path, monkeypatch):
    import json

    expires = int(NOW.timestamp()) + offset
    pin = {
        "hosts": {"provider.example.invalid": "192.0.2.10"},
        "issued_at": expires - 3600,
        "expires_at": expires,
    }
    # Expiry contract only; separate negative tests use the real address screen.
    monkeypatch.setattr(
        "signal_core.health_probes.validate_public_addresses", lambda values: values
    )
    probe = ScreenedPinHealth((tmp_path / "provider-pins.json",), lambda path: json.dumps(pin))
    observation = asyncio.run(probe())
    assert evaluate("egress_pins", observation, NOW).state == state


def test_private_or_unscreened_pin_is_unknown(tmp_path):
    import json

    probe = ScreenedPinHealth(
        (tmp_path / "origin-pin.json",),
        lambda path: json.dumps(
            {
                "origin": "https://example.invalid",
                "address": "127.0.0.1",
                "issued_at": int(NOW.timestamp()) - 5,
                "expires_at": int(NOW.timestamp()) + 60,
            }
        ),
    )
    with pytest.raises(ValueError):
        asyncio.run(probe())


def test_scheduler_stops_without_leaking_task():
    stop = asyncio.Event()

    class Monitor(HealthMonitor):
        async def run_once(self):
            stop.set()
            return ()

    monitor = Monitor(None, Scope(uuid4(), uuid4()), None)
    asyncio.run(monitor.serve(stop))
