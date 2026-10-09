import ipaddress
import subprocess
from types import SimpleNamespace

import lab_network as lab
import pytest
from signal_core.crawl_urls import validate_public_addresses


def test_sampler_rejects_multicast_private_and_documentation_answers(monkeypatch):
    valid = lab.candidate_network()
    draws = iter(
        int(ipaddress.ip_address(value))
        for value in ("224.0.0.0", "127.0.0.0", "192.0.2.0", valid.gateway)
    )
    monkeypatch.setattr(lab.secrets, "randbits", lambda _: next(draws))
    assert lab.candidate_network() == valid
    subnet = ipaddress.ip_network(valid.subnet)
    validate_public_addresses(tuple(str(subnet.network_address + i) for i in range(8)))


def test_sampler_exhaustion_is_fail_closed(monkeypatch):
    monkeypatch.setattr(lab.secrets, "randbits", lambda _: int(ipaddress.ip_address("224.0.0.0")))
    with pytest.raises(lab.LabNetworkError, match="No public-shaped"):
        lab.candidate_network()


def test_atomic_collision_retry_preserves_isolation_and_exact_owner(monkeypatch):
    first, second = lab.candidate_network(), lab.candidate_network()
    fixtures = iter((first, second))
    monkeypatch.setattr(lab, "candidate_network", lambda: next(fixtures))
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=int(len(calls) == 1),
            stderr="Pool overlaps with other one on this address space",
        )

    monkeypatch.setattr(lab.subprocess, "run", run)
    assert lab.create_public_network("synthetic-run-network", "synthetic-run-label") == second
    for call, fixture in zip(calls, (first, second), strict=True):
        assert call[-1] == "synthetic-run-network"
        assert call[call.index("--label") + 1] == "synthetic-run-label"
        assert call[call.index("--subnet") + 1] == fixture.subnet
        assert "--internal" in call
        assert "com.docker.network.bridge.enable_ip_masquerade=false" in call
        assert "com.docker.network.bridge.gateway_mode_ipv4=isolated" in call
        assert "--publish" not in call


@pytest.mark.parametrize("failure", ("daemon", "timeout", "collision"))
def test_network_creation_failures_are_bounded_and_sanitized(monkeypatch, failure):
    fixture = lab.candidate_network()
    monkeypatch.setattr(lab, "candidate_network", lambda: fixture)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if failure == "timeout":
            raise subprocess.TimeoutExpired("private-command", 30)
        return SimpleNamespace(
            returncode=1,
            stderr=(
                "Pool overlaps with other one on this address space"
                if failure == "collision"
                else "private-daemon-output"
            ),
        )

    monkeypatch.setattr(lab.subprocess, "run", run)
    with pytest.raises(lab.LabNetworkError) as captured:
        lab.create_public_network("synthetic-run-network", "synthetic-run-label")
    assert "private" not in str(captured.value)
    assert len(calls) == (32 if failure == "collision" else 1)


@pytest.mark.parametrize("kind", ("container", "network"))
def test_cleanup_recovers_an_owned_resource_without_creation_acknowledgement(monkeypatch, kind):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="synthetic-owned\n", stderr="")

    monkeypatch.setattr(lab.subprocess, "run", run)
    lab.remove_owned_resource(kind, "synthetic-owned", "synthetic-lab=synthetic-run")
    assert len(calls) == 2
    assert calls[0][:3] == ["docker", kind, "ls"]
    assert calls[0][calls[0].index("--filter") + 1] == "label=synthetic-lab=synthetic-run"
    assert calls[1][:3] == ["docker", kind, "rm"]
    assert calls[1][-1] == "synthetic-owned"
    assert ("--force" in calls[1]) is (kind == "container")


@pytest.mark.parametrize("kind", ("container", "network"))
def test_cleanup_never_removes_resources_outside_exact_name_and_label(monkeypatch, kind):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="synthetic-other-run\n", stderr="")

    monkeypatch.setattr(lab.subprocess, "run", run)
    lab.remove_owned_resource(kind, "synthetic-owned", "synthetic-lab=synthetic-run")
    assert len(calls) == 1
    assert "rm" not in calls[0]
    assert calls[0][calls[0].index("--filter") + 1] == "label=synthetic-lab=synthetic-run"


@pytest.mark.parametrize("failure", ("query", "timeout", "remove"))
def test_cleanup_failures_remain_explicit_and_sanitized(monkeypatch, failure):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if failure == "timeout":
            raise subprocess.TimeoutExpired("private-command", 30)
        return SimpleNamespace(
            returncode=int(failure == "query" or len(calls) == 2),
            stdout="synthetic-owned\n",
            stderr="private-daemon-output",
        )

    monkeypatch.setattr(lab.subprocess, "run", run)
    with pytest.raises(lab.LabNetworkError) as captured:
        lab.remove_owned_resource("network", "synthetic-owned", "synthetic-lab=synthetic-run")
    assert "private" not in str(captured.value)
    assert len(calls) == (2 if failure == "remove" else 1)


def test_cleanup_rejects_an_unsupported_resource_kind():
    with pytest.raises(lab.LabNetworkError, match="Unsupported"):
        lab.remove_owned_resource("image", "synthetic-owned", "synthetic-lab=synthetic-run")
