"""Focused visibility/recipe qualification on invocation-owned PostgreSQL; no Temporal."""

import json
import subprocess
import sys
from datetime import UTC, datetime

from database_lab import ROOT, isolated_postgres, provision, source_hashes, summarize_junit
from lab_runtime import runtime_root


def main():
    hashes = source_hashes()
    directory = runtime_root(ROOT) / "ai-answers-tests"
    directory.mkdir(parents=True, exist_ok=True)
    with isolated_postgres() as (admin, common):
        env, version = provision(admin, common)
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            cwd=ROOT,
            check=True,
            timeout=180,
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/control_plane/test_owner_ai_questions.py",
                "tests/control_plane/test_ai_visibility.py",
                "tests/control_plane/test_visibility_agent.py",
                "tests/control_plane/test_visibility_schedule.py",
                "tests/control_plane/test_grounded_structured_sealing.py",
                "tests/control_plane/test_technical_recipes.py",
                "-q",
                f"--junitxml={directory / 'latest.xml'}",
            ],
            env=env,
            cwd=ROOT,
            timeout=600,
        )
    if hashes != source_hashes():
        raise RuntimeError("Source changed during AI answers qualification.")
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "database": version,
        "cleanup": "completed",
        "production_authority": False,
        **summarize_junit(directory / "latest.xml"),
    }
    (directory / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(report["passed"] != len(report["tests"]))


if __name__ == "__main__":
    sys.exit(main())
