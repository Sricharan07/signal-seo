"""Focused slice 0141 checks on invocation-owned disposable PostgreSQL only."""

import subprocess
import sys

from database_lab import ROOT, isolated_postgres, provision


def main():
    with isolated_postgres() as (admin, common):
        env, _ = provision(admin, common)
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=180,
        )
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/control_plane/test_keyword_topics.py",
                "tests/control_plane/test_seo_strategy.py",
                "tests/control_plane/test_weekly_skills.py",
                "tests/control_plane/test_dataforseo.py",
                "-q",
                *sys.argv[1:],
            ],
            cwd=ROOT,
            env=env,
            timeout=600,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
