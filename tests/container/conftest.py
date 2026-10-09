"""Candidate lab containers carry an invocation label without changing the sandbox."""

import os
from uuid import UUID

import pytest
from signal_core import candidate_sandbox


@pytest.fixture(autouse=True)
def candidate_lab_ownership(monkeypatch):
    if os.environ.get("SIGNAL_CANDIDATE_SANDBOX_LAB") != "1":
        return
    try:
        run_id = UUID(os.environ["SIGNAL_CANDIDATE_SANDBOX_RUN_ID"]).hex
    except (KeyError, ValueError):
        raise pytest.UsageError("Use the invocation-owned candidate sandbox lab runner.") from None
    original = candidate_sandbox._create_args

    def create_args(*args, **kwargs):
        command = list(original(*args, **kwargs))
        image_index = command.index(candidate_sandbox.NODE_IMAGE)
        command[image_index:image_index] = ["--label", f"dev.signal.candidate-lab={run_id}"]
        return tuple(command)

    monkeypatch.setattr(candidate_sandbox, "_create_args", create_args)
