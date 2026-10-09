"""Verify legacy GSC discovery cannot open an unmediated provider socket."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from signal_core.gsc_properties import GscProtocolError, discover_gsc_properties  # noqa: E402


async def main() -> int:
    try:
        await discover_gsc_properties(
            access_token="signal-gsc-negative-provider-check",
            verified_origin="https://example.invalid",
        )
    except GscProtocolError as error:
        if error.code == "GSC_EGRESS_REQUIRED":
            print("Unmediated GSC discovery is denied; shared egress is required.")
            return 0
        print(f"Google Search Console boundary check failed with {error.code}.")
        return 1
    print("Unmediated GSC discovery unexpectedly opened a provider path.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
