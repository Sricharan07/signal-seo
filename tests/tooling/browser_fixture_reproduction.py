"""Opt-in, single-draw multicast entropy injection for the synthetic browser lab."""

import ipaddress


def pytest_collection_modifyitems(items):
    for item in items:
        if item.module.__name__ == "test_browser_worker":
            original = item.module.secrets.randbits
            first = True

            def draw(bits, original=original):
                nonlocal first
                if first:
                    first = False
                    return int(ipaddress.ip_address("224.0.0.0"))
                return original(bits)

            item.module.secrets.randbits = draw
            return
