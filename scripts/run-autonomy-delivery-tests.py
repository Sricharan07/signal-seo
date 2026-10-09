#!/usr/bin/env python3
"""Qualify weekly delivery on disposable PostgreSQL and real Temporal."""

import argparse
import hashlib
import json
import signal
import subprocess
import sys
from datetime import UTC, datetime

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


def delivery_source_hashes():
    hashes = source_hashes()
    files = [ROOT / "scripts/run-autonomy-delivery-tests.py"]
    files.extend((ROOT / "tests/delivery").glob("*.py"))
    for path in files:
        hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, choices=(0, 1))
    args = parser.parse_args()
    directory = runtime_root(ROOT) / "autonomy-delivery"
    directory.mkdir(parents=True, exist_ok=True)
    # A fresh checkout (CI or a clean worktree) has no Temporal SDK cache yet.
    temporal_cache = runtime_root(ROOT) / "temporal-tests/sdk-cache"
    temporal_cache.mkdir(parents=True, exist_ok=True)
    hashes = delivery_source_hashes()
    with (
        isolated_postgres() as (admin_dsn, common),
        isolated_postgres() as (journal_dsn, journal_common),
    ):
        env, _ = provision(admin_dsn, common)
        env.pop("SIGNAL_DELIVERY_TEST_SHARD", None)
        if args.shard is not None:
            env["SIGNAL_DELIVERY_TEST_SHARD"] = str(args.shard)
        writer, _ = _journal_credentials(journal_dsn, journal_common)
        env.update(
            SIGNAL_TEST_WRITE_JOURNAL_DSN=writer,
            SIGNAL_TEMPORAL_LAB="1",
            SIGNAL_TEMPORAL_DOWNLOAD_DIR=str(temporal_cache),
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
                "tests/delivery",
                "-v",
                f"--junitxml={directory / 'latest.xml'}",
            ],
            cwd=ROOT,
            env=env,
            # The unsharded suite now qualifies both technical and editorial delivery.
            timeout=3600,
        )
    if hashes != delivery_source_hashes():
        raise LabError("Delivery source changed during qualification; rerun the lab.")
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "source_sha256": hashes,
        "production_authority": False,
        "full_suite": args.shard is None,
        "shard": args.shard,
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
    except KeyboardInterrupt:
        print("Weekly delivery lab interrupted; owned cleanup attempted.", file=sys.stderr)
        raise SystemExit(130) from None
    except (LabError, OSError, subprocess.SubprocessError, psycopg.Error):
        print("Weekly delivery lab unavailable; no production resources used.", file=sys.stderr)
        raise SystemExit(1) from None
