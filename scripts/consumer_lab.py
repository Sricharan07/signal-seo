"""Joint disposable PostgreSQL and Temporal workflow-consumer qualification."""

import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import temporalio
from database_lab import IMAGE, LabError, isolated_postgres, provision
from lab_runtime import runtime_root
from temporal_lab import parse_temporal_versions

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = runtime_root(ROOT) / "consumer-tests"
DOWNLOAD = RUNTIME / "sdk-cache"


def source_hashes() -> dict[str, str]:
    files = [
        ROOT / "requirements.txt",
        ROOT / "pyproject.toml",
        ROOT / "scripts/consumer_lab.py",
        ROOT / "scripts/database_lab.py",
        ROOT / "scripts/run-consumer-tests.py",
        ROOT / "scripts/temporal_lab.py",
        ROOT / "scripts/lab_runtime.py",
    ]
    for directory, suffixes in [
        ("database", {".ini", ".json", ".py", ".sql"}),
        ("services/control_plane/src", {".py"}),
        ("tests/consumer", {".py"}),
    ]:
        files.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.suffix in suffixes and "__pycache__" not in path.parts
        )
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(files))
    }


def summarize_junit(filename: Path) -> dict[str, object]:
    cases = ET.parse(filename).getroot().findall(".//testcase")
    if not cases:
        raise LabError("Empty workflow-consumer integration report.")
    tests = []
    for case in cases:
        failed = any(case.find(tag) is not None for tag in ["failure", "error"])
        status = "FAIL" if failed else "PASS"
        if case.find("skipped") is not None:
            status = "SKIP"
        tests.append({"name": case.attrib["name"], "status": status})
    return {"tests": tests, "passed": sum(test["status"] == "PASS" for test in tests)}


def temporal_versions() -> tuple[str, str, str]:
    binaries = sorted(DOWNLOAD.glob("temporal-sdk-python-*"))
    if len(binaries) != 1 or not binaries[0].is_file():
        raise LabError("The consumer lab Temporal binary is unavailable.")
    try:
        result = subprocess.run(
            [str(binaries[0]), "--version"],
            check=True,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise LabError("The consumer lab Temporal version could not be verified.") from None
    return parse_temporal_versions(result.stdout)


def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    DOWNLOAD.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    report_path = RUNTIME / "latest.xml"
    with isolated_postgres() as (admin_dsn, common):
        env, database_version = provision(admin_dsn, common)
        env.update(
            {
                "SIGNAL_CONSUMER_LAB": "1",
                "SIGNAL_TEMPORAL_DOWNLOAD_DIR": str(DOWNLOAD),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        env.pop("SIGNAL_TEMPORAL_ADDRESS", None)
        migrate = [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"]
        for _ in range(2):
            subprocess.run(migrate, env=env, cwd=ROOT, check=True, timeout=180)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/consumer",
                "-v",
                f"--junitxml={report_path}",
            ],
            env=env,
            cwd=ROOT,
            timeout=240,
        )
    after = source_hashes()
    if before != after:
        raise LabError("Source changed during verification; rerun against stable source.")
    cli_version, server_version, ui_version = temporal_versions()
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "database": database_version,
        "database_image": IMAGE,
        "temporal_sdk": temporalio.__version__,
        "temporal_cli": cli_version,
        "temporal_server": server_version,
        "temporal_ui": ui_version,
        "bind_address": "127.0.0.1",
        "persistence": "ephemeral_postgresql_and_temporal_sqlite",
        "source_sha256": after,
        "production_authority": False,
        "cleanup": "completed",
        **summarize_junit(report_path),
    }
    (RUNTIME / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(report["passed"] != len(report["tests"]))
