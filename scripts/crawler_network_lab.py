"""Qualify the crawl HTTP boundary on an isolated public-shaped Docker network."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lab_network import LabNetworkError, PublicFixtureNetwork, create_public_network
from lab_runtime import runtime_root

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = runtime_root(ROOT) / "crawler-network"
DOCKERFILE = ROOT / "deploy" / "crawler-network" / "Dockerfile"
BASE_IMAGE = (
    "python:3.12.14-slim-bookworm@"
    "sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254"
)
LABEL = "dev.signal.crawler-network-lab"


class LabError(RuntimeError):
    """Failure safe to print without command arguments or provider output."""


@dataclass(frozen=True)
class CrawlerNetwork:
    run_id: str
    image: str
    image_id: str
    network: str
    fixture: PublicFixtureNetwork


@contextmanager
def isolated_crawler_network():
    """Yield an invocation-owned synthetic origin and hardened crawler image."""
    require_free_space(ROOT)
    docker("info", "--format", "{{.ServerVersion}}", timeout=30)
    run_id = uuid.uuid4().hex[:12]
    image = f"signal-crawler-network-lab:{run_id}"
    network = f"signal-crawler-network-{run_id}"
    server = f"signal-crawler-network-{run_id}-origin"
    try:
        docker(
            "build",
            "--pull",
            "--file",
            str(DOCKERFILE.relative_to(ROOT)),
            "--tag",
            image,
            "--label",
            f"{LABEL}={run_id}",
            ".",
            timeout=600,
        )
        image_id = docker("image", "inspect", "--format", "{{.Id}}", image).stdout.strip()
        if re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None:
            raise LabError("Built crawler network image identity is invalid.")
        try:
            fixture = create_public_network(network, f"{LABEL}={run_id}")
        except LabNetworkError:
            raise LabError("Crawler fixture network isolation failed.") from None
        docker(
            "container",
            "create",
            "--name",
            server,
            "--network",
            network,
            "--ip",
            fixture.address,
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=1m,mode=1777",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "64",
            "--memory",
            "128m",
            "--cpus",
            "0.5",
            "--sysctl",
            "net.ipv4.ip_unprivileged_port_start=0",
            "--label",
            f"{LABEL}={run_id}",
            image,
            "/opt/signal/crawler-network-server.py",
        )
        docker("container", "start", server)
        wait_for_server(server)
        yield CrawlerNetwork(run_id, image, image_id, network, fixture)
    finally:
        cleanup(run_id, network, image)


def require_free_space(directory: Path, minimum: int = 2 * 1024**3) -> None:
    if shutil.disk_usage(directory).free < minimum:
        raise LabError("At least 2 GiB free is required before running the crawler network lab.")


def docker(*args: str, timeout: int = 180, check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["docker", *args],
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise LabError(f"Docker command failed ({type(error).__name__}).") from None


def source_hashes() -> dict[str, str]:
    files = [
        ROOT / ".dockerignore",
        ROOT / ".github" / "workflows" / "quality.yml",
        DOCKERFILE,
        ROOT / "deploy" / "crawler-network" / "server.py",
        ROOT / "pyproject.toml",
        ROOT / "requirements.txt",
        Path(__file__),
        ROOT / "scripts/lab_runtime.py",
        ROOT / "scripts/lab_network.py",
        ROOT / "scripts" / "run-crawler-network-tests.py",
        ROOT / "services" / "control_plane" / "src" / "signal_core" / "__init__.py",
        ROOT / "services" / "control_plane" / "src" / "signal_core" / "crawl_http.py",
        ROOT / "services" / "control_plane" / "src" / "signal_core" / "crawl_urls.py",
        ROOT / "tests" / "repository" / "crawler-network.test.mjs",
        ROOT / "tests" / "tooling" / "test_crawler_network_lab.py",
    ]
    files.extend((ROOT / "tests" / "crawler").glob("*.py"))
    files.extend((ROOT / "tests" / "tooling").glob("test_crawl_*.py"))
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(files))
    }


def summarize_junit(filename: Path) -> dict[str, object]:
    cases = ET.parse(filename).getroot().findall(".//testcase")
    if not cases:
        raise LabError("Empty crawler network test report.")
    tests = []
    for case in cases:
        failed = any(case.find(tag) is not None for tag in ["failure", "error"])
        status = "FAIL" if failed else "PASS"
        if case.find("skipped") is not None:
            status = "SKIP"
        tests.append({"name": case.attrib["name"], "status": status})
    return {"tests": tests, "passed": sum(test["status"] == "PASS" for test in tests)}


def cleanup(run_id: str, network: str, image: str) -> None:
    failures = []
    try:
        names = docker(
            "container",
            "ls",
            "--all",
            "--filter",
            f"label={LABEL}={run_id}",
            "--format",
            "{{.Names}}",
        ).stdout.splitlines()
        for name in names:
            if name.startswith(f"signal-crawler-network-{run_id}"):
                docker("container", "rm", "--force", "--volumes", name)
            else:
                failures.append("unexpected labeled container")
    except LabError:
        failures.append("container cleanup failed")
    try:
        networks = docker(
            "network",
            "ls",
            "--filter",
            f"label={LABEL}={run_id}",
            "--format",
            "{{.Name}}",
        ).stdout.splitlines()
        if network in networks:
            docker("network", "rm", network)
        if any(name != network for name in networks):
            failures.append("unexpected labeled network")
    except LabError:
        failures.append("network cleanup failed")
    try:
        if docker("image", "inspect", image, check=False).returncode == 0:
            docker("image", "rm", "--force", image)
        if docker("image", "inspect", image, check=False).returncode == 0:
            failures.append("image cleanup incomplete")
    except LabError:
        failures.append("image cleanup failed")
    if failures:
        raise LabError("Crawler network lab cleanup could not be confirmed.")


def wait_for_server(name: str) -> None:
    deadline = time.monotonic() + 20
    program = "import socket; socket.create_connection(('127.0.0.1',80),1).close()"
    while time.monotonic() < deadline:
        result = docker("exec", name, "python", "-c", program, check=False, timeout=3)
        if result.returncode == 0:
            return
        state = docker("container", "inspect", "--format", "{{.State.Running}}", name, check=False)
        if state.returncode == 0 and state.stdout.strip() == "false":
            break
        time.sleep(0.2)
    raise LabError("Synthetic crawler origin did not become ready.")


def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    report_path = RUNTIME / "latest.xml"
    result: subprocess.CompletedProcess | None = None
    cleanup_complete = False
    with isolated_crawler_network() as environment:
        env = dict(
            os.environ,
            SIGNAL_CRAWLER_NETWORK_LAB="1",
            SIGNAL_CRAWLER_NETWORK_IMAGE=environment.image,
            SIGNAL_CRAWLER_NETWORK_NAME=environment.network,
            SIGNAL_CRAWLER_NETWORK_RUN_ID=environment.run_id,
            SIGNAL_CRAWLER_NETWORK_ADDRESS=environment.fixture.address,
            SIGNAL_CRAWLER_NETWORK_UNSERVED_ADDRESS=environment.fixture.unserved_address,
            SIGNAL_CRAWLER_NETWORK_SUBNET=environment.fixture.subnet,
            SIGNAL_CRAWLER_NETWORK_GATEWAY=environment.fixture.gateway,
            PYTHONDONTWRITEBYTECODE="1",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/crawler",
                "-v",
                f"--junitxml={report_path}",
            ],
            cwd=ROOT,
            env=env,
            timeout=120,
        )
    cleanup_complete = True

    after = source_hashes()
    if before != after:
        raise LabError("Source changed during crawler qualification; rerun against stable source.")
    if result is None or environment is None:
        raise LabError("Crawler network tests did not run.")
    docker_version = docker("version", "--format", "{{.Server.Version}}", timeout=30).stdout.strip()
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "docker_server": docker_version,
        "base_image": BASE_IMAGE,
        "local_image_id": environment.image_id,
        "network": {
            "internal": True,
            "ip_masquerade": False,
            "subnet": environment.fixture.subnet,
            "gateway": environment.fixture.gateway,
            "address": environment.fixture.address,
        },
        "source_sha256": after,
        "production_authority": False,
        "cleanup": "completed" if cleanup_complete else "unconfirmed",
        **summarize_junit(report_path),
    }
    (RUNTIME / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(report["passed"] != len(report["tests"]))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LabError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
