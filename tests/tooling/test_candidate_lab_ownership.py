from uuid import uuid4

import pytest
from signal_core import candidate_sandbox

from tests.container.conftest import candidate_lab_ownership


def test_candidate_lab_labels_only_this_run_without_changing_isolation(monkeypatch):
    before = candidate_sandbox._create_args("synthetic-container")
    run_id = uuid4().hex
    monkeypatch.setenv("SIGNAL_CANDIDATE_SANDBOX_LAB", "1")
    monkeypatch.setenv("SIGNAL_CANDIDATE_SANDBOX_RUN_ID", run_id)
    candidate_lab_ownership.__wrapped__(monkeypatch)
    after = candidate_sandbox._create_args("synthetic-container")
    index = after.index("dev.signal.candidate-lab=" + run_id) - 1
    assert after[index] == "--label"
    assert after[:index] + after[index + 2 :] == before
    assert after.index(candidate_sandbox.NODE_IMAGE) > index


def test_candidate_lab_missing_owner_is_unavailable(monkeypatch):
    monkeypatch.setenv("SIGNAL_CANDIDATE_SANDBOX_LAB", "1")
    monkeypatch.delenv("SIGNAL_CANDIDATE_SANDBOX_RUN_ID", raising=False)
    with pytest.raises(pytest.UsageError, match="invocation-owned"):
        candidate_lab_ownership.__wrapped__(monkeypatch)


def test_nonlab_candidate_sandbox_is_unchanged(monkeypatch):
    monkeypatch.delenv("SIGNAL_CANDIDATE_SANDBOX_LAB", raising=False)
    before = candidate_sandbox._create_args
    candidate_lab_ownership.__wrapped__(monkeypatch)
    assert candidate_sandbox._create_args is before
