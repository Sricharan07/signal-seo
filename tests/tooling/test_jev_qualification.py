import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from qualify_jev_provider import (  # noqa: E402
    QualificationConfigurationError,
    StdinCredential,
    _policy,
    _read_context,
    _run,
    configured_egress,
)


def context() -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact_root": "/tmp/signal-jev-qualification-objects",
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
            "allowed_origins": ["https://api.typesafe.ai"],
            "user_agent": "SignalBot/1.0 (+https://signal.example/bot)",
            "max_redirects": 0,
            "max_body_bytes": 128 * 1024,
            "request_timeout_seconds": 20,
            "total_timeout_seconds": 20,
        },
    }


def test_live_context_is_strict_fixed_origin_and_secret_free(tmp_path) -> None:
    document = context()
    path = tmp_path / "context.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    loaded = _read_context(path)
    run = _run(loaded["run"])
    policy = _policy(loaded["policy"])

    assert run.status == "running"
    assert policy.allowed_origins == ("https://api.typesafe.ai",)
    credential = StdinCredential("typesafe-test-key-0000000000000000")
    assert "typesafe-test-key" not in repr(credential)
    assert "typesafe-test-key" not in path.read_text(encoding="utf-8")


def test_live_context_rejects_extra_fields_and_changed_origin(tmp_path) -> None:
    document = context()
    document["unexpected"] = True
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(QualificationConfigurationError, match="CONTEXT_INVALID"):
        _read_context(path)

    changed = context()["policy"]
    changed["allowed_origins"] = ["https://other.example"]
    with pytest.raises(QualificationConfigurationError, match="CONTEXT_INVALID"):
        _policy(changed)


@pytest.mark.parametrize("artifact_root", ["", "relative/path", "x" * 4097, None])
def test_live_context_rejects_invalid_artifact_root(tmp_path, artifact_root) -> None:
    document = context()
    document["artifact_root"] = artifact_root
    path = tmp_path / "invalid-root.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(QualificationConfigurationError, match="CONTEXT_INVALID"):
        _read_context(path)


def test_live_qualification_requires_shared_egress_configuration(monkeypatch) -> None:
    for name in (
        "SIGNAL_JEV_EGRESS_CONTEXT",
        "SIGNAL_JEV_EGRESS_ADMISSION_DSN",
        "SIGNAL_JEV_EGRESS_INGEST_DSN",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(QualificationConfigurationError, match="EGRESS_UNCONFIGURED"):
        with configured_egress():
            raise AssertionError("unconfigured shared egress was entered")
