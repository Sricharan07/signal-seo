"""Joint PostgreSQL and isolated-network qualification for crawl page attempts."""

import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from crawler_network_lab import BASE_IMAGE, isolated_crawler_network
from database_lab import IMAGE as DATABASE_IMAGE
from database_lab import isolated_postgres, provision
from lab_runtime import runtime_root

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = runtime_root(ROOT) / "page-attempt-tests"


class LabError(RuntimeError):
    """Failure safe to print without subprocess arguments or credentials."""


def source_hashes() -> dict[str, str]:
    paths = [
        ROOT / ".github/workflows/quality.yml",
        ROOT / "database/migrations/versions/0024_crawl_page_attempts.py",
        ROOT / "database/migrations/versions/0024_crawl_page_attempts.sql",
        ROOT / "deploy/crawler-network/Dockerfile",
        ROOT / "deploy/crawler-network/server.py",
        ROOT / "scripts/crawler_network_lab.py",
        ROOT / "scripts/database_lab.py",
        ROOT / "scripts/lab_runtime.py",
        ROOT / "scripts/lab_network.py",
        Path(__file__),
        ROOT / "scripts/run-page-attempt-tests.py",
        ROOT / "services/control_plane/src/signal_core/crawl_admission.py",
        ROOT / "services/control_plane/src/signal_core/crawl_artifacts.py",
        ROOT / "services/control_plane/src/signal_core/crawl_frontier.py",
        ROOT / "services/control_plane/src/signal_core/crawl_http.py",
        ROOT / "services/control_plane/src/signal_core/crawl_page.py",
        ROOT / "services/control_plane/src/signal_core/crawl_robots.py",
        ROOT / "services/control_plane/src/signal_core/crawl_urls.py",
    ]
    paths.extend((ROOT / "tests/page_attempt").glob("*.py"))
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
    }


def summarize_junit(filename: Path) -> dict[str, object]:
    cases = ET.parse(filename).getroot().findall(".//testcase")
    if not cases:
        raise LabError("Empty page-attempt qualification report.")
    tests = []
    for case in cases:
        failed = any(case.find(tag) is not None for tag in ["failure", "error"])
        status = "FAIL" if failed else "PASS"
        if case.find("skipped") is not None:
            status = "SKIP"
        tests.append({"name": case.attrib["name"], "status": status})
    return {"tests": tests, "passed": sum(test["status"] == "PASS" for test in tests)}


def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    report_path = RUNTIME / "latest.xml"
    result: subprocess.CompletedProcess | None = None
    database_version: str | None = None
    crawler = None
    with isolated_postgres(minimum_free=2 * 1024**3) as (admin_dsn, common):
        env, database_version = provision(admin_dsn, common)
        migrate = [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"]
        for _ in range(2):
            subprocess.run(migrate, env=env, cwd=ROOT, check=True, timeout=180)
        with isolated_crawler_network() as crawler:
            qualified_env = dict(
                env,
                SIGNAL_PAGE_ATTEMPT_LAB="1",
                SIGNAL_CRAWLER_NETWORK_IMAGE=crawler.image,
                SIGNAL_CRAWLER_NETWORK_NAME=crawler.network,
                SIGNAL_CRAWLER_NETWORK_RUN_ID=crawler.run_id,
                SIGNAL_CRAWLER_NETWORK_ADDRESS=crawler.fixture.address,
                PYTHONDONTWRITEBYTECODE="1",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/page_attempt",
                    "-v",
                    f"--junitxml={report_path}",
                ],
                cwd=ROOT,
                env=qualified_env,
                timeout=180,
            )

    after = source_hashes()
    if before != after:
        raise LabError("Source changed during page-attempt qualification; rerun stable source.")
    if result is None or database_version is None or crawler is None:
        raise LabError("Page-attempt qualification did not run.")
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "database": database_version,
        "database_image": DATABASE_IMAGE,
        "crawler_base_image": BASE_IMAGE,
        "crawler_image_id": crawler.image_id,
        "network": {
            "internal": True,
            "ip_masquerade": False,
            "subnet": crawler.fixture.subnet,
            "gateway": crawler.fixture.gateway,
            "address": crawler.fixture.address,
        },
        "source_sha256": after,
        "production_authority": False,
        "cleanup": "completed",
        **summarize_junit(report_path),
    }
    (RUNTIME / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(report["passed"] != len(report["tests"]))
