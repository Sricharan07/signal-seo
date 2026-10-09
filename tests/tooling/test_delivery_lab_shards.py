"""CI partitions preserve the full test set and the original timeout boundary."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.delivery.shards import partition_delivery_cases  # noqa: E402


def test_ci_shards_are_complete_disjoint_and_stable():
    items = [SimpleNamespace(nodeid=name) for name in ("d", "b", "a", "c")]
    first, first_other = partition_delivery_cases(items, 0)
    second, second_other = partition_delivery_cases(items, 1)
    assert first == second_other and second == first_other
    assert sorted(item.nodeid for item in first + second) == ["a", "b", "c", "d"]
    assert partition_delivery_cases(list(reversed(items)), 0)[0] == first


def test_unsharded_lab_keeps_every_case():
    items = [SimpleNamespace(nodeid="all-cases")]
    assert partition_delivery_cases(items, None) == (items, [])


@pytest.mark.parametrize("shard", [-1, 2, "0", True])
def test_other_shard_values_are_rejected(shard):
    with pytest.raises(ValueError, match="must be 0 or 1"):
        partition_delivery_cases([], shard)
