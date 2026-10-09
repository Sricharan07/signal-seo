"""Disposable Docker composition: worker -> isolated proxy -> trusted policy pipe."""

import json
import os
import re
import selectors
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from uuid import uuid4

from signal_core.browser_policy import BrowserLimits, BrowserRejected, validate_action
from signal_core.crawl_urls import CrawlScopePolicy

LABEL = "dev.signal.browser-session"
STARTUP_SECONDS = 60


class BrowserUnavailable(RuntimeError):
    """Browser composition is absent, failed or incomplete, never simulated."""


def docker(*args: str) -> str:
    try:
        return subprocess.run(
            ["docker", *args], check=True, capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise BrowserUnavailable("BROWSER_DOCKER_UNAVAILABLE") from None


def read_line(process: subprocess.Popen, deadline: float, maximum: int) -> dict:
    data = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while b"\n" not in data:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise BrowserUnavailable("BROWSER_TIME_EXHAUSTED")
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk or len(data) + len(chunk) > maximum:
                raise BrowserUnavailable("BROWSER_PROTOCOL_UNAVAILABLE")
            data.extend(chunk)
    try:
        result = json.loads(data)
    except ValueError:
        raise BrowserUnavailable("BROWSER_PROTOCOL_UNAVAILABLE") from None
    if not isinstance(result, dict):
        raise BrowserUnavailable("BROWSER_PROTOCOL_UNAVAILABLE")
    return result


@dataclass(frozen=True)
class DockerBrowserSandbox:
    image_digest: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_digest) is None:
            raise ValueError("A pinned browser image digest is required.")

    def session(
        self, policy: CrawlScopePolicy, limits: BrowserLimits, forward: Callable[[dict], dict]
    ) -> "DockerBrowserSession":
        return DockerBrowserSession(self.image_digest, policy, limits, forward)


class DockerBrowserSession:
    def __init__(
        self,
        image: str,
        policy: CrawlScopePolicy,
        limits: BrowserLimits,
        forward: Callable[[dict], dict],
    ) -> None:
        self.image = image
        self.policy = policy
        self.limits = limits
        self.forward = forward
        self.run_id = uuid4().hex
        self.network = "signal-browser-" + self.run_id
        self.worker_name = self.network + "-worker"
        self.proxy_name = self.network + "-proxy"
        self.processes = []
        self.deadline = time.monotonic() + STARTUP_SECONDS
        self.pump = None
        self.pump_error = False

    def _create(self, name: str, module: str, *, alias: bool = False) -> subprocess.Popen:
        args = [
            "container",
            "create",
            "--interactive",
            "--name",
            name,
            "--network",
            self.network,
            "--read-only",
            "--user",
            "10001:10001",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=128m,mode=1777",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--label",
            f"{LABEL}={self.run_id}",
            "--init",
        ]
        if alias:
            args.extend(["--network-alias", "signal-egress"])
        docker(*args, self.image, module)
        process = subprocess.Popen(
            ["docker", "start", "--attach", "--interactive", name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.processes.append(process)
        return process

    def __enter__(self) -> "DockerBrowserSession":
        try:
            docker(
                "network",
                "create",
                "--internal",
                "--opt",
                "com.docker.network.bridge.enable_ip_masquerade=false",
                "--opt",
                "com.docker.network.bridge.gateway_mode_ipv4=isolated",
                "--label",
                f"{LABEL}={self.run_id}",
                self.network,
            )
            proxy = self._create(self.proxy_name, "signal_core.browser_proxy_runtime", alias=True)
            self.pump = threading.Thread(target=self._proxy_loop, args=(proxy,), daemon=True)
            self.pump.start()
            self.worker = self._create(self.worker_name, "signal_core.browser_worker_runtime")
            self._write(
                self.worker,
                {
                    "origins": list(self.policy.allowed_origins),
                    "user_agent": self.policy.user_agent,
                    "limits": asdict(self.limits),
                    "proxy_host": "signal-egress",
                },
            )
            if read_line(self.worker, self.deadline, 65536) != {"ready": True}:
                raise BrowserUnavailable("BROWSER_START_UNAVAILABLE")
            self.deadline = time.monotonic() + self.limits.seconds
            return self
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _write(process: subprocess.Popen, value: dict) -> None:
        try:
            process.stdin.write(json.dumps(value, ensure_ascii=True).encode() + b"\n")
            process.stdin.flush()
        except (OSError, ValueError):
            raise BrowserUnavailable("BROWSER_PROTOCOL_UNAVAILABLE") from None

    def _proxy_loop(self, process: subprocess.Popen) -> None:
        try:
            while time.monotonic() < self.deadline:
                request = read_line(process, self.deadline, 4096)
                self._write(process, self.forward(request))
        except Exception:
            self.pump_error = True

    def execute(self, action: dict) -> dict:
        validate_action(action)
        self._write(self.worker, action)
        result = read_line(self.worker, self.deadline, 8 * 1024 * 1024)
        if "error" in result:
            raise BrowserRejected(result["error"])
        if self.pump_error:
            raise BrowserUnavailable("BROWSER_PROXY_UNAVAILABLE")
        return result

    def close(self) -> None:
        failures = []
        for name in (self.worker_name, self.proxy_name):
            try:
                # Exact invocation names only; never inspect or stop another track.
                exists = (
                    subprocess.run(
                        ["docker", "inspect", name], capture_output=True, timeout=10
                    ).returncode
                    == 0
                )
                if exists:
                    owner = docker(
                        "inspect", "--format", '{{ index .Config.Labels "' + LABEL + '" }}', name
                    )
                    if owner != self.run_id:
                        raise BrowserUnavailable("BROWSER_CLEANUP_OWNER_MISMATCH")
                    docker("container", "rm", "--force", "--volumes", name)
            except (BrowserUnavailable, subprocess.SubprocessError):
                failures.append(name)
        for process in self.processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            for stream in (process.stdin, process.stdout):
                stream.close()
        if self.pump is not None:
            self.pump.join(timeout=35)
            if self.pump.is_alive():
                failures.append("policy pipe")
        try:
            exists = (
                subprocess.run(
                    ["docker", "network", "inspect", self.network], capture_output=True, timeout=10
                ).returncode
                == 0
            )
            if exists:
                docker("network", "rm", self.network)
        except (BrowserUnavailable, subprocess.SubprocessError):
            failures.append(self.network)
        if failures:
            raise BrowserUnavailable("BROWSER_CLEANUP_UNCONFIRMED")

    def __exit__(self, *args) -> None:
        self.close()
