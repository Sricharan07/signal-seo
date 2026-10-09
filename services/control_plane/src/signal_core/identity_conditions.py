"""Shared, deliberately narrow identity-condition normalization."""

import re
from email.headerregistry import Address

_LOCAL_PART = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}")
_DOMAIN_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def normalize_ascii_mailbox(value: object) -> str:
    """Return a lower-case mailbox accepted by the pilot identity contract."""
    if (
        not isinstance(value, str)
        or not value.isascii()
        or value != value.strip()
        or not 3 <= len(value) <= 320
    ):
        raise ValueError("Identity email must be a bounded ASCII mailbox.")
    try:
        address = Address(addr_spec=value)
    except (TypeError, ValueError):
        raise ValueError("Identity email must be a bounded ASCII mailbox.") from None
    local = address.username
    domain = address.domain
    labels = domain.split(".")
    if (
        _LOCAL_PART.fullmatch(local) is None
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or len(labels) < 2
        or any(_DOMAIN_LABEL.fullmatch(label) is None for label in labels)
        or len(domain) > 253
    ):
        raise ValueError("Identity email must be a bounded ASCII mailbox.")
    return f"{local.lower()}@{domain.lower()}"
