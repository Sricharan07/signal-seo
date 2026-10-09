"""Combined consumer tests run only in the runner-owned provider lab."""

import os

import pytest

if os.environ.get("SIGNAL_CONSUMER_LAB") != "1":
    raise pytest.UsageError(
        "Use scripts/run-consumer-tests.py; external PostgreSQL or Temporal is not accepted."
    )
