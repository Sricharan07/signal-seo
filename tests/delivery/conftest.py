"""The end-to-end suite accepts only runner-owned primary and journal clusters."""

import os

import pytest

from tests.control_plane.conftest import admin as admin
from tests.control_plane.conftest import api as api
from tests.control_plane.conftest import crawl_admission as crawl_admission
from tests.control_plane.conftest import crawl_ingest as crawl_ingest
from tests.control_plane.conftest import identity as identity
from tests.control_plane.conftest import identity_context as identity_context
from tests.control_plane.conftest import scheduler as scheduler
from tests.control_plane.conftest import scopes as scopes
from tests.control_plane.conftest import workflow as workflow
from tests.delivery.shards import partition_delivery_cases

if os.environ.get("SIGNAL_TEST_WRITE_JOURNAL_DSN") is None:
    raise pytest.UsageError("Use scripts/run-autonomy-delivery-tests.py; journal is required.")


def pytest_collection_modifyitems(config, items):
    raw = os.environ.get("SIGNAL_DELIVERY_TEST_SHARD")
    if raw is None:
        return
    if raw not in {"0", "1"}:
        raise pytest.UsageError("A delivery shard must be 0 or 1.")
    selected, deselected = partition_delivery_cases(items, int(raw))
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
