"""Qualify invitation foundations and owner product authority on disposable PostgreSQL."""

import json
import subprocess
import sys
from datetime import UTC, datetime

from database_lab import ROOT, isolated_postgres, provision, source_hashes, summarize_junit
from lab_runtime import runtime_root


def main() -> int:
    hashes = source_hashes()
    directory = runtime_root(ROOT) / "team-invitation-tests"
    directory.mkdir(parents=True, exist_ok=True)
    with isolated_postgres() as (admin, common):
        env, version = provision(admin, common)
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
                "tests/control_plane/test_team_invitations.py",
                "tests/control_plane/test_invitation_revocation.py",
                "tests/control_plane/test_invitations.py",
                "tests/control_plane/test_invitation_acceptance.py",
                "tests/control_plane/test_invitation_identity_proofs.py",
                "tests/control_plane/test_login_flow.py",
                "-q",
                f"--junitxml={directory / 'latest.xml'}",
            ],
            cwd=ROOT,
            env=env,
            timeout=300,
        )
    if source_hashes() != hashes:
        raise RuntimeError("Source changed during invitation qualification.")
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "database": version,
        "source_sha256": hashes,
        "production_authority": False,
        "cleanup": "completed",
        **summarize_junit(directory / "latest.xml"),
    }
    (directory / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(report["passed"] != len(report["tests"]))


if __name__ == "__main__":
    sys.exit(main())
