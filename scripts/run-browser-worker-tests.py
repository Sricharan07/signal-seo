"""Real Docker/PostgreSQL browser lab with invocation-owned cache and cleanup."""

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from database_lab import LabError, isolated_postgres, provision, summarize_junit
from lab_runtime import runtime_root

ROOT = Path(__file__).resolve().parents[1]
REPORT = runtime_root(ROOT) / "browser-worker"


def hashes() -> dict[str, str]:
    files = [
        Path(__file__),
        ROOT / ".dockerignore",
        ROOT / "scripts/lab_network.py",
        ROOT / "tests/tooling/browser_fixture_reproduction.py",
        ROOT / "services/control_plane/src/signal_core/egress_profiles.py",
        ROOT / "services/control_plane/src/signal_core/shared_egress.py",
    ]
    for pattern in (
        "services/control_plane/src/signal_core/browser*.py",
        "database/migrations/versions/0072_browser_worker.*",
        "deploy/browser-worker/*",
        "tests/browser/*.py",
    ):
        files.extend(ROOT.glob(pattern))
    return {
        str(file.relative_to(ROOT)): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(set(files))
        if file.is_file()
    }


def docker(*args: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=timeout
    )


def main() -> int:
    before = hashes()
    REPORT.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex
    builder = "signal-browser-build-" + run_id
    image = "signal-browser-lab:" + run_id
    try:
        docker("buildx", "create", "--driver", "docker-container", "--name", builder)
        build = docker(
            "buildx",
            "build",
            "--builder",
            builder,
            "--load",
            "--file",
            "deploy/browser-worker/Dockerfile",
            "--tag",
            image,
            ".",
            check=False,
            timeout=900,
        )
        if build.returncode:
            print(build.stderr[-8000:], file=sys.stderr)
            raise LabError("Browser image build failed.")
        image_id = docker("image", "inspect", "--format", "{{.Id}}", image).stdout.strip()
        image_size = int(docker("image", "inspect", "--format", "{{.Size}}", image).stdout)
        with isolated_postgres() as (admin_dsn, common):
            env, version = provision(admin_dsn, common)
            env.update(
                SIGNAL_BROWSER_WORKER_LAB="1",
                SIGNAL_BROWSER_WORKER_IMAGE=image_id,
                PYTHONDONTWRITEBYTECODE="1",
            )
            subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
                cwd=ROOT,
                env=env,
                check=True,
                timeout=180,
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/browser",
                    "-v",
                    f"--junitxml={REPORT / 'latest.xml'}",
                ],
                cwd=ROOT,
                env=env,
                timeout=600,
            )
        report = {
            "schema_version": 1,
            "recorded_at": datetime.now(UTC).isoformat(),
            "database": version,
            "image_id": image_id,
            "image_bytes": image_size,
            "live_jev": "NOT_EXECUTED",
            "production_authority": False,
            **summarize_junit(REPORT / "latest.xml"),
        }
    finally:
        # The dedicated builder owns its entire cache; no shared prune is ever used.
        docker("buildx", "rm", "--force", builder, check=False)
        docker("image", "rm", image, check=False)
        if docker("image", "inspect", image, check=False).returncode == 0:
            raise LabError("Browser lab image cleanup unconfirmed.")
        if builder in docker("buildx", "ls").stdout:
            raise LabError("Browser lab cache cleanup unconfirmed.")
    report["cleanup"] = "completed"
    if before != hashes():
        raise LabError("Browser source changed during qualification; rerun stable sources.")
    report["source_sha256"] = before
    (REPORT / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(report["passed"] != len(report["tests"]))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LabError, OSError, subprocess.SubprocessError):
        print("Browser lab failed; invocation-owned cleanup attempted.", file=sys.stderr)
        raise SystemExit(1) from None
