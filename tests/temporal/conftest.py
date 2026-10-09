"""Temporal integration tests run only through the isolated lab runner."""

import os

import pytest

if os.environ.get("SIGNAL_TEMPORAL_LAB") != "1":
    raise pytest.UsageError(
        "Use scripts/run-temporal-tests.py; no external Temporal server is accepted by default."
    )
