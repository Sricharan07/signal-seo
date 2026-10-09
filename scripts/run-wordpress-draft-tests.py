#!/usr/bin/env python3
"""Bounded WordPress draft qualification, without starting Temporal."""

import hashlib
import json
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from authority_journal_lab import _journal_credentials
from database_lab import (
    ROOT,
    LabError,
    isolated_postgres,
    provision,
    source_hashes,
    summarize_junit,
)
from lab_runtime import runtime_root


def hashes():
    result = source_hashes()
    for path in (ROOT / "tests/delivery/test_wordpress_drafts.py", Path(__file__)):
        result[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main():
    directory = runtime_root(ROOT) / "wordpress-drafts"
    directory.mkdir(parents=True, exist_ok=True)
    before = hashes()
    with (
        isolated_postgres() as (primary, common),
        isolated_postgres() as (journal, journal_common),
    ):
        env, _ = provision(primary, common)
        writer, _ = _journal_credentials(journal, journal_common)
        env["SIGNAL_TEST_WRITE_JOURNAL_DSN"] = writer
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
                "tests/delivery/test_wordpress_drafts.py",
                "-q",
                f"--junitxml={directory / 'latest.xml'}",
            ],
            cwd=ROOT,
            env=env,
            timeout=900,
        )
    if before != hashes():
        raise LabError("WordPress source changed during qualification; rerun.")
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "source_sha256": before,
        "production_authority": False,
        "live_wordpress": "NOT_EXECUTED",
        "cleanup": "completed",
        **summarize_junit(directory / "latest.xml"),
    }
    (directory / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(report["passed"] != len(report["tests"]))


if __name__ == "__main__":

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, LabError, OSError, subprocess.SubprocessError, psycopg.Error):
        print("WordPress draft lab unavailable; owned cleanup attempted.", file=sys.stderr)
        raise SystemExit(1) from None
