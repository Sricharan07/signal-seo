"""Two complementary CI partitions; an unsharded qualification keeps every case."""


def partition_delivery_cases(items, shard):
    if shard is None:
        return list(items), []
    if type(shard) is not int or shard not in (0, 1):
        raise ValueError("A delivery shard must be 0 or 1.")
    ordered = sorted(items, key=lambda item: item.nodeid)
    return (
        [item for index, item in enumerate(ordered) if index % 2 == shard],
        [item for index, item in enumerate(ordered) if index % 2 != shard],
    )
