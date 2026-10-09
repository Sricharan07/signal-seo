import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from uuid import uuid4  # noqa: E402

from qualify_weekly_delivery import REQUIRED, context, main, secret_file  # noqa: E402
from signal_core.database import Scope  # noqa: E402


def test_live_cycle_is_not_executed_without_explicit_owner_configuration(monkeypatch, capsys):
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["qualify_weekly_delivery.py"])
    assert main() == 2
    output = capsys.readouterr().out
    assert '"status": "NOT_EXECUTED"' in output
    assert "password" not in output.lower()


def test_live_context_cannot_be_relative_or_oversized(tmp_path):
    scope = Scope(uuid4(), uuid4())
    with pytest.raises(ValueError, match="CONTEXT_INVALID"):
        context("relative.json", scope, "https://api.github.com", 256 * 1024, 5)
    huge = tmp_path / "huge.json"
    huge.write_bytes(b"x" * 65537)
    with pytest.raises(ValueError, match="CONTEXT_INVALID"):
        context(str(huge), scope, "https://api.github.com", 256 * 1024, 5)


def test_journal_secret_files_are_absolute_regular_and_bounded(tmp_path):
    with pytest.raises(ValueError, match="KEY_FILE_INVALID"):
        secret_file("relative.pem")
    with pytest.raises(ValueError, match="KEY_FILE_INVALID"):
        secret_file(str(tmp_path))
    key = tmp_path / "key"
    key.write_bytes(b"x" * 65)
    with pytest.raises(ValueError, match="KEY_FILE_INVALID"):
        secret_file(str(key), 64)
    key.write_bytes(b"x" * 64)
    assert secret_file(str(key), 64) == b"x" * 64
