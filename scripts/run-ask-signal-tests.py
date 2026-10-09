"""Focused qualification using only the existing invocation-owned database lab."""

import subprocess
import sys

from database_lab import ROOT, isolated_postgres, provision


def main():
    with isolated_postgres() as (dsn, common):
        env, _ = provision(dsn, common)
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            cwd=ROOT,
            check=True,
            timeout=180,
        )
        return subprocess.run(
            [sys.executable, "-m", "pytest", "tests/control_plane/test_assistant_store.py", "-q"],
            env=env,
            cwd=ROOT,
            timeout=600,
        ).returncode


if __name__ == "__main__":
    sys.exit(main())
