"""Qualify IndexNow on disposable primary and independent journal PostgreSQL."""

import json
import subprocess
import sys
from datetime import UTC, datetime

from authority_journal_lab import _journal_credentials
from database_lab import ROOT, isolated_postgres, provision, source_hashes, summarize_junit
from lab_runtime import runtime_root


def main() -> int:
    hashes = source_hashes()
    directory = runtime_root(ROOT) / "indexnow-tests"
    directory.mkdir(parents=True, exist_ok=True)
    with isolated_postgres() as (admin, common), isolated_postgres() as (journal, journal_common):
        env, version = provision(admin, common)
        writer, _ = _journal_credentials(journal, journal_common)
        env["SIGNAL_TEST_INDEXNOW_JOURNAL_DSN"] = writer
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
                "tests/control_plane/test_indexnow.py",
                "tests/control_plane/test_technical_recipes.py",
                "-v",
                f"--junitxml={directory / 'latest.xml'}",
            ],
            cwd=ROOT,
            env=env,
            timeout=600,
        )
    if source_hashes() != hashes:
        raise RuntimeError("Source changed during IndexNow qualification.")
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "database": version,
        "source_sha256": hashes,
        "production_authority": False,
        "cleanup": "completed",
        "live_indexnow": "NOT_EXECUTED",
        **summarize_junit(directory / "latest.xml"),
    }
    (directory / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(report["passed"] != len(report["tests"]))


if __name__ == "__main__":
    sys.exit(main())
