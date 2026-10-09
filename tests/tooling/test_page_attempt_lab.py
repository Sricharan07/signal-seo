import json
from contextlib import nullcontext
from types import SimpleNamespace

import page_attempt_lab as lab
from lab_network import candidate_network


def test_joint_runner_migrates_uses_owned_network_records_evidence_and_cleanup(
    monkeypatch, tmp_path
):
    runtime = tmp_path / "runtime"
    hashes = {"source.py": "a" * 64}
    crawler = SimpleNamespace(
        run_id="run",
        image="crawler:run",
        image_id="sha256:" + "b" * 64,
        network="crawler-network-run",
        fixture=candidate_network(),
    )
    postgres_options = {}

    def isolated_postgres(**kwargs):
        postgres_options.update(kwargs)
        return nullcontext(("admin-dsn", {}))

    monkeypatch.setattr(lab, "ROOT", tmp_path)
    monkeypatch.setattr(lab, "RUNTIME", runtime)
    monkeypatch.setattr(lab, "source_hashes", lambda: hashes)
    monkeypatch.setattr(lab, "isolated_postgres", isolated_postgres)
    monkeypatch.setattr(
        lab,
        "provision",
        lambda admin_dsn, common: ({"SIGNAL_DATABASE_LAB": "1"}, "17.11"),
    )
    monkeypatch.setattr(lab, "isolated_crawler_network", lambda: nullcontext(crawler))
    monkeypatch.setattr(
        lab,
        "summarize_junit",
        lambda _: {"tests": [{"name": "joint", "status": "PASS"}], "passed": 1},
    )
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lab.subprocess, "run", run)

    assert lab.main() == 0
    assert postgres_options == {"minimum_free": 2 * 1024**3}
    assert len([call for call in calls if "alembic" in call[0]]) == 2
    pytest_call = next(call for call in calls if "pytest" in call[0])
    assert "tests/page_attempt" in pytest_call[0]
    assert pytest_call[1]["env"]["SIGNAL_CRAWLER_NETWORK_NAME"] == crawler.network

    report = json.loads((runtime / "latest.json").read_text())
    assert report["database"] == "17.11"
    assert report["crawler_image_id"] == crawler.image_id
    assert report["network"]["internal"] is True
    assert report["production_authority"] is False
    assert report["cleanup"] == "completed"
