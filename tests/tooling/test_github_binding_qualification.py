import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from qualify_github_read_binding import (  # noqa: E402
    QualificationConfigurationError,
    _connections,
    _context,
)


def _document():
    return {
        "schema_version": 1,
        "artifact_root": "/tmp/signal-github-qualification-objects",
        "run": {
            "tenant_id": str(uuid4()),
            "site_id": str(uuid4()),
            "command_id": str(uuid4()),
            "run_id": str(uuid4()),
            "root_frontier_id": str(uuid4()),
            "root_url_id": str(uuid4()),
            "first_run_id": str(uuid4()),
            "started_at": datetime.now(UTC).isoformat(),
            "status": "running",
        },
        "policy": {
            "schema_version": 1,
            "allowed_origins": ["https://api.github.com"],
            "user_agent": "SignalBot/1.0 (+https://signal.example/bot)",
            "max_redirects": 0,
            "max_body_bytes": 256 * 1024,
            "request_timeout_seconds": 5,
            "total_timeout_seconds": 15,
        },
    }


def test_live_command_accepts_only_fixed_origin_running_context(tmp_path):
    path = tmp_path / "context.json"
    document = _document()
    path.write_text(json.dumps(document), encoding="utf-8")
    run, policy, root = _context(path)
    assert str(run.site_id) == document["run"]["site_id"]
    assert policy.allowed_origins == ("https://api.github.com",)
    assert root.is_absolute()
    for edit in (
        lambda value: value["policy"].update(allowed_origins=["https://other.invalid"]),
        lambda value: value["policy"].update(max_redirects=1),
        lambda value: value["run"].update(status="completed"),
        lambda value: value.update(secret="not-allowed"),
    ):
        changed = _document()
        edit(changed)
        path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(QualificationConfigurationError, match="CONTEXT_INVALID"):
            _context(path)


def test_live_command_refuses_missing_database_connections(monkeypatch):
    for name in (
        "SIGNAL_GITHUB_IDENTITY_DSN",
        "SIGNAL_GITHUB_EGRESS_ADMISSION_DSN",
        "SIGNAL_GITHUB_EGRESS_INGEST_DSN",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(QualificationConfigurationError, match="DATABASE_UNCONFIGURED"):
        with _connections():
            pytest.fail("Missing runtime roles reached the provider")
