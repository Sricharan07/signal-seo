"""JSON object-pairs hook shared by boundaries that already reject duplicate keys."""


def unique_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate JSON object key.")
    return value
