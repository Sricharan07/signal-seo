"""Build and qualify the workflow consumer OCI image with synthetic inputs."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from lab_runtime import runtime_root

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = runtime_root(ROOT) / "workflow-consumer-image"
DOCKERFILE = ROOT / "deploy" / "workflow-consumer" / "Dockerfile"
BASE_IMAGE = (
    "python:3.12.14-slim-bookworm@"
    "sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254"
)
LABEL = "dev.signal.image-lab"


class LabError(RuntimeError):
    """Failure safe to print without command arguments or provider output."""


def require_free_space(directory: Path, minimum: int = 2 * 1024**3) -> None:
    if shutil.disk_usage(directory).free < minimum:
        raise LabError("At least 2 GiB free is required before building the consumer image.")


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


def image_source_hashes() -> dict[str, str]:
    files = [
        ROOT / ".dockerignore",
        DOCKERFILE,
        ROOT / "deploy" / "workflow-consumer" / "requirements.txt",
        ROOT / "scripts" / "check-workflow-consumer-health.py",
        ROOT / "scripts" / "run-workflow-consumer.py",
    ]
    files.extend(
        path
        for path in (ROOT / "services" / "control_plane" / "src" / "signal_core").glob("*.py")
        if path.is_file()
    )
    return _hashes(files)


def verification_source_hashes() -> dict[str, str]:
    files = [
        ROOT / "pyproject.toml",
        ROOT / "requirements.txt",
        ROOT / "deploy" / "workflow-consumer" / "compose.yaml",
        ROOT / ".github" / "workflows" / "quality.yml",
        Path(__file__),
        ROOT / "scripts/lab_runtime.py",
        ROOT / "scripts" / "run-workflow-consumer-image-tests.py",
        ROOT / "tests" / "repository" / "workflow-consumer-packaging.test.mjs",
    ]
    files.extend((ROOT / "tests" / "container").glob("*.py"))
    files.extend((ROOT / "tests" / "tooling").glob("test_workflow_consumer_image_lab.py"))
    return {**image_source_hashes(), **_hashes(files)}


def release_identity(hashes: dict[str, str]) -> str:
    encoded = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def summarize_junit(filename: Path) -> dict[str, object]:
    cases = ET.parse(filename).getroot().findall(".//testcase")
    if not cases:
        raise LabError("Empty workflow consumer image report.")
    tests = []
    for case in cases:
        failed = any(case.find(tag) is not None for tag in ["failure", "error"])
        status = "FAIL" if failed else "PASS"
        if case.find("skipped") is not None:
            status = "SKIP"
        tests.append({"name": case.attrib["name"], "status": status})
    return {"tests": tests, "passed": sum(test["status"] == "PASS" for test in tests)}


def cleanup(run_id: str, image: str) -> None:
    errors = []
    try:
        found = docker(
            "container",
            "ls",
            "--all",
            "--filter",
            f"label={LABEL}={run_id}",
            "--format",
            "{{.Names}}",
        ).stdout.splitlines()
        for name in found:
            if name.startswith(f"signal-consumer-image-{run_id}"):
                docker("container", "rm", "--force", "--volumes", name)
            else:
                errors.append("unexpected labeled container")
    except LabError:
        errors.append("container cleanup failed")
    try:
        inspection = docker("image", "inspect", image, check=False)
        if inspection.returncode == 0:
            docker("image", "rm", "--force", image)
        remaining = docker(
            "image",
            "ls",
            "--quiet",
            "--filter",
            f"reference={image}",
        ).stdout.strip()
        if remaining:
            errors.append("image cleanup incomplete")
    except LabError:
        errors.append("image cleanup failed")
    if errors:
        raise LabError("Workflow consumer image cleanup could not be confirmed.")


def main() -> int:
    require_free_space(ROOT)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    before_image = image_source_hashes()
    before_all = verification_source_hashes()
    release = release_identity(before_image)
    run_id = uuid.uuid4().hex[:12]
    image = f"signal-workflow-consumer-lab:{run_id}"
    report_path = RUNTIME / "latest.xml"
    result: subprocess.CompletedProcess | None = None
    image_id: str | None = None
    cleanup_complete = False
    try:
        docker("info", "--format", "{{.ServerVersion}}", timeout=30)
        docker(
            "build",
            "--pull",
            "--file",
            str(DOCKERFILE.relative_to(ROOT)),
            "--tag",
            image,
            "--label",
            f"{LABEL}={run_id}",
            "--build-arg",
            f"SIGNAL_RELEASE={release}",
            ".",
            timeout=600,
        )
        image_id = docker("image", "inspect", "--format", "{{.Id}}", image).stdout.strip()
        if re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None:
            raise LabError("Built workflow consumer image identity is invalid.")
        env = dict(
            os.environ,
            SIGNAL_WORKFLOW_CONSUMER_IMAGE_LAB="1",
            SIGNAL_WORKFLOW_CONSUMER_IMAGE=image,
            SIGNAL_WORKFLOW_CONSUMER_IMAGE_RUN_ID=run_id,
            SIGNAL_WORKFLOW_CONSUMER_RELEASE=release,
            PYTHONDONTWRITEBYTECODE="1",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/container/test_workflow_consumer_image.py",
                "-v",
                f"--junitxml={report_path}",
            ],
            cwd=ROOT,
            env=env,
            timeout=180,
        )
    finally:
        cleanup(run_id, image)
        cleanup_complete = True

    after_image = image_source_hashes()
    after_all = verification_source_hashes()
    if before_image != after_image or before_all != after_all:
        raise LabError("Source changed during image qualification; rerun against stable source.")
    if result is None or image_id is None:
        raise LabError("Workflow consumer image tests did not run.")
    docker_version = docker("version", "--format", "{{.Server.Version}}", timeout=30).stdout.strip()
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "docker_server": docker_version,
        "base_image": BASE_IMAGE,
        "release": release,
        "local_image_id": image_id,
        "image_source_sha256": after_image,
        "verification_source_sha256": after_all,
        "production_authority": False,
        "cleanup": "completed" if cleanup_complete else "unconfirmed",
        **summarize_junit(report_path),
    }
    (RUNTIME / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(report["passed"] != len(report["tests"]))


def _hashes(files) -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(files))
    }
