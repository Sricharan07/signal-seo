"""Qualify the pinned no-network candidate container with synthetic repositories."""

import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from lab_runtime import runtime_root

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from signal_core.candidate_build import NODE_IMAGE  # noqa: E402

REPORT = runtime_root(ROOT) / "candidate-sandbox"
SOURCES = (
    "scripts/run-candidate-sandbox-tests.py",
    "scripts/lab_runtime.py",
    "services/control_plane/src/signal_core/candidate_build.py",
    "services/control_plane/src/signal_core/candidate_sandbox.py",
    "tests/container/test_candidate_sandbox.py",
    "tests/container/conftest.py",
    "tests/tooling/test_candidate_build.py",
    "tests/tooling/astro_build_support.py",
    "tests/tooling/test_astro_build_boundary.py",
    "tests/container/test_astro_build_boundary.py",
    "services/control_plane/src/signal_core/astro_source.py",
    "services/control_plane/src/signal_core/npm_registry.py",
    "services/control_plane/src/signal_core/egress_profiles.py",
    "services/control_plane/src/signal_core/astro_recipes.py",
    "tests/tooling/astro_delivery_support.py",
    "tests/container/test_astro_delivery.py",
    "services/control_plane/src/signal_core/front_matter.py",
    "services/control_plane/src/signal_core/front_matter_recipes.py",
    "tests/tooling/front_matter_support.py",
    "tests/container/test_front_matter_delivery.py",
    "services/control_plane/src/signal_core/nextjs_recipes.py",
    "tests/tooling/nextjs_support.py",
    "tests/container/test_nextjs_delivery.py",
)


def _hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("CANDIDATE_DOCKER_LAB_FAILED") from None
    if result.returncode != 0:
        raise RuntimeError("CANDIDATE_DOCKER_LAB_FAILED")
    return result


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    before = _hashes()
    _docker("info", "--format", "{{.ServerVersion}}")
    _docker("pull", NODE_IMAGE)
    image_id = _docker("image", "inspect", "--format", "{{.Id}}", NODE_IMAGE).stdout.strip()
    run_id = uuid4().hex
    env = dict(os.environ, SIGNAL_CANDIDATE_SANDBOX_LAB="1", SIGNAL_CANDIDATE_SANDBOX_RUN_ID=run_id)
    env["PYTHONPATH"] = str(ROOT / "services/control_plane/src")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-v",
            "tests/container/test_candidate_sandbox.py",
            "tests/container/test_astro_build_boundary.py",
            "tests/container/test_astro_delivery.py",
            "tests/container/test_front_matter_delivery.py",
            "tests/container/test_nextjs_delivery.py",
            f"--junitxml={REPORT / 'latest.xml'}",
        ],
        cwd=ROOT,
        env=env,
        timeout=180,
    )
    leftovers = _docker(
        "container",
        "ls",
        "--all",
        "--filter",
        f"label=dev.signal.candidate-lab={run_id}",
        "--format",
        "{{.ID}}",
    ).stdout.strip()
    if leftovers:
        raise RuntimeError("CANDIDATE_SANDBOX_CLEANUP_UNCONFIRMED")
    if before != _hashes():
        raise RuntimeError("CANDIDATE_SANDBOX_SOURCE_CHANGED")
    cases = ET.parse(REPORT / "latest.xml").getroot().findall(".//testcase")
    if not cases:
        raise RuntimeError("CANDIDATE_SANDBOX_REPORT_EMPTY")
    passed = sum(
        case.find("failure") is None and case.find("error") is None and case.find("skipped") is None
        for case in cases
    )
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "image": NODE_IMAGE,
        "image_id": image_id,
        "source_sha256": before,
        "tests": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "cleanup": "completed",
        "production_authority": False,
    }
    (REPORT / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(passed != len(cases))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
