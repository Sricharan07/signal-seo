"""Disposable no-network, no-mount container for untrusted repository builds."""

import hashlib
import io
import json
import os
import re
import selectors
import subprocess
import tarfile
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from uuid import uuid4

from signal_core.candidate_build import NODE_IMAGE, CandidateBuildPlan, CandidatePolicyRejected
from signal_core.npm_registry import (
    MAX_CACHE_BYTES,
    MAX_EXPANDED_BYTES,
    locked_dependencies,
    verify_tarball,
)

_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_LABEL = "dev.signal.candidate-build"
_MAX_LOG_BYTES = 64 * 1024
_MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


class CandidateSandboxUnavailable(Exception):
    """The local sandbox could not establish or confirm its isolation boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CandidateSandboxOutcome:
    exit_class: str
    exit_code: int | None
    logs_sha256: str
    log_bytes: int
    artifacts: tuple[tuple[str, str, int], ...]
    lockfile_sha256: str | None = None
    built_pages: tuple[tuple[str, str, str | None, str | None], ...] = ()
    unavailable_reason: str | None = None
    built_html: tuple[tuple[str, str], ...] = ()


class DockerCandidateSandbox:
    """Run a bounded build without mounting a host path or exposing a socket."""

    def __init__(self, *, timeout_seconds: int = 30) -> None:
        if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
            raise ValueError("Candidate build timeout is invalid.")
        self.timeout_seconds = timeout_seconds

    def run(self, plan: CandidateBuildPlan) -> CandidateSandboxOutcome:
        if not isinstance(plan, CandidateBuildPlan) or plan.toolchain != NODE_IMAGE:
            raise CandidatePolicyRejected("CANDIDATE_TOOLCHAIN_UNAVAILABLE")
        archive = _source_tar(plan.files)
        manifest = plan.dependency_manifest
        dependency_archive = None
        if manifest is not None:
            source = dict(plan.files)
            lock = source.get("npm-shrinkwrap.json", source.get("package-lock.json"))
            if locked_dependencies(source["package.json"], lock) != manifest:
                raise CandidatePolicyRejected("NPM_LOCKFILE_REJECTED")
            selected = {
                hashlib.sha256(item.integrity.encode("ascii")).hexdigest() + ".tgz": item
                for item in manifest.tarballs
            }
            dependencies = dict(plan.dependencies)
            if len(dependencies) != len(plan.dependencies) or set(dependencies) != set(selected):
                raise CandidatePolicyRejected("NPM_DEPENDENCY_CACHE_INCOMPLETE")
            if sum(len(content) for content in dependencies.values()) > MAX_CACHE_BYTES:
                raise CandidatePolicyRejected("NPM_DEPENDENCY_BUDGET_EXCEEDED")
            if (
                sum(verify_tarball(selected[key], content) for key, content in dependencies.items())
                > MAX_EXPANDED_BYTES
            ):
                raise CandidatePolicyRejected("NPM_DEPENDENCY_BUDGET_EXCEEDED")
            dependency_archive = _source_tar(
                tuple(
                    (".signal-dependencies/" + key, content) for key, content in plan.dependencies
                ),
                maximum_bytes=MAX_CACHE_BYTES + 1024 * 1024,
            )
        _docker("image", "inspect", NODE_IMAGE, timeout=10)
        name = f"signal-candidate-{uuid4().hex}"
        container_id = None
        try:
            created = _docker(*_create_args(name, dependencies=manifest is not None), timeout=15)
            container_id = created.stdout.decode("ascii", "ignore").strip()
            if _CONTAINER_ID.fullmatch(container_id) is None:
                raise CandidateSandboxUnavailable("CANDIDATE_SANDBOX_CREATE_FAILED")
            _docker("container", "start", container_id, timeout=10)
            loaded = _command(
                ["docker", "exec", "-i", container_id, "/bin/tar", "-x", "-C", "/workspace"],
                input_bytes=archive,
                timeout=10,
            )
            if loaded.returncode != 0:
                raise CandidateSandboxUnavailable("CANDIDATE_CHECKOUT_TRANSFER_FAILED")
            npm_flags = (
                "--ignore-scripts",
                "--offline",
                "--no-audit",
                "--no-fund",
                "--cache=/workspace/.signal-npm-cache",
                "--userconfig=/dev/null",
                "--globalconfig=/tmp/signal-global-npmrc",
            )
            combined = bytearray()
            if dependency_archive is not None:
                loaded = _command(
                    ["docker", "exec", "-i", container_id, "/bin/tar", "-x", "-C", "/workspace"],
                    input_bytes=dependency_archive,
                    timeout=15,
                )
                if loaded.returncode != 0:
                    raise CandidateSandboxUnavailable("CANDIDATE_CHECKOUT_TRANSFER_FAILED")
                deadline = time.monotonic() + self.timeout_seconds
                stages = [
                    (
                        "npm",
                        "cache",
                        "add",
                        *("/workspace/.signal-dependencies/" + key for key, _ in plan.dependencies),
                        *npm_flags,
                    ),
                    ("npm", "ci", *npm_flags),
                ]
                for stage in stages:
                    remaining = max(1, int(deadline - time.monotonic()))
                    code, out, errors, forced = _bounded_exec(
                        ["docker", "exec", "--workdir", "/workspace", container_id, *stage],
                        timeout=remaining,
                        output_limit=_MAX_LOG_BYTES - len(combined),
                    )
                    combined.extend(out + errors)
                    if forced is not None or code != 0 or time.monotonic() >= deadline:
                        return CandidateSandboxOutcome(
                            forced or ("timeout" if time.monotonic() >= deadline else "crash"),
                            code if forced is None else None,
                            hashlib.sha256(combined).hexdigest(),
                            len(combined),
                            (),
                            manifest.lockfile_sha256,
                            (),
                            "NPM_OFFLINE_INSTALL_UNAVAILABLE_LIFECYCLE_SCRIPTS_DISABLED",
                        )
            exit_code, stdout, stderr, forced = _bounded_exec(
                [
                    "docker",
                    "exec",
                    "--workdir",
                    "/workspace",
                    container_id,
                    *plan.command,
                    *(npm_flags if manifest else ()),
                ],
                timeout=self.timeout_seconds,
                output_limit=_MAX_LOG_BYTES - len(combined),
            )
            logs = bytes(combined) + stdout + b"\0" + stderr
            log_bytes = len(combined) + len(stdout) + len(stderr)
            nextjs = (
                manifest is not None
                and plan.artifact_root == "out"
                and json.loads(dict(plan.files)["package.json"]).get("scripts", {}).get("build")
                == "next build"
            )
            if forced is not None:
                return CandidateSandboxOutcome(
                    forced,
                    None,
                    hashlib.sha256(logs).hexdigest(),
                    log_bytes,
                    (),
                    manifest.lockfile_sha256 if manifest else None,
                    (),
                    "NEXT_OFFLINE_BUILD_LIMIT_EXCEEDED"
                    if nextjs
                    else "ASTRO_BUILD_LIMIT_EXCEEDED"
                    if manifest
                    else None,
                )
            if exit_code != 0:
                exit_class = "oom" if exit_code == 137 else "crash"
                return CandidateSandboxOutcome(
                    exit_class,
                    exit_code,
                    hashlib.sha256(logs).hexdigest(),
                    log_bytes,
                    (),
                    manifest.lockfile_sha256 if manifest else None,
                    (),
                    (
                        "NEXT_BUILD_NETWORK_UNAVAILABLE"
                        if nextjs
                        and any(
                            marker in stdout + stderr
                            for marker in (
                                b"ENETUNREACH",
                                b"ENOTFOUND",
                                b"EAI_AGAIN",
                                b"ECONNREFUSED",
                                b"Failed to fetch",
                                b"next/font/google",
                            )
                        )
                        else "NEXT_OFFLINE_BUILD_UNAVAILABLE_NETWORK_DISABLED"
                        if nextjs
                        else "ASTRO_BUILD_UNAVAILABLE_LIFECYCLE_SCRIPTS_DISABLED"
                        if manifest
                        else None
                    ),
                )
            tree_code, tree, tree_errors, tree_forced = _bounded_exec(
                [
                    "docker",
                    "exec",
                    container_id,
                    "/bin/tar",
                    "-C",
                    "/workspace",
                    *(
                        (
                            "--exclude=./node_modules",
                            "--exclude=./.signal-dependencies",
                            "--exclude=./.signal-npm-cache",
                            "--exclude=./.astro",
                        )
                        if manifest
                        else ()
                    ),
                    "-cf",
                    "-",
                    ".",
                ],
                timeout=10,
                output_limit=_MAX_ARCHIVE_BYTES,
            )
            if tree_forced is not None or tree_code != 0 or tree_errors:
                raise CandidateSandboxUnavailable("CANDIDATE_ARTIFACT_READ_FAILED")
            try:
                artifacts = _inspect_output(tree, plan)
                pages = _built_pages(tree, artifacts) if manifest else ()
                html = _built_html(tree, artifacts) if manifest else ()
            except CandidatePolicyRejected:
                return CandidateSandboxOutcome(
                    "policy_rejected",
                    None,
                    hashlib.sha256(logs).hexdigest(),
                    log_bytes,
                    (),
                    manifest.lockfile_sha256 if manifest else None,
                    (),
                    "NEXT_BUILT_OUTPUT_REJECTED"
                    if nextjs
                    else "ASTRO_BUILT_OUTPUT_REJECTED"
                    if manifest
                    else None,
                )
            return CandidateSandboxOutcome(
                "passed",
                0,
                hashlib.sha256(logs).hexdigest(),
                log_bytes,
                artifacts,
                manifest.lockfile_sha256 if manifest else None,
                pages,
                built_html=html,
            )
        finally:
            if container_id is not None:
                removed = _command(
                    ["docker", "container", "rm", "--force", "--volumes", container_id],
                    timeout=15,
                    check=False,
                )
                if removed.returncode != 0:
                    raise CandidateSandboxUnavailable("CANDIDATE_SANDBOX_CLEANUP_UNCONFIRMED")


def _create_args(name: str, *, dependencies: bool = False) -> tuple[str, ...]:
    return (
        "container",
        "create",
        "--pull=never",
        "--name",
        name,
        "--label",
        f"{_LABEL}=1",
        "--network",
        "none",
        "--read-only",
        "--user",
        "10001:10001",
        "--workdir",
        "/workspace",
        "--tmpfs",
        f"/workspace:rw,nosuid,nodev,{('exec,' if dependencies else '')}"
        f"size={'512m' if dependencies else '32m'},mode=1777",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=4m,mode=1777",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "32",
        "--memory",
        "768m" if dependencies else "256m",
        "--memory-swap",
        "768m" if dependencies else "256m",
        "--cpus",
        "0.5",
        "--ulimit",
        "nofile=64:64",
        "--ulimit",
        "fsize=33554432:33554432",
        "--env",
        "HOME=/tmp",
        "--env",
        "CI=1",
        "--env",
        "NEXT_TELEMETRY_DISABLED=1",
        "--env",
        "npm_config_offline=true",
        "--env",
        "npm_config_audit=false",
        "--env",
        "npm_config_ignore_scripts=true",
        "--env",
        f"NODE_OPTIONS=--max-old-space-size={'256' if dependencies else '128'}",
        "--entrypoint",
        "/bin/sleep",
        NODE_IMAGE,
        "infinity",
    )


def _source_tar(
    files: tuple[tuple[str, bytes], ...], *, maximum_bytes: int = 10 * 1024 * 1024
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        directories = set()
        for path, content in files:
            parts = path.split("/")
            for index in range(1, len(parts)):
                directory = "/".join(parts[:index])
                if directory not in directories:
                    entry = tarfile.TarInfo(directory)
                    entry.type = tarfile.DIRTYPE
                    entry.mode = 0o755
                    archive.addfile(entry)
                    directories.add(directory)
            entry = tarfile.TarInfo(path)
            entry.size = len(content)
            entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(content))
    if buffer.tell() > maximum_bytes:
        raise CandidatePolicyRejected("CANDIDATE_CHECKOUT_INVALID")
    return buffer.getvalue()


def _inspect_output(tree: bytes, plan: CandidateBuildPlan) -> tuple[tuple[str, str, int], ...]:
    expected = dict(plan.files)
    seen = set()
    artifacts = []
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(tree), mode="r:") as archive:
            members = archive.getmembers()
            if len(members) > 2000:
                raise CandidatePolicyRejected("CANDIDATE_OUTPUT_REJECTED")
            for member in members:
                path = member.name.removeprefix("./").removesuffix("/")
                if path in {"", "."}:
                    continue
                if (
                    path.startswith("/")
                    or "\\" in path
                    or ".." in path.split("/")
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                ):
                    raise CandidatePolicyRejected("CANDIDATE_OUTPUT_REJECTED")
                if member.isdir():
                    continue
                if not member.isfile() or path in seen or member.mode & 0o111:
                    raise CandidatePolicyRejected("CANDIDATE_OUTPUT_REJECTED")
                seen.add(path)
                content_file = archive.extractfile(member)
                if content_file is None:
                    raise CandidatePolicyRejected("CANDIDATE_OUTPUT_REJECTED")
                content = content_file.read(_MAX_ARTIFACT_BYTES + 1)
                if len(content) != member.size or len(content) > _MAX_ARTIFACT_BYTES:
                    raise CandidatePolicyRejected("CANDIDATE_OUTPUT_REJECTED")
                if path in expected:
                    if content != expected[path]:
                        raise CandidatePolicyRejected("CANDIDATE_SOURCE_MODIFIED")
                elif path.startswith(plan.artifact_root + "/"):
                    total += len(content)
                    if total > _MAX_ARTIFACT_BYTES or len(artifacts) >= 1000:
                        raise CandidatePolicyRejected("CANDIDATE_OUTPUT_REJECTED")
                    artifacts.append((path, hashlib.sha256(content).hexdigest(), len(content)))
                else:
                    raise CandidatePolicyRejected("CANDIDATE_OUTPUT_REJECTED")
    except (tarfile.TarError, OSError, EOFError):
        raise CandidatePolicyRejected("CANDIDATE_OUTPUT_REJECTED") from None
    if set(expected) - seen or not artifacts:
        raise CandidatePolicyRejected("CANDIDATE_OUTPUT_REJECTED")
    manifest = [{"path": path, "sha256": digest, "size": size} for path, digest, size in artifacts]
    if len(json.dumps(manifest, separators=(",", ":")).encode("utf-8")) > 128 * 1024:
        raise CandidatePolicyRejected("CANDIDATE_OUTPUT_REJECTED")
    return tuple(sorted(artifacts))


class _PageFacts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title, self.title, self.description = False, [], None

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            attributes = dict(attrs)
            if (attributes.get("name") or "").lower() == "description":
                self.description = attributes.get("content")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)


def _built_pages(tree, artifacts):
    pages = []
    expected = {path: digest for path, digest, _ in artifacts if path.endswith(".html")}
    with tarfile.open(fileobj=io.BytesIO(tree), mode="r:") as archive:
        for member in archive:
            path = member.name.removeprefix("./")
            if path not in expected:
                continue
            if member.size > 512 * 1024:
                raise CandidatePolicyRejected("ASTRO_BUILT_PAGE_LIMIT_EXCEEDED")
            try:
                content = archive.extractfile(member).read().decode("utf8")
                parser = _PageFacts()
                parser.feed(content)
            except (UnicodeError, ValueError):
                raise CandidatePolicyRejected("ASTRO_BUILT_PAGE_REJECTED") from None
            title = "".join(parser.title).strip() or None
            if any(
                value is not None and len(value.encode("utf8")) > 4096
                for value in (title, parser.description)
            ):
                raise CandidatePolicyRejected("ASTRO_BUILT_PAGE_REJECTED")
            pages.append((path, expected[path], title, parser.description))
    if not pages:
        raise CandidatePolicyRejected("ASTRO_BUILT_PAGES_UNAVAILABLE")
    return tuple(sorted(pages))


def _built_html(tree, artifacts):
    expected = {path for path, _, _ in artifacts if path.endswith(".html")}
    pages = []
    with tarfile.open(fileobj=io.BytesIO(tree), mode="r:") as archive:
        for member in archive:
            path = member.name.removeprefix("./")
            if path in expected:
                pages.append((path, archive.extractfile(member).read().decode("utf8")))
    return tuple(sorted(pages))


def _docker(*args: str, timeout: int) -> subprocess.CompletedProcess[bytes]:
    return _command(["docker", *args], timeout=timeout)


def _command(
    args: list[str], *, input_bytes: bytes | None = None, timeout: int, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            args, input=input_bytes, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        raise CandidateSandboxUnavailable("CANDIDATE_SANDBOX_UNAVAILABLE") from None
    if check and result.returncode != 0:
        raise CandidateSandboxUnavailable("CANDIDATE_SANDBOX_UNAVAILABLE")
    return result


def _bounded_exec(
    args: list[str], *, timeout: int, output_limit: int
) -> tuple[int | None, bytes, bytes, str | None]:
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        raise CandidateSandboxUnavailable("CANDIDATE_SANDBOX_UNAVAILABLE") from None
    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    deadline = time.monotonic() + timeout
    try:
        selector.register(process.stdout, selectors.EVENT_READ, stdout)
        selector.register(process.stderr, selectors.EVENT_READ, stderr)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, bytes(stdout), bytes(stderr), "timeout"
            for key, _ in selector.select(remaining):
                target = key.data
                chunk = os.read(key.fd, min(4096, output_limit + 1 - len(stdout) - len(stderr)))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                available = output_limit - len(stdout) - len(stderr)
                target.extend(chunk[:available])
                if len(chunk) > available:
                    return None, bytes(stdout), bytes(stderr), "output_limit"
        return process.wait(timeout=1), bytes(stdout), bytes(stderr), None
    except (OSError, subprocess.TimeoutExpired):
        raise CandidateSandboxUnavailable("CANDIDATE_SANDBOX_UNAVAILABLE") from None
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.communicate()
