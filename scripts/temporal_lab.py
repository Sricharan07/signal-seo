"""Disposable Temporal lifecycle and reproducible integration-test evidence."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import temporalio
from lab_runtime import runtime_root

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = runtime_root(ROOT) / "temporal-tests"
DOWNLOAD = RUNTIME / "sdk-cache"


class LabError(RuntimeError):
    """Failure safe to print without command arguments or process output."""


def require_free_space(directory: Path, minimum: int = 512 * 1024**2) -> None:
    if shutil.disk_usage(directory).free < minimum:
        raise LabError("At least 512 MiB free is required before starting the Temporal lab.")


def source_hashes() -> dict[str, str]:
    files = [
        ROOT / "requirements.txt",
        ROOT / "pyproject.toml",
        ROOT / "scripts/run-temporal-tests.py",
        Path(__file__),
        ROOT / "scripts/lab_runtime.py",
    ]
    for directory in [
        "services/control_plane/src",
        "tests/temporal",
        "tests/tooling",
    ]:
        files.extend(
            path for path in (ROOT / directory).rglob("*.py") if "__pycache__" not in path.parts
        )
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(files))
    }


def summarize_junit(filename: Path) -> dict[str, object]:
    cases = ET.parse(filename).getroot().findall(".//testcase")
    if not cases:
        raise LabError("Empty Temporal integration-test report.")
    tests = []
    for case in cases:
        failed = any(case.find(tag) is not None for tag in ["failure", "error"])
        status = "FAIL" if failed else "PASS"
        if case.find("skipped") is not None:
            status = "SKIP"
        tests.append({"name": case.attrib["name"], "status": status})
    return {"tests": tests, "passed": sum(test["status"] == "PASS" for test in tests)}


def parse_temporal_versions(output: str) -> tuple[str, str, str]:
    match = re.fullmatch(
        r"temporal version ([0-9]+\.[0-9]+\.[0-9]+) "
        r"\(Server ([0-9]+\.[0-9]+\.[0-9]+), UI ([0-9]+\.[0-9]+\.[0-9]+)\)\n?",
        output,
    )
    if match is None:
        raise LabError("The Temporal development server reported an unexpected version.")
    return match.group(1), match.group(2), match.group(3)


def temporal_versions() -> tuple[str, str, str]:
    binaries = sorted(DOWNLOAD.glob("temporal-sdk-python-*"))
    if len(binaries) != 1 or not binaries[0].is_file():
        raise LabError("The Temporal SDK-managed development server binary is unavailable.")
    try:
        result = subprocess.run(
            [str(binaries[0]), "--version"],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise LabError("The Temporal development server version could not be verified.") from None
    return parse_temporal_versions(result.stdout)


def main() -> int:
    require_free_space(ROOT)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    DOWNLOAD.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    report_path = RUNTIME / "latest.xml"
    env = dict(
        os.environ,
        SIGNAL_TEMPORAL_LAB="1",
        SIGNAL_TEMPORAL_DOWNLOAD_DIR=str(DOWNLOAD),
        PYTHONDONTWRITEBYTECODE="1",
    )
    env.pop("SIGNAL_TEMPORAL_ADDRESS", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/temporal",
            "-v",
            f"--junitxml={report_path}",
        ],
        cwd=ROOT,
        env=env,
        timeout=180,
    )
    after = source_hashes()
    if before != after:
        raise LabError("Source changed during verification; rerun against a stable source tree.")
    cli_version, server_version, ui_version = temporal_versions()
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "temporal_sdk": temporalio.__version__,
        "temporal_cli": cli_version,
        "temporal_server": server_version,
        "temporal_ui": ui_version,
        "bind_address": "127.0.0.1",
        "persistence": "ephemeral_in_memory_sqlite",
        "source_sha256": after,
        "production_authority": False,
        "cleanup": "completed",
        **summarize_junit(report_path),
    }
    (RUNTIME / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(report["passed"] != len(report["tests"]))
