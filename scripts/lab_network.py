"""Atomically claim run-private public-shaped fixture subnets, never internet egress."""

import ipaddress
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/control_plane/src"))

from signal_core.crawl_urls import CrawlUrlRejected, validate_public_addresses  # noqa: E402


class LabNetworkError(RuntimeError):
    """A synthetic network could not establish its isolation boundary."""


@dataclass(frozen=True)
class PublicFixtureNetwork:
    subnet: str
    gateway: str
    address: str
    unserved_address: str


def candidate_network() -> PublicFixtureNetwork:
    for _ in range(256):
        subnet = ipaddress.ip_network((secrets.randbits(32), 29), strict=False)
        try:
            validate_public_addresses(tuple(str(subnet.network_address + i) for i in range(8)))
        except CrawlUrlRejected:
            continue
        return PublicFixtureNetwork(
            str(subnet),
            str(subnet.network_address + 1),
            str(subnet.network_address + 2),
            str(subnet.network_address + 3),
        )
    raise LabNetworkError("No public-shaped synthetic subnet was available.")


def create_public_network(name: str, label: str) -> PublicFixtureNetwork:
    for _ in range(32):
        fixture = candidate_network()
        try:
            result = subprocess.run(
                [
                    "docker",
                    "network",
                    "create",
                    "--internal",
                    "--subnet",
                    fixture.subnet,
                    "--gateway",
                    fixture.gateway,
                    "--opt",
                    "com.docker.network.bridge.enable_ip_masquerade=false",
                    "--opt",
                    "com.docker.network.bridge.gateway_mode_ipv4=isolated",
                    "--label",
                    label,
                    name,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            raise LabNetworkError("Synthetic network creation failed.") from None
        if result.returncode == 0:
            return fixture
        # Docker IPAM is the atomic arbiter across processes and worktrees. Retry
        # only a conflicting pool; every other failure remains visible.
        if "Pool overlaps with other one on this address space" not in result.stderr:
            raise LabNetworkError("Synthetic network creation failed.")
    raise LabNetworkError("Synthetic subnet collision retry limit reached.")


def remove_owned_resource(kind: str, name: str, label: str) -> None:
    if kind not in {"container", "network"}:
        raise LabNetworkError("Unsupported synthetic resource kind.")
    try:
        result = subprocess.run(
            ["docker", kind, "ls"]
            + (["--all"] if kind == "container" else [])
            + [
                "--filter",
                f"label={label}",
                "--format",
                "{{.Names}}" if kind == "container" else "{{.Name}}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise LabNetworkError("Synthetic resource ownership could not be confirmed.")
        if name not in result.stdout.splitlines():
            return
        removed = subprocess.run(
            ["docker", kind, "rm"]
            + (["--force", "--volumes"] if kind == "container" else [])
            + [name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if removed.returncode != 0:
            raise LabNetworkError("Synthetic resource cleanup was not confirmed.")
    except (OSError, subprocess.SubprocessError):
        raise LabNetworkError("Synthetic resource cleanup was not confirmed.") from None
