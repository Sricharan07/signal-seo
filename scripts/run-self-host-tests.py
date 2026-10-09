"""Self-host safety lab: fresh PostgreSQL and non-dev OpenBao, no owner input accepted."""

import json
import subprocess
import sys
from datetime import UTC, datetime

from database_lab import ROOT, isolated_postgres, provision, summarize_junit
from lab_runtime import runtime_root


def main():
    directory = runtime_root(ROOT) / "self-host-tests"
    directory.mkdir(parents=True, exist_ok=True)
    with isolated_postgres() as (dsn, common):
        env, version = provision(dsn, common)
        env["SIGNAL_SELF_HOST_LAB"] = "1"
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
                "tests/self_host",
                "-q",
                f"--junitxml={directory / 'latest.xml'}",
            ],
            cwd=ROOT,
            env=env,
            timeout=600,
        )
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "database": version,
        "production_authority": False,
        "cleanup": "completed",
        **summarize_junit(directory / "latest.xml"),
    }
    (directory / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(report["passed"] != len(report["tests"]))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Self-host lab failed; values suppressed. Invocation-owned cleanup attempted.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
