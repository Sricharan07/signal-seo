"""Stable complementary partitions, independent of collection order and hash seed."""


def partition_cases(items, shard: int, count: int):
    if type(count) is not int or not 1 <= count <= 6:
        raise ValueError("Shard count must be between 1 and 6.")
    if type(shard) is not int or not 0 <= shard < count:
        raise ValueError("Shard index must be within the configured count.")
    ordered = sorted(items, key=lambda item: item.nodeid)
    return (
        [item for index, item in enumerate(ordered) if index % count == shard],
        [item for index, item in enumerate(ordered) if index % count != shard],
    )
