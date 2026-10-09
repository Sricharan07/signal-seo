"""Provider/strategy checks on invocation-owned disposable PostgreSQL only."""

import subprocess
import sys

from database_lab import ROOT, isolated_postgres, provision


def main():
    with isolated_postgres(minimum_free=4 * 1024**3) as (admin, common):
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
                "tests/control_plane/test_provider_data.py",
                "tests/control_plane/test_dataforseo.py",
                "tests/control_plane/test_bing_binding.py",
                "tests/control_plane/test_bing_pages.py",
                "tests/control_plane/test_seo_strategy.py",
                "tests/control_plane/test_keyword_topics.py",
                "tests/control_plane/test_weekly_skills.py",
                "-q",
                *sys.argv[1:],
            ],
            cwd=ROOT,
            env=env,
            timeout=600,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
